import streamlit as st
import json
import os
import re
import hashlib
import time
import random
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
    from pytesseract import Output
    TESSERACT_AVAILABLE = True
except Exception:
    TESSERACT_AVAILABLE = False

# 1. 初期設定
st.set_page_config(page_title="配送管理システム", layout="centered")

# Secretsから取得（Streamlit Cloud等で設定）
COMPANY_NAME = st.secrets.get("COMPANY_NAME", "(株)アイプラス")
API_KEY = st.secrets.get("GEMINI_API_KEY", "")

client = genai.Client(api_key=API_KEY)

# キャッシュファイル
CACHE_PATH = '.cache/orders_cache.json'

# OCR 信頼度閾値（％）
OCR_CONFIDENCE_THRESHOLD = 60

# 最大キャッシュエントリ数（運用に応じて調整）
MAX_CACHE_ENTRIES = 2000

# Utility helpers

def safe_int(v: Any) -> int:
    if v is None:
        return 0
    if isinstance(v, int):
        return v
    try:
        s = re.sub(r'[^0-9\-]', '', str(v))
        return int(s) if s else 0
    except Exception:
        return 0

UNIT_RULES = {
    "胡瓜(3本P)": 30,
    "胡瓜(バラ)": 100,
    "春菊": 30,
    "青梗菜": 20,
    "長ネギ(2本P)": 30
}

def normalize_item_name(raw: str) -> str:
    if not raw:
        return raw
    s = str(raw)
    s = s.replace(" ", "").strip()
    if re.search(r'青梗菜|チンゲン菜|ちんげん菜', s):
        return "青梗菜"
    if re.search(r'胡瓜|きゅうり|キュウリ', s):
        if re.search(r'バラ|ばら', s):
            return "胡瓜(バラ)"
        if re.search(r'3本|3本P|3本パック', s):
            return "胡瓜(3本P)"
        return "胡瓜(バラ)"
    if re.search(r'長ネギ|長ねぎ|ねぎ', s) and re.search(r'2本', s):
        return "長ネギ(2本P)"
    if re.search(r'春菊', s):
        return "春菊"
    return s

def compute_boxes_and_remainder(total_qty: int, item_name: str) -> Dict[str, int]:
    unit = UNIT_RULES.get(item_name)
    result = {"unit": 0, "boxes": 0, "remainder": 0, "fifty_box": 0}
    if unit is None:
        result["remainder"] = total_qty
        return result
    result["unit"] = unit
    boxes = total_qty // unit
    remainder = total_qty % unit
    if item_name == "胡瓜(バラ)" and remainder >= 50:
        result["fifty_box"] = 1
        remainder = max(remainder - 50, 0)
    result["boxes"] = boxes
    result["remainder"] = remainder
    return result

# Persistent cache functions

def _load_cache() -> Dict[str, Any]:
    try:
        if os.path.exists(CACHE_PATH):
            with open(CACHE_PATH, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception:
        pass
    return {}

def _save_cache(cache: Dict[str, Any]):
    try:
        os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
        # Keep cache size bounded
        if len(cache) > MAX_CACHE_ENTRIES:
            # remove oldest keys (not strictly LRU; simple approach)
            keys = list(cache.keys())[-MAX_CACHE_ENTRIES:]
            cache = {k: cache[k] for k in keys}
        with open(CACHE_PATH, 'w', encoding='utf-8') as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

# Simple image hashing

def image_hash(img: Image.Image) -> str:
    try:
        bio = io.BytesIO()
        img.save(bio, format='PNG')
        h = hashlib.sha256(bio.getvalue()).hexdigest()
        return h
    except Exception:
        return str(time.time())

# OCR parsing with confidence

_ITEM_KEYWORDS = ['胡瓜', 'きゅうり', 'キュウリ', '春菊', '青梗菜', 'チンゲン菜', 'ちんげん菜', 'バラ', 'ネギ', 'ねぎ']

def _line_contains_item(line: str) -> bool:
    return any(k in line for k in _ITEM_KEYWORDS)

def ocr_parse_image_with_confidence(img: Image.Image) -> (List[Dict[str, Any]], float):
    """Return list of parsed entries and average confidence (0-100).
    Each entry is {'store':..., 'item':..., 'total':...}
    """
    if not TESSERACT_AVAILABLE:
        return [], 0.0
    try:
        # Preprocess: convert to grayscale, increase contrast, binarize
        proc = img.convert('L')
        proc = ImageOps.autocontrast(proc)
        data = pytesseract.image_to_data(proc, lang='jpn', output_type=Output.DICT)
        n = len(data.get('text', []))
        confidences = []
        lines = {}
        for i, txt in enumerate(data.get('text', [])):
            t = txt.strip()
            if not t:
                continue
            conf = safe_int(data.get('conf', [])[i]) if i < len(data.get('conf', [])) else 0
            confidences.append(conf)
            block_num = (data.get('block_num', [])[i] if i < len(data.get('block_num', [])) else 0)
            par_num = (data.get('par_num', [])[i] if i < len(data.get('par_num', [])) else 0)
            line_key = f"{block_num}-{par_num}"
            lines.setdefault(line_key, []).append(t)
        avg_conf = float(sum(confidences) / len(confidences)) if confidences else 0.0
        # build lines text
        line_texts = [''.join(parts) for parts in lines.values()]
        entries: List[Dict[str, Any]] = []
        # parse lines heuristics
        p_unit_mul = re.compile(r'(?P<item>[^0-9×xX✕]+?)(?P<unit>\d+)\s*[×xX✕]\s*(?P<count>\d+)$')
        p_item_num = re.compile(r'(?P<item>[^×xX✕]+?)\s*[×xX✕]\s*(?P<number>\d+)$')
        for line in line_texts:
            line = line.strip()
            if not line:
                continue
            if not _line_contains_item(line):
                # maybe a store name
                # skip unless it contains both store and item
                pass
            # remove spaces
            cl = line.replace(' ', '')
            m1 = p_unit_mul.search(cl)
            if m1:
                item_raw = m1.group('item')
                unit = safe_int(m1.group('unit'))
                count = safe_int(m1.group('count'))
                total = unit * count
                item_name = normalize_item_name(item_raw)
                entries.append({'store': '', 'item': item_name, 'total': total})
                continue
            m2 = p_item_num.search(cl)
            if m2:
                item_raw = m2.group('item')
                number = safe_int(m2.group('number'))
                item_name = normalize_item_name(item_raw)
                total = number
                entries.append({'store': '', 'item': item_name, 'total': total})
                continue
            # fallback: pick last number as total
            nums = re.findall(r'\d+', cl)
            if nums:
                total = safe_int(nums[-1])
                item_name = normalize_item_name(re.sub(r'\d+', '', cl))
                entries.append({'store': '', 'item': item_name, 'total': total})
        return entries, avg_conf
    except Exception:
        return [], 0.0

# --------------- AI call helpers (with retries) ----------------

def _is_resource_exhausted_exc(exc: Exception) -> bool:
    msg = str(exc).lower()
    return 'resource_exhausted' in msg or 'resource exhausted' in msg or '429' in msg or 'rate limit' in msg

def generate_with_retries(model: str, contents: list, max_retries: int = 4, base_delay: float = 1.0):
    attempt = 0
    while True:
        try:
            return client.models.generate_content(model=model, contents=contents)
        except Exception as e:
            attempt += 1
            if _is_resource_exhausted_exc(e) and attempt <= max_retries:
                delay = base_delay * (2 ** (attempt - 1))
                jitter = random.uniform(0, delay * 0.3)
                sleep_for = delay + jitter
                try:
                    st.warning(f"API リソース不足のため再試行します（{attempt}/{max_retries}）。{int(sleep_for)} 秒待機します。")
                except Exception:
                    pass
                time.sleep(sleep_for)
                continue
            raise

# Existing get_order_data now will be used as a per-image AI fallback if OCR is insufficient

def get_order_data_from_ai(image: Image.Image) -> Optional[List[Dict[str, Any]]]:
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
        response = generate_with_retries(model="gemini-2.0-flash", contents=[prompt, image], max_retries=4, base_delay=1.0)
        text = getattr(response, 'text', '') or ''
        if '```json' in text:
            try:
                text = text.split('```json', 1)[1].split('```', 1)[0]
            except Exception:
                pass
        parsed = json.loads(text.strip())
        if isinstance(parsed, list):
            return parsed
        if isinstance(parsed, dict):
            return [parsed]
        return None
    except Exception as e:
        if _is_resource_exhausted_exc(e):
            try:
                os.makedirs('pending', exist_ok=True)
                fname = f"pending/img_{int(time.time())}.png"
                image.save(fname)
                st.info(f"保留画像を保存しました: {fname}")
            except Exception:
                pass
            return None
        st.error(f"解析エラー: {e}")
        return None

# PDF creator with safe bytes handling

def create_b5_pdf(data: List[Dict[str, Any]]) -> bytes:
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

    pdf_data = pdf.output(dest='S')
    if isinstance(pdf_data, bytes):
        pdf_bytes = pdf_data
    else:
        pdf_bytes = pdf_data.encode('latin-1')
    return pdf_bytes

# 4. メイン画面レイアウト
st.title("📦 配送伝票作成システム (節約モード対応)")

# UI: 簡易節約モードトグルと AI 呼び出しカウンタ
save_mode = st.checkbox('節約モード（OCR優先・AI呼び出し抑制）', value=True)
if 'ai_call_count' not in st.session_state:
    st.session_state['ai_call_count'] = 0

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
        cache = _load_cache()
        all_raw = []
        ai_needed_count = 0
        with st.spinner('画像を解析中...（OCR優先）'):
            for f in uploaded_files:
                try:
                    img = Image.open(f)
                except Exception as ex:
                    st.error(f"画像読み込み失敗: {ex}")
                    continue
                h = image_hash(img)
                # キャッシュヒット
                if h in cache:
                    st.info(f"キャッシュから読み込み: {getattr(f, 'name', '')}")
                    all_raw.extend(cache[h])
                    continue
                # OCR 解析
                entries, avg_conf = ocr_parse_image_with_confidence(img)
                st.info(f"OCR平均信頼度: {int(avg_conf)}% - {getattr(f, 'name', '')}")
                if entries and avg_conf >= OCR_CONFIDENCE_THRESHOLD:
                    # 信頼できるのでキャッシュに保存
                    cache[h] = entries
                    all_raw.extend(entries)
                    continue
                # 節約モードが有効なら OCR のみで保留（AI 使わない）
                if save_mode:
                    st.warning(f"{getattr(f, 'name', '')} は OCR の信頼度が低いため保留しました（節約モード）。")
                    try:
                        os.makedirs('pending', exist_ok=True)
                        fname = f"pending/{h}.png"
                        img.save(fname)
                        st.info(f"保留画像を保存しました: {fname}")
                    except Exception:
                        pass
                    continue
                # OCR 不十分で節約モードオフ -> AI フォールバック
                ai_needed_count += 1
                ai_res = get_order_data_from_ai(img)
                st.session_state['ai_call_count'] += 1
                if ai_res:
                    cache[h] = ai_res
                    all_raw.extend(ai_res)
                else:
                    st.warning(f"画像 {getattr(f, 'name', '')} の解析でデータが得られませんでした。保留にします。")
                    try:
                        os.makedirs('pending', exist_ok=True)
                        fname = f"pending/{h}.png"
                        img.save(fname)
                        st.info(f"保留画像を保存しました: {fname}")
                    except Exception:
                        pass
        # save cache
        _save_cache(cache)

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

            st.write(f"AI 呼び出し回数（このセッション）: {st.session_state['ai_call_count']}")

            st.download_button(
                label="📥 PDFをダウンロード (一覧表付き)",
                data=pdf_bytes,
                file_name=f"label_{datetime.now().strftime('%m%d%H%M')}.pdf",
                mime="application/pdf"
            )

# Keep existing postprocess_ai_results function in the file (not shown here for brevity).