import streamlit as st
import json
import os
import re
from datetime import datetime, timedelta
from PIL import Image
from fpdf import FPDF
from google import genai
from collections import defaultdict
import io
from typing import List, Dict, Any, Optional

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
        s = re.sub(r'[^\d\-]', '', str(v))
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
    if re.search(r'胡瓜|きゅうり|キュウリ|キュウリ', s):
        # バラ（ばら）判定
        if re.search(r'バラ|ばら|バラ\)|バラ\(|バラ$', s):
            return "胡瓜(バラ)"
        # 3本パック判定
        if re.search(r'3本|3本P|3本パック', s):
            return "胡瓜(3本P)"
        # default: if contains 'バラ' anywhere
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
            # 50本箱を1つ追加
            result["fifty_box"] = 1
            remainder = remainder - 50
            # もし remainder が負になれば 0 にする
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
        # 正規化
        entry['item'] = normalize_item_name(entry.get('item', '') or entry.get('品目', '') or '')
        entry['store'] = entry.get('store', '') or entry.get('店舗', '')
        entry['spec'] = entry.get('spec', '') or entry.get('規格', '')

        # 欠損チェック：unit, boxes, remainder
        u = safe_int(entry.get('unit', None))
        b = safe_int(entry.get('boxes', None))
        r = safe_int(entry.get('remainder', None))

        # もし total や count が与えられていたらそれを使う
        total_candidate = None
        if 'total' in entry:
            total_candidate = safe_int(entry.get('total'))
        elif 'count' in entry:
            total_candidate = safe_int(entry.get('count'))
        elif '数量' in entry:
            total_candidate = safe_int(entry.get('数量'))

        if (u == 0 or b == 0) and total_candidate is not None:
            # 算出する
            comp = compute_boxes_and_remainder(total_candidate, entry['item'])
            entry['unit'] = comp['unit']
            entry['boxes'] = comp['boxes']
            entry['remainder'] = comp['remainder']
            if comp.get('fifty_box'):
                entry['fifty_box'] = comp['fifty_box']
        else:
            # もし unit/boxes/remainder のどれかが埋まっていれば安全に数値化して補う
            entry['unit'] = u
            entry['boxes'] = b
            entry['remainder'] = r

            # もし unit が 0 で total_candidate があるなら unit ルールから補完
            if entry['unit'] == 0 and total_candidate is not None:
                comp = compute_boxes_and_remainder(total_candidate, entry['item'])
                entry['unit'] = comp['unit']
                entry['boxes'] = comp['boxes']
                entry['remainder'] = comp['remainder']
                if comp.get('fifty_box'):
                    entry['fifty_box'] = comp['fifty_box']

        processed.append(entry)
    return processed

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
        # Gemini API: prompt と image を contents に含める（既存の実装にならう）
        response = client.models.generate_content(model="gemini-2.0-flash", contents=[prompt, image])
        text = response.text or ""
        # AI が ```json で返す場合に対応
        if "```json" in text:
            try:
                text = text.split("```json")[1].split("```[0]")[0]
            except Exception:
                pass
        # 最終的に JSON パース
        parsed = json.loads(text.strip())
        if isinstance(parsed, list):
            return parsed
        # もし dict の場合それを list に包む
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
    # B5サイズ (182mm x 257mm)
    pdf = FPDF(orientation='P', unit='mm', format=(182, 257))

    # フォント登録（ipaexg.ttfが実行ディレクトリに必要）
    if os.path.exists('ipaexg.ttf'):
        try:
            pdf.add_font('Gothic', fname='ipaexg.ttf', uni=True)
            pdf.add_font('Gothic', style='B', fname='ipaexg.ttf', uni=True)
            font_name = 'Gothic'
        except Exception:
            font_name = 'Arial'
    else:
        font_name = 'Arial'  # フォントがない場合の予備

    # 日付計算
    tomorrow = datetime.now() + timedelta(days=1)
    tomorrow_pdf_str = tomorrow.strftime('%m 月 %d 日')
    tomorrow_list_str = tomorrow.strftime('%m/%d')

    # --- 1ページ目：全体一覧表 ---
    pdf.add_page()
    pdf.set_font(font_name, style='B', size=20)
    pdf.cell(0, 15, f"【出荷一覧表】 {tomorrow_list_str}", ln=True, align='C')

    # テーブルヘッダー
    pdf.set_font(font_name, style='B', size=12)
    pdf.set_fill_color(230, 230, 230)
    pdf.cell(55, 12, " 店舗名", border=1, fill=True)
    pdf.cell(55, 12, " 品目", border=1, fill=True)
    pdf.cell(25, 12, " フル箱", border=1, fill=True, align='C')
    pdf.cell(25, 12, " 端数箱", border=1, fill=True, align='C', ln=True)

    # テーブル内容
    pdf.set_font(font_name, style='', size=12)
    for entry in data:
        r_val = safe_int(entry.get('remainder', 0))
        rem_box = 1 if r_val > 0 or entry.get('fifty_box', 0) else 0
        pdf.cell(55, 12, f" {entry.get('store','')}", border=1)
        pdf.cell(55, 12, f" {entry.get('item','')}", border=1)
        pdf.cell(25, 12, f" {safe_int(entry.get('boxes',0))}", border=1, align='C')
        pdf.cell(25, 12, f" {rem_box}", border=1, align='C', ln=True)

    # --- 個別伝票 ---
    for entry in data:
        pdf.add_page()
        pdf.set_auto_page_break(auto=False)
        pdf.set_line_width(0.2)

        pdf.set_font(font_name, style='B', size=26)
        pdf.cell(0, 25, f"{COMPANY_NAME} (千葉県産)", align='C', ln=True)
        pdf.ln(2)

        col1, col2, h = 45, 122, 30

        # 行先
        pdf.set_font(font_name, style='B', size=18); pdf.cell(col1, h, " 行先", border=1)
        pdf.set_font(font_name, style='B', size=36); pdf.cell(col2, h, f" {entry.get('store','')}", border=1, ln=True)

        # 商品名
        pdf.set_font(font_name, style='B', size=18); pdf.cell(col1, h, " 商品名", border=1)
        pdf.set_font(font_name, style='B', size=32); pdf.cell(col2, h, f" {entry.get('item','')}", border=1, ln=True)

        # 出荷日
        pdf.set_font(font_name, style='B', size=18); pdf.cell(col1, h, " 出荷日", border=1)
        pdf.set_font(font_name, style='B', size=26); pdf.cell(col2, h, f" {tomorrow_pdf_str}", border=1, ln=True)

        # 規格
        pdf.set_font(font_name, style='B', size=18); pdf.cell(col1, h, " 規格", border=1)
        pdf.set_font(font_name, style='B', size=26); pdf.cell(col2, h, f" {entry.get('spec', '')}", border=1, ln=True)

        # 入数・箱数
        u_val = safe_int(entry.get('unit',0))
        b_val = safe_int(entry.get('boxes',0))
        r_val = safe_int(entry.get('remainder',0))

        pdf.set_font(font_name, style='B', size=18); pdf.cell(col1, h, " 入数", border=1)
        pdf.set_font(font_name, style='B', size=24); pdf.cell(col2/2, h, f" {u_val if u_val>0 else ''}", border=1)
        pdf.cell(col2/2, h, f" {b_val} ケース", border=1, ln=True)

        # 端数
        pdf.set_font(font_name, style='B', size=18); pdf.cell(col1, h, " 端数", border=1)
        pdf.set_font(font_name, style='B', size=24)
        # 胡瓜(バラ) の 50本箱表現がある場合、それを優先表示する
        if entry.get('fifty_box', 0):
            # 例: "50本箱1" と表示して、その後に残り端数を表示
            display_rem = f"{r_val if r_val>0 else ''}"
            pdf.cell(col2/2, h, f" {display_rem}", border=1)
            pdf.cell(col2/2, h, f" 50本箱1", border=1, ln=True)
        else:
            pdf.cell(col2/2, h, f" {r_val if r_val > 0 else ''}", border=1)
            rem_box = 1 if r_val > 0 else 0
            pdf.cell(col2/2, h, f" {rem_box} ケース", border=1, ln=True)

        # TOTAL
        pdf.set_font(font_name, style='B', size=20); pdf.cell(col1, h, " TOTAL 数", border=1)
        pdf.set_font(font_name, style='B', size=42)
        total_qty = (u_val * b_val) + r_val + (50 if entry.get('fifty_box', 0) else 0)
        pdf.cell(col2, h, f" {total_qty}", border=1, ln=True)

    # PDF を bytes で返す
    pdf_bytes = pdf.output(dest='S').encode('latin-1')
    return pdf_bytes

# 4. メイン画面レイアウト
st.title("📦 配送伝票作成システム (改良版)")
uploaded_files = st.file_uploader("注文画像をアップロード（複数可）", type=['png', 'jpg', 'jpeg'], accept_multiple_files=True)

if uploaded_files:
    # プレビューを横並びで表示（1枚ずつ）
    st.subheader("アップロード画像")
    for idx, f in enumerate(uploaded_files):
        try:
            img = Image.open(f)
            st.image(img, caption=f"画像 {idx+1}: {getattr(f, 'name', '')}", use_container_width=True)
        except Exception as ex:
            st.warning(f"画像 {idx+1} の読み込みに失敗しました: {ex}")

    if st.button("配送伝票を生成"):
        all_raw = []
        with st.spinner('AIが解析中（複数画像）...'):
            # 画像ごとに解析して結果をマージ
            for f in uploaded_files:
                try:
                    img = Image.open(f)
                except Exception as ex:
                    st.error(f"画像読み込み失敗: {ex}")
                    continue
                ai_res = get_order_data(img)
                if ai_res:
                    all_raw.extend(ai_res)
                else:
                    st.warning(f"画像 {getattr(f, 'name', '')} の解析でデータが得られませんでした。")

            if not all_raw:
                st.error("どの画像からも注文データを取得できませんでした。")
            else:
                # 後処理（正規化・欠損補完）
                processed = postprocess_ai_results(all_raw)

                # PDF作成
                pdf_bytes = create_b5_pdf(processed)
                st.success("伝票が完成しました！")

                # LINE用集計の表示
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

                # ダウンロードボタン
                st.download_button(
                    label="📥 PDFをダウンロード (一覧表付き)",
                    data=pdf_bytes,
                    file_name=f"label_{datetime.now().strftime('%m%d%H%M')}.pdf",
                    mime="application/pdf"
                )
