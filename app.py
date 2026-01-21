import streamlit as st
import json
import os
import re
from datetime import datetime, timedelta
from PIL import Image, ImageFilter, ImageOps
from fpdf import FPDF
from google import genai
from collections import defaultdict
import io
from typing import List, Dict, Any, Optional

# Optional OCR (pytesseract). If unavailable, code will fall back to AI.
try:
    import pytesseract
    TESSERACT_AVAILABLE = True
except Exception:
    TESSERACT_AVAILABLE = False

# 1. 初期設定
st.set_page_config(page_title="配送管理システム", layout="centered")

# Secretsから取得（Streamlit Cloud等で設定）
COMPANY_NAME = st.secrets.get("COMPANY_NAME", "(株)アイプラス")
API_KEY = st.secrets.get("GEMINI_API_KEY", "")

client = genai.Client(api_key=API_KEY)

def safe_int(v: Any) -> int:
    """数値文字列や None を安全に int に変換"""
    if v is None:
        return 0
    if isinstance(v, int):
        return v
    try:
        # Keep digits and minus only
        s = re.sub(r'[^-]', '', str(v))
        return int(s) if s else 0
    except Exception:
        return 0

# 品目ごとの箱あたり個数（ルール）
UNIT_RULES = {
    "胡瓜(3本P)": 30,
    "胡瓜(バラ)": 100,
    "春菊": 30,
    "青梗菜": 20,
    "長ネギ(2本P)": 30
}

def normalize_item_name(raw: str) -> str:
    """品目名のゆらぎを正規化して、既知のキーにマップする"""
    if not raw:
        return raw
    s = str(raw)
    s = s.replace(" ", "").strip()
    # 青梗菜 のゆらぎ
    if re.search(r'青梗菜|チンゲン菜|ちんげん菜', s):
        return "青梗菜"
    # きゅうり関連（漢字・ひらがな・カナ）
    if re.search(r'胡瓜|きゅうり|キュウリ', s):
        # バラ（ばら）判定
        if re.search(r'バラ|ばら', s):
            return "胡瓜(バラ)"
        # 3本パック判定
        if re.search(r'3本|3本P|3本パック', s):
            return "胡瓜(3本P)"
        # default: バラ扱い
        return "胡瓜(バラ)"
    # 長ネギ
    if re.search(r'長ネギ|長ねぎ|ねぎ', s) and re.search(r'2本', s):
        return "長ネギ(2本P)"
    if re.search(r'春菊', s):
        return "春菊"
    # デフォルトは元の文字列（ただし全角半角トリム）
    return s

def compute_boxes_and_remainder(total_qty: int, item_name: str) -> Dict[str, int]:
    """
    総個数(total_qty) から unit/boxes/remainder を算出するフォールバックロジック。
    胡瓜(バラ) の場合は 100/箱 が基本だが、端数 >=50 のとき 50本箱 を考慮する。
    戻り値: {'unit': ..., 'boxes': ..., 'remainder': ..., 'fifty_box': 0 or 1}
    """
    unit = UNIT_RULES.get(item_name)
    result = {"unit": 0, "boxes": 0, "remainder": 0, "fifty_box": 0}
    if unit is None:
        # 既知ルールがなければ全部端数として扱う
        result["unit"] = 0
        result["boxes"] = 0
        result["remainder"] = total_qty
        return result

    result["unit"] = unit
    if unit <= 0:
        result["boxes"] = 0
        result["remainder"] = total_qty
        return result

    boxes = total_qty // unit
    remainder = total_qty % unit

    # 胡瓜(バラ) の特殊処理: 端数が 50 以上なら 50本箱を1つ使う（fifty_box=1）し、残りを remainder に残す
    if item_name == "胡瓜(バラ)":
        if remainder >= 50:
            result["fifty_box"] = 1
            remainder = remainder - 50
            remainder = max(remainder, 0)
    result["boxes"] = boxes
    result["remainder"] = remainder
    return result

def postprocess_ai_results(raw_entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    AI が返した JSON を受け取り、欠損フィールドの補完・正規化を行う。
    - item の正規化
    - unit/boxes/remainder が欠けている場合は total や count から算出する
    - fifty_box の情報を付与する場合あり
    """
    processed = []
    for e in raw_entries:
        entry = {k: v for k, v in e.items()}
        entry['item'] = normalize_item_name(entry.get('item', '') or entry.get('品目', '') or '')
        entry['store'] = entry.get('store', '') or entry.get('店舗', '')
        entry['spec'] = entry.get('spec', '') or entry.get('規格', '')

        u = safe_int(entry.get('unit', None))
        b = safe_int(entry.get('boxes', None))
        r = safe_int(entry.get('remainder', None))

        total_candidate = None
        if 'total' in entry:
            total_candidate = safe_int(entry.get('total'))
        elif 'count' in entry:
            total_candidate = safe_int(entry.get('count'))
        elif '数量' in entry:
            total_candidate = safe_int(entry.get('数量'))

        if (u == 0 or b == 0) and total_candidate is not None:
            comp = compute_boxes_and_remainder(total_candidate, entry['item'])
            entry['unit'] = comp['unit']
            entry['boxes'] = comp['boxes']
            entry['remainder'] = comp['remainder']
            if comp.get('fifty_box'):
                entry['fifty_box'] = comp['fifty_box']
        else:
            entry['unit'] = u
            entry['boxes'] = b
            entry['remainder'] = r

            if entry['unit'] == 0 and total_candidate is not None:
                comp = compute_boxes_and_remainder(total_candidate, entry['item'])
                entry['unit'] = comp['unit']
                entry['boxes'] = comp['boxes']
                entry['remainder'] = comp['remainder']
                if comp.get('fifty_box'):
                    entry['fifty_box'] = comp['fifty_box']

        processed.append(entry)
    return processed

# OCR-based parsing for email screenshots / text-heavy images
_ITEM_KEYWORDS = ['胡瓜', 'きゅうり', 'キュウリ', '春菊', '青梗菜', 'チンゲン菜', 'ちんげん菜', 'バラ', 'ネギ', 'ねぎ']

def _line_contains_item(line: str) -> bool:
    return any(k in line for k in _ITEM_KEYWORDS)

def ocr_parse_image(img: Image.Image) -> List[Dict[str, Any]]:
    """画像から OCR して、メール形式の発注をパースしてエントリ一覧を返す。
    戻り値の各エントリは {'store':..., 'item':..., 'total':...} の形を想定。
    空リストは OCR で意味あるデータが取れなかったことを示す。
    """
    if not TESSERACT_AVAILABLE:
        return []

    try:
        # 前処理: グレースケール化・コントラスト調整
        img_cv = img.convert('L')
        img_cv = ImageOps.invert(img_cv)
        img_cv = img_cv.point(lambda x: 0 if x < 128 else 255, '1')
        text = pytesseract.image_to_string(img_cv, lang='jpn')
    except Exception:
        try:
            text = pytesseract.image_to_string(img, lang='jpn')
        except Exception:
            return []

    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if not lines:
        return []

    entries: List[Dict[str, Any]] = []
    current_store = None

    # パターン1: item + unit × count (例: 胡瓜バラ50×4 -> unit=50 count=4)
    p_unit_mul = re.compile(r'(?P<item>[^×xX✕]+?)(?P<unit>\d+)[×xX✕](?P<count>\d+)$')
    # パターン2: item × number (例: 春菊×20, 胡瓜3本×120)
    p_item_num = re.compile(r'(?P<item>[^×xX✕]+?)[×xX✕](?P<number>\d+)$')

    for line in lines:
        # 行に店舗名と項目が同居する場合 (例: 青葉台 胡瓜バラ50×4)
        m_combined = re.match(r'^(?P<store>\S{1,10})\s+(?P<rest>.+)$', line)
        if m_combined and _line_contains_item(m_combined.group('rest')):
            current_store = m_combined.group('store')
            rest = m_combined.group('rest')
            # parse rest as item line(s)
            candidate_lines = [rest]
        else:
            # 店舗名だけの行か、項目の行
            if not _line_contains_item(line):
                # 店舗名の可能性が高い
                current_store = line
                continue
            candidate_lines = [line]

        for cl in candidate_lines:
            cl = cl.replace(' ', '')
            # try unit*count pattern
            m1 = p_unit_mul.search(cl)
            if m1:
                item_raw = m1.group('item')
                unit = safe_int(m1.group('unit'))
                count = safe_int(m1.group('count'))
                total = unit * count
                item_name = normalize_item_name(item_raw)
                entries.append({'store': current_store or '', 'item': item_name, 'total': total})
                continue
            m2 = p_item_num.search(cl)
            if m2:
                item_raw = m2.group('item')
                number = safe_int(m2.group('number'))
                item_name = normalize_item_name(item_raw)
                # 仮に item_raw 内に数字（例: 3本）が含まれていれば、number は "個数(パック数)" と扱う
                if re.search(r'\d+本', item_raw):
                    total = number
                else:
                    # 春菊×20 等は total=number
                    total = number
                entries.append({'store': current_store or '', 'item': item_name, 'total': total})
                continue
            # 最後の手段: 行中の数字を拾って total とする
            nums = re.findall(r'\d+', cl)
            if nums:
                total = safe_int(nums[-1])
                item_name = normalize_item_name(re.sub(r'\d+', '', cl))
                entries.append({'store': current_store or '', 'item': item_name, 'total': total})

    return entries

# 2. AI画像解析（Gemini 2.0 Flash）
def get_order_data(image: Image.Image) -> Optional[List[Dict[str, Any]]]:
    """
    画像をGeminiに送り、JSONで注文情報を受け取る。
    返り値は list of dict を期待。AIからの応答が不正なら None を返す。
    """
    prompt = """画像を解析し、以下の計算ルールを適用してJSONで返してください。
【重要】
1. unit, boxes, remainderには「数字のみ」を入れてください（可能なら箱数と端数を入れてください）。
2. 「青梗菜」は「チンゲン菜」「ちんげん菜」と表記されている場合もあります。これらをすべて「青梗菜」として統一してください。
3. 品目名の揺らぎ（例：胡瓜、きゅうり、キュウリ、胡瓜(バラ)、胡瓜(3本P) 等）は可能ならそのまま出力してください。出力が得られない場合は後処理で正規化します。

【ルール】
胡瓜(3本P):30/箱, 胡瓜(バラ):100/箱（端数が50以上なら50本箱を使用可能）, 春菊:30/箱, 青梗菜:20/箱, 長ネギ(2本P):30/箱

【出力JSON例】
[
  {"store":"店舗名","item":"品目名","spec":"規格","unit":30,"boxes":5,"remainder":0}
]
"""
    try:
        response = client.models.generate_content(model="gemini-2.0-flash", contents=[prompt, image])
        text = getattr(response, 'text', '') or ''
        # AI が ```json で返す場合に対応
        if '```json' in text:
            try:
                text = text.split('```json', 1)[1].split('```', 1)[0]
            except Exception:
                pass
        # 最終的に JSON パース
        parsed = json.loads(text.strip())
        if isinstance(parsed, list):
            return parsed
        if isinstance(parsed, dict):
            return [parsed]
        st.error("AIの応答が期待形式(list/dict)ではありません。")
        return None
    except Exception as e:
        st.error(f"解析エラー: {e}")
        return None

# 3. PDF作成（B5サイズ：一覧表 ＋ 伝票）
def create_b5_pdf(data: List[Dict[str, Any]]) -> bytes:
    """
    data: list of entries, each entry should contain at least
    store, item, spec, unit, boxes, remainder, optional fifty_box
    戻り値: PDF の bytes
    """
    pdf = FPDF(orientation='P', unit='mm', format=(182, 257))

    if os.path.exists('ipaexg.ttf'):
        try:
            pdf.add_font('Gothic', fname='ipaexg.ttf', uni=True)
            pdf.add_font('Gothic', style='B', fname='ipaexg.ttf', uni=True)
            font_name = 'Gothic'
        except Exception:
            font_name = 'Arial'
    else:
        font_name = 'Arial'

    tomorrow = datetime.now() + timedelta(days=1)
    tomorrow_pdf_str = tomorrow.strftime('%m 月 %d 日')
    tomorrow_list_str = tomorrow.strftime('%m/%d')

    pdf.add_page()
    pdf.set_font(font_name, style='B', size=20)
    pdf.cell(0, 15, f"【出荷一覧表】 {tomorrow_list_str}", ln=True, align='C')

    pdf.set_font(font_name, style='B', size=12)
    pdf.set_fill_color(230, 230, 230)
    pdf.cell(55, 12, " 店舗名", border=1, fill=True)
    pdf.cell(55, 12, " 品目", border=1, fill=True)
    pdf.cell(25, 12, " フル箱", border=1, fill=True, align='C')
    pdf.cell(25, 12, " 端数箱", border=1, fill=True, align='C', ln=True)

    pdf.set_font(font_name, style='', size=12)
    for entry in data:
        r_val = safe_int(entry.get('remainder', 0))
        rem_box = 1 if r_val > 0 or entry.get('fifty_box', 0) else 0
        pdf.cell(55, 12, f" {entry.get('store','')}", border=1)
        pdf.cell(55, 12, f" {entry.get('item','')}", border=1)
        pdf.cell(25, 12, f" {safe_int(entry.get('boxes',0))}", border=1, align='C')
        pdf.cell(25, 12, f" {rem_box}", border=1, align='C', ln=True)

    for entry in data:
        pdf.add_page()
        pdf.set_auto_page_break(auto=False)
        pdf.set_line_width(0.2)

        pdf.set_font(font_name, style='B', size=26)
        pdf.cell(0, 25, f"{COMPANY_NAME} (千葉県産)", align='C', ln=True)
        pdf.ln(2)

        col1, col2, h = 45, 122, 30

        pdf.set_font(font_name, style='B', size=18); pdf.cell(col1, h, " 行先", border=1)
        pdf.set_font(font_name, style='B', size=36); pdf.cell(col2, h, f" {entry.get('store','')}", border=1, ln=True)

        pdf.set_font(font_name, style='B', size=18); pdf.cell(col1, h, " 商品名", border=1)
        pdf.set_font(font_name, style='B', size=32); pdf.cell(col2, h, f" {entry.get('item','')}", border=1, ln=True)

        pdf.set_font(font_name, style='B', size=18); pdf.cell(col1, h, " 出荷日", border=1)
        pdf.set_font(font_name, style='B', size=26); pdf.cell(col2, h, f" {tomorrow_pdf_str}", border=1, ln=True)

        pdf.set_font(font_name, style='B', size=18); pdf.cell(col1, h, " 規格", border=1)
        pdf.set_font(font_name, style='B', size=26); pdf.cell(col2, h, f" {entry.get('spec', '')}", border=1, ln=True)

        u_val = safe_int(entry.get('unit',0))
        b_val = safe_int(entry.get('boxes',0))
        r_val = safe_int(entry.get('remainder',0))

        pdf.set_font(font_name, style='B', size=18); pdf.cell(col1, h, " 入数", border=1)
        pdf.set_font(font_name, style='B', size=24); pdf.cell(col2/2, h, f" {u_val if u_val>0 else ''}", border=1)
        pdf.cell(col2/2, h, f" {b_val} ケース", border=1, ln=True)

        pdf.set_font(font_name, style='B', size=18); pdf.cell(col1, h, " 端数", border=1)
        pdf.set_font(font_name, style='B', size=24)
        if entry.get('fifty_box', 0):
            display_rem = f"{r_val if r_val>0 else ''}"
            pdf.cell(col2/2, h, f" {display_rem}", border=1)
            pdf.cell(col2/2, h, f" 50本箱1", border=1, ln=True)
        else:
            pdf.cell(col2/2, h, f" {r_val if r_val > 0 else ''}", border=1)
            rem_box = 1 if r_val > 0 else 0
            pdf.cell(col2/2, h, f" {rem_box} ケース", border=1, ln=True)

        pdf.set_font(font_name, style='B', size=20); pdf.cell(col1, h, " TOTAL 数", border=1)
        pdf.set_font(font_name, style='B', size=42)
        total_qty = (u_val * b_val) + r_val + (50 if entry.get('fifty_box', 0) else 0)
        pdf.cell(col2, h, f" {total_qty}", border=1, ln=True)

    pdf_bytes = pdf.output(dest='S').encode('latin-1')
    return pdf_bytes

# 4. メイン画面レイアウト
st.title("📦 配送伝票作成システム (改良版 + OCR)")
uploaded_files = st.file_uploader("注文画像をアップロード（複数可）", type=['png', 'jpg', 'jpeg'], accept_multiple_files=True)

if uploaded_files:
    st.subheader("アップロード画像")
    for idx, f in enumerate(uploaded_files):
        try:
            img = Image.open(f)
            st.image(img, caption=f"画像 {idx+1}: {getattr(f, 'name', '')}", use_container_width=True)
        except Exception as ex:
            st.warning(f"画像 {idx+1} の読み込みに失敗しました: {ex}")

    if st.button("配送伝票を生成"):
        all_raw = []
        with st.spinner('画像を解析中...（OCR→AI）'):
            for f in uploaded_files:
                try:
                    img = Image.open(f)
                except Exception as ex:
                    st.error(f"画像読み込み失敗: {ex}")
                    continue

                # 1) まずOCRでメール形式の発注をパース
                ocr_entries = ocr_parse_image(img)
                if ocr_entries:
                    # Convert OCR entries to the same shape expected by postprocess
                    for oe in ocr_entries:
                        all_raw.append({'store': oe.get('store',''), 'item': oe.get('item',''), 'total': oe.get('total',0)})
                    continue

                # 2) OCRで取れなければAIにフォールバック
                ai_res = get_order_data(img)
                if ai_res:
                    all_raw.extend(ai_res)
                else:
                    st.warning(f"画像 {getattr(f, 'name', '')} の解析でデータが得られませんでした。")

            if not all_raw:
                st.error("どの画像からも注文データを取得できませんでした。")
            else:
                processed = postprocess_ai_results(all_raw)

                pdf_bytes = create_b5_pdf(processed)
                st.success("伝票が完成しました！")

                st.subheader("📋 LINE用集計（コピー用）")
                summary_packs = defaultdict(int)
                for entry in processed:
                    total = (safe_int(entry.get('unit',0)) * safe_int(entry.get('boxes',0))) + safe_int(entry.get('remainder',0)) + (50 if entry.get('fifty_box',0) else 0)
                    summary_packs[entry.get('item','不明')] += total

                line_text = f"【{datetime.now().strftime('%m/%d')} 出荷・作成総数】\n"
                for item, total in summary_packs.items():
                    unit_label = "袋" if any(x in item for x in ["春菊", "青梗菜"]) else "パック"
                    line_text += f"・{item}：{total}{unit_label}\n"

                st.code(line_text, language="text")
                st.write("↑ タップしてコピーし、LINEに貼り付けてください。")

                st.download_button(
                    label="📥 PDFをダウンロード (一覧表付き)",
                    data=pdf_bytes,
                    file_name=f"label_{datetime.now().strftime('%m%d%H%M')}.pdf",
                    mime="application/pdf"
                )
