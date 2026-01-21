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

# 1. 初期設定
st.set_page_config(page_title="配送管理システム", layout="centered")

# Secretsから取得
COMPANY_NAME = st.secrets.get("COMPANY_NAME", "(株)アイプラス")
API_KEY = st.secrets.get("GEMINI_API_KEY", "")

client = genai.Client(api_key=API_KEY)

def safe_int(v):
    if v is None: return 0
    if isinstance(v, int): return v
    s = re.sub(r'\D', '', str(v))
    return int(s) if s else 0

# 2. AI画像解析（Gemini 2.0 Flash Lite）
def get_order_data(image):
    prompt = """画像を解析し、以下のルールに従ってJSONで回答してください。

【品名判定ルール】
- 「胡瓜」で「バラ」「B品」「箱なし」「規格外」等の記載があれば品目を「胡瓜(バラ)」とする。
- 「青梗菜」「チンゲン菜」「ちんげん菜」はすべて「青梗菜」に統一する。

【計算ルール】
- 胡瓜(3本P): 30/箱
- 春菊: 30/箱
- 青梗菜: 20/箱
- 長ネギ(2本P): 30/箱
- **胡瓜(バラ): 注文数が50以上なら[unit:50, boxes:1, remainder:総数-50]、50未満なら[unit:0, boxes:0, remainder:総数]とする。**

【出力JSON形式】
数字のみを入れ、Markdownタグ(```json)は不要です。
[{"store":"店舗名","item":"品目名","spec":"規格","unit":"30","boxes":"5","remainder":"0"}]"""

    # ...（中略：client.models.generate_content等の処理）...
    # ... (以下、元の処理と同じ)
    response = client.models.generate_content(model="gemini-2.0-flash", contents=[prompt, image])
    try:
        text = response.text
        if "```json" in text: text = text.split("```json")[1].split("```")[0]
        return json.loads(text.strip())
    except:
        return None

# 3. PDF作成（一覧表 ＋ 視認性最大化レイアウト）
def create_b5_pdf(data):
    pdf = FPDF(orientation='P', unit='mm', format=(182, 257))
    pdf.add_font('Gothic', fname='ipaexg.ttf')
    pdf.add_font('Gothic', style='B', fname='ipaexg.ttf')
    
    # --- 【ここ重要】明日の日付を計算して変数に入れる ---
    tomorrow = datetime.now() + timedelta(days=1)
    tomorrow_pdf_str = tomorrow.strftime('%m 月 %d 日') # 伝票用
    tomorrow_list_str = tomorrow.strftime('%m/%d')    # 一覧表用

    # --- 1ページ目：全体一覧表 ---
    pdf.add_page()
    pdf.set_line_width(0.2) # 枠線を細く
    pdf.set_font('Gothic', style='B', size=20) # 太字
    pdf.cell(0, 15, f"【出荷一覧表】 {tomorrow_list_str}", ln=True, align='C')
    
    # テーブルヘッダー
    pdf.set_font('Gothic', style='B', size=12)
    pdf.set_fill_color(230, 230, 230)
    pdf.cell(55, 12, " 店舗名", border=1, fill=True)
    pdf.cell(55, 12, " 品目", border=1, fill=True)
    pdf.cell(25, 12, " フル箱", border=1, fill=True, align='C')
    pdf.cell(25, 12, " 端数箱", border=1, fill=True, align='C', ln=True)
    
    # テーブル中身
    pdf.set_font('Gothic', style='B', size=14)
    for entry in data:
        r_val = safe_int(entry['remainder'])
        rem_box = 1 if r_val > 0 else 0
        pdf.cell(55, 12, f" {entry['store']}", border=1)
        pdf.cell(55, 12, f" {entry['item']}", border=1)
        pdf.cell(25, 12, f" {entry['boxes']}", border=1, align='C')
        pdf.cell(25, 12, f" {rem_box}", border=1, align='C', ln=True)

    # --- 2ページ目以降：個別伝票 ---
    for entry in data:
        pdf.add_page()
        pdf.set_auto_page_break(auto=False)
        pdf.set_draw_color(0, 0, 0)
        pdf.set_line_width(0.2) # 枠線を細く
        
        pdf.set_font('Gothic', style='B', size=26)
        pdf.cell(0, 25, f"{COMPANY_NAME} (千葉県産)", align='C', ln=True)
        pdf.ln(2)

        col1, col2, h = 45, 122, 30
        
        # すべての項目に style='B' を適用
        pdf.set_font('Gothic', style='B', size=18); pdf.cell(col1, h, " 行先", border=1)
        pdf.set_font('Gothic', style='B', size=36); pdf.cell(col2, h, f" {entry['store']}", border=1, ln=True)
        
        pdf.set_font('Gothic', style='B', size=18); pdf.cell(col1, h, " 商品名", border=1)
        pdf.set_font('Gothic', style='B', size=32); pdf.cell(col2, h, f" {entry['item']}", border=1, ln=True)
        
        pdf.set_font('Gothic', style='B', size=18); pdf.cell(col1, h, " 出荷日", border=1)
        pdf.set_font('Gothic', style='B', size=26)
        pdf.cell(col2, h, f" {tomorrow_pdf_str}", border=1, ln=True)
        
        pdf.set_font('Gothic', style='B', size=18); pdf.cell(col1, h, " 規格", border=1)
        pdf.set_font('Gothic', style='B', size=26); pdf.cell(col2, h, f" {entry.get('spec', '')}", border=1, ln=True)
        
        u_val, b_val, r_val = safe_int(entry['unit']), safe_int(entry['boxes']), safe_int(entry['remainder'])
        
        pdf.set_font('Gothic', style='B', size=18); pdf.cell(col1, h, " 入数", border=1)
        pdf.set_font('Gothic', style='B', size=24); pdf.cell(col2/2, h, f" {u_val}", border=1)
        pdf.cell(col2/2, h, f" {b_val} ケース", border=1, ln=True)
        
        pdf.set_font('Gothic', style='B', size=18); pdf.cell(col1, h, " 端数", border=1)
        pdf.set_font('Gothic', style='B', size=24); pdf.cell(col2/2, h, f" {r_val if r_val > 0 else ''}", border=1)
        rem_box = 1 if r_val > 0 else 0
        pdf.cell(col2/2, h, f" {rem_box} ケース", border=1, ln=True)
        
        pdf.set_font('Gothic', style='B', size=20); pdf.cell(col1, h, " TOTAL 数", border=1)
        pdf.set_font('Gothic', style='B', size=42); total_qty = (u_val * b_val) + r_val
        pdf.cell(col2, h, f" {total_qty}", border=1, ln=True)

    return pdf.output()

# 4. メイン画面
st.title("📦 配送伝票作成システム")

# 【修正】複数ファイルを受け取れるように変更
uploaded_files = st.file_uploader("注文画像をアップロード（複数可）", type=['png', 'jpg', 'jpeg'], accept_multiple_files=True)

if uploaded_files:
    if st.button("配送伝票を生成"):
        all_order_data = [] # 全ての画像データを統合するリスト
        
        with st.spinner('AIが順次解析中...'):
            # 【追加】アップロードされた各画像をループで処理
            for uploaded_file in uploaded_files:
                image = Image.open(uploaded_file)
                order_data = get_order_data(image)
                if order_data:
                    all_order_data.extend(order_data) # データを合流させる

            if all_order_data:
                # 【修正】統合したデータ(all_order_data)でPDFを作成
                pdf_bytes = create_b5_pdf(all_order_data)
                st.success(f"画像{len(uploaded_files)}枚分の伝票が完成しました！")

                # --- LINE用集計（all_order_dataを使用） ---
                st.subheader("📋 LINE用集計（コピー用）")
                summary_packs = defaultdict(int)
                for entry in all_order_data:
                    total = (safe_int(entry['unit']) * safe_int(entry['boxes'])) + safe_int(entry['remainder'])
                    summary_packs[entry['item']] += total

                # ...（以下、LINE用テキスト表示とダウンロードボタンの処理は元のまま）...
                
                # 表示用テキスト
                line_text = f"【{datetime.now().strftime('%m/%d')} 出荷・作成総数】\n"
                for item, total in summary_packs.items():
                    # 品目名に合わせて単位を推測（パックまたは袋）
                    unit_label = "袋" if "春菊" in item or "青梗菜" in item else "パック"
                    line_text += f"・{item}：{total}{unit_label}\n"
                
                st.code(line_text, language="text")
                st.write("↑ タップしてコピーし、LINEに貼り付けてください。")

                # ダウンロードボタン
                st.download_button(
                    label="📥 PDFをダウンロード (一覧表付き)",
                    data=bytes(pdf_bytes),
                    file_name=f"label_{datetime.now().strftime('%m%d%H%M')}.pdf",
                    mime="application/pdf"
                )