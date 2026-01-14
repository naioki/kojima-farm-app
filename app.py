import streamlit as st
import json
import os
import re
from datetime import datetime
from PIL import Image
from fpdf import FPDF
from google import genai
import io

# 1. 初期設定
st.set_page_config(page_title="配送管理システム", layout="centered")

# Secretsから取得（GitHub公開対策）
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
    prompt = """画像を解析し、以下の計算ルールを適用してJSONで返してください。
    【重要】unit, boxes, remainderには「数字のみ」を入れてください。
    【ルール】胡瓜(3本P):30/箱, 胡瓜(バラ):100/箱(50以上なら50本箱1,未満バラ), 春菊:30/箱, 青梗菜:20/箱, 長ネギ(2本P):30/箱
    【出力JSON】[{"store":"店舗名","item":"品目名","spec":"規格","unit":"30","boxes":"5","remainder":"0"}]"""
    
    response = client.models.generate_content(model="gemini-2.0-flash-lite", contents=[prompt, image])
    try:
        text = response.text
        if "```json" in text: text = text.split("```json")[1].split("```")[0]
        return json.loads(text.strip())
    except:
        return None

# 3. PDF作成（1品目1枚・格子状レイアウト）
def create_b5_pdf(data):
    pdf = FPDF(orientation='P', unit='mm', format=(182, 257))
    pdf.add_font('Gothic', fname='ipaexg.ttf')
    
    for entry in data:
        pdf.add_page()
        pdf.set_auto_page_break(auto=False)
        
        # --- ヘッダー ---
        pdf.set_draw_color(0, 0, 0)
        pdf.set_line_width(0.4)
        pdf.set_font('Gothic', size=20)
        pdf.cell(0, 20, f"{COMPANY_NAME} (千葉県産)", align='C', ln=True)
        pdf.ln(5)

        # --- グリッド構造（全7段） ---
        col1 = 40  # 見出し
        col2 = 127 # 内容
        h = 28     # 1行の高さ（情報を絞った分、少し高さを出して視認性アップ）
        
        # 1. 行先
        pdf.set_font('Gothic', size=14)
        pdf.cell(col1, h, " 行先", border=1)
        pdf.set_font('Gothic', size=28)
        pdf.cell(col2, h, f" {entry['store']}", border=1, ln=True)
        
        # 2. 商品名
        pdf.set_font('Gothic', size=14)
        pdf.cell(col1, h, " 商品名", border=1)
        pdf.set_font('Gothic', size=24)
        pdf.cell(col2, h, f" {entry['item']}", border=1, ln=True)
        
        # 3. 出荷日
        pdf.set_font('Gothic', size=14)
        pdf.cell(col1, h, " 出荷日", border=1)
        pdf.set_font('Gothic', size=20)
        today = datetime.now().strftime('%m 月 %d 日')
        pdf.cell(col2, h, f" {today}", border=1, ln=True)
        
        # 4. 規格
        pdf.set_font('Gothic', size=14)
        pdf.cell(col1, h, " 規格", border=1)
        pdf.cell(col2, h, f" {entry.get('spec', '')}", border=1, ln=True)
        
        # 数値取得
        u_val = safe_int(entry['unit'])
        b_val = safe_int(entry['boxes'])
        r_val = safe_int(entry['remainder'])
        
        # 5. 入数 と ケース数(フル)
        pdf.cell(col1, h, " 入数", border=1)
        pdf.cell(col2/2, h, f" {u_val}", border=1)
        pdf.set_font('Gothic', size=16)
        pdf.cell(col2/2, h, f" ケース数： {b_val}", border=1, ln=True)
        
        # 6. 端数 と ケース数(端数箱)
        pdf.set_font('Gothic', size=14)
        pdf.cell(col1, h, " 端数", border=1)
        pdf.cell(col2/2, h, f" {r_val if r_val > 0 else ''}", border=1)
        rem_box = 1 if r_val > 0 else 0
        pdf.cell(col2/2, h, f" ケース数： {rem_box}", border=1, ln=True)
        
        # 7. TOTAL数
        pdf.cell(col1, h, " TOTAL 数", border=1)
        pdf.set_font('Gothic', size=24)
        total_qty = (u_val * b_val) + r_val
        pdf.cell(col2, h, f" {total_qty}", border=1, ln=True)

    return pdf.output()

# 4. メイン画面
st.title("📦 配送伝票作成システム")
uploaded_file = st.file_uploader("注文画像をアップロード", type=['png', 'jpg', 'jpeg'])

if uploaded_file:
    image = Image.open(uploaded_file)
    if st.button("配送伝票を生成"):
        with st.spinner('解析中...'):
            order_data = get_order_data(image)
            if order_data:
                pdf_bytes = create_b5_pdf(order_data)
                st.success("伝票が完成しました。")
                st.download_button(label="📥 PDFダウンロード", data=bytes(pdf_bytes),
                                 file_name=f"label_{datetime.now().strftime('%m%d%H%M')}.pdf", mime="application/pdf")