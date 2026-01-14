import streamlit as st
import json
import os
from datetime import datetime
from PIL import Image
from fpdf import FPDF
from google import genai
import io

# 1. 初期設定
st.set_page_config(page_title="配送管理システム", layout="centered")

# --- GitHub公開用の配慮：Secretsから情報を取得 ---
# Streamlit CloudのSecretsに設定した値を使います。未設定時は空欄になります。
COMPANY_NAME = st.secrets.get("COMPANY_NAME", "")
PRODUCER_NAME = st.secrets.get("PRODUCER_NAME", "")
API_KEY = st.secrets.get("GEMINI_API_KEY", "")

client = genai.Client(api_key=API_KEY)

# 2. AI画像解析
def get_order_data(image):
    prompt = """画像を解析し、以下の計算ルールを適用してJSON形式で返してください。
    【ルール】胡瓜(3本P):30/箱, 胡瓜(バラ):100/箱(50以上なら50本箱1,未満バラ), 春菊:30/箱, 青梗菜:20/箱, 長ネギ(2本P):30/箱
    【出力JSON】[{"store":"店舗名","item":"品目名","spec":"規格","unit":"入数","boxes":フル箱数,"remainder":端数}]"""
    
    response = client.models.generate_content(model="gemini-2.0-flash-lite", contents=[prompt, image])
    try:
        text = response.text
        if "```json" in text: text = text.split("```json")[1].split("```")[0]
        return json.loads(text.strip())
    except:
        return None

# 3. PDF作成（和郷園・案A形式：1品目1枚）
def create_b5_pdf(data):
    # B5サイズ (182mm x 257mm)
    pdf = FPDF(orientation='P', unit='mm', format=(182, 257))
    pdf.add_font('Gothic', fname='ipaexg.ttf')
    
    for entry in data:
        pdf.add_page()
        pdf.set_auto_page_break(auto=False)
        
        # --- ヘッダー領域 ---
        pdf.set_draw_color(0, 0, 0)
        pdf.set_line_width(0.5)
        
        # タイトル（会社名）
        pdf.set_font('Gothic', size=18)
        pdf.cell(0, 15, f"{COMPANY_NAME} (千葉県産)", align='C', ln=True)
        
        # 生産者名
        pdf.set_font('Gothic', size=12)
        pdf.text(15, 25, f"生産者名： {PRODUCER_NAME}")
        pdf.ln(10)

        # --- メイングリッド（和郷園形式） ---
        pdf.set_font('Gothic', size=14)
        col1 = 40  # 見出し幅
        col2 = 127 # 内容幅
        row_h = 22 # 基本の行の高さ
        
        # 1. 行先 (Destination)
        pdf.cell(col1, row_h, " 行先", border=1)
        pdf.set_font('Gothic', size=28)
        pdf.cell(col2, row_h, f" {entry['store']}", border=1, ln=True)
        
        # 2. 商品名 (Product)
        pdf.set_font('Gothic', size=14)
        pdf.cell(col1, row_h, " 商品名", border=1)
        pdf.set_font('Gothic', size=24)
        pdf.cell(col2, row_h, f" {entry['item']}", border=1, ln=True)
        
        # 3. 出荷日 (Date)
        pdf.set_font('Gothic', size=14)
        pdf.cell(col1, row_h, " 出荷日", border=1)
        today = datetime.now().strftime('%m 月 %d 日')
        pdf.cell(col2, row_h, f" {today}", border=1, ln=True)
        
        # 4. 規格 (Spec)
        pdf.cell(col1, row_h, " 規格", border=1)
        pdf.cell(col2, row_h, f" {entry.get('spec', '')}", border=1, ln=True)
        
        # 5. 入数 と ケース数(フル箱)
        pdf.cell(col1, row_h, " 入数", border=1)
        pdf.cell(col2/2, row_h, f" {entry['unit']}", border=1)
        pdf.cell(col2/2, row_h, f" ケース数： {entry['boxes']}", border=1, ln=True)
        
        # 6. 端数 と ケース数(端数箱)
        pdf.cell(col1, row_h, " 端数", border=1)
        pdf.cell(col2/2, row_h, f" {entry['remainder'] if entry['remainder'] > 0 else ''}", border=1)
        rem_box = 1 if entry['remainder'] > 0 else 0
        pdf.cell(col2/2, row_h, f" ケース数： {rem_box}", border=1, ln=True)
        
        # 7. TOTAL数
        pdf.cell(col1, row_h, " TOTAL 数", border=1)
        pdf.set_font('Gothic', size=20)
        total_qty = int(entry['unit']) * int(entry['boxes']) + int(entry['remainder'])
        pdf.cell(col2, row_h, f" {total_qty}", border=1, ln=True)
        
        # 8. マテハン名（空欄）
        pdf.set_font('Gothic', size=14)
        pdf.cell(col1, row_h, " マテハン名", border=1)
        pdf.cell(col2, row_h, "", border=1, ln=True)

    return pdf.output()

# 4. メイン画面
st.title("📦 配送伝票作成システム")
uploaded_file = st.file_uploader("注文画像をアップロード", type=['png', 'jpg', 'jpeg'])

if uploaded_file:
    image = Image.open(uploaded_file)
    if st.button("B5伝票PDFを生成"):
        with st.spinner('解析中...'):
            order_data = get_order_data(image)
            if order_data:
                pdf_bytes = create_b5_pdf(order_data)
                st.success("伝票が完成しました。")
                st.download_button(
                    label="📥 PDFをダウンロード",
                    data=bytes(pdf_bytes),
                    file_name=f"label_{datetime.now().strftime('%m%d%H%M')}.pdf",
                    mime="application/pdf"
                )