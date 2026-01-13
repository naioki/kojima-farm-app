import streamlit as st
import json
import os
from datetime import datetime
from PIL import Image
from fpdf import FPDF
from collections import defaultdict
from google import genai
import io

# 1. 初期設定
st.set_page_config(page_title="小島農園 配送ラベル作成", layout="centered")
st.title("📦 小島農園 配送ラベル作成")
st.write("注文メールの写真をアップロードしてください。B5サイズのラベルを自動作成します。")

# APIキーの設定
# Secretsがダメな場合、画面上で入力できるようにします（これなら安全です）
API_KEY = st.sidebar.text_input("Gemini API Key", type="password")
if not API_KEY:
    # Secretsから読み込みを試みる
    API_KEY = st.secrets.get("GEMINI_API_KEY", "")

if not API_KEY:
    st.warning("左側のメニューからAPIキーを入力するか、Secretsを設定してください。")
    st.stop()

client = genai.Client(api_key=API_KEY)

# 2. AI画像解析
def get_order_data(image):
    prompt = """注文画像を解析し、以下の計算ルールを適用してJSON形式で返してください。
    【ルール】胡瓜(3本P):30/箱, 胡瓜(バラ):100/箱(50以上なら50本箱1,未満バラ), 春菊:30/箱, 青梗菜:20/箱, 長ネギ(2本P):30/箱
    【出力】[{"store":"店舗名","item":"品目名","spec":"入数","boxes":箱数,"remainder":端数}]"""
    
    response = client.models.generate_content(model="gemini-2.0-flash-lite", contents=[prompt, image])
    try:
        text = response.text
        if "```json" in text: text = text.split("```json")[1].split("```")[0]
        return json.loads(text.strip())
    except:
        return None

# 3. PDF作成（B5・トナー節約デザイン）
def create_b5_pdf(data):
    grouped = defaultdict(list)
    for entry in data:
        grouped[entry['store']].append(entry)

    # 日本語フォントはGitHubに一緒にアップした ipaexg.ttf を使います
    pdf = FPDF(orientation='P', unit='mm', format=(182, 257))
    pdf.add_font('Gothic', fname='ipaexg.ttf')
    
    for store_name, items in grouped.items():
        pdf.add_page()
        pdf.set_text_color(60, 60, 60)
        pdf.set_font('Gothic', size=42)
        pdf.set_y(15)
        pdf.cell(0, 25, store_name, align='C', new_x="LMARGIN", new_y="NEXT")
        pdf.set_draw_color(120, 120, 120)
        pdf.set_line_width(0.3)
        pdf.line(15, 42, 167, 42) 

        pdf.set_text_color(0, 0, 0)
        pdf.set_y(55)
        store_total = 0
        for item in items:
            pdf.set_font('Gothic', size=24)
            pdf.cell(95, 15, item['item'], align='L')
            pdf.set_font('Gothic', size=20)
            detail = f"{item['boxes']}箱"
            if item['remainder'] > 0: detail += f" +バラ{item['remainder']}"
            pdf.cell(57, 15, detail, align='R', new_x="LMARGIN", new_y="NEXT")
            store_total += item['boxes'] + (1 if item['remainder'] > 0 else 0)
            pdf.ln(5)

        pdf.set_xy(15, 200)
        pdf.set_draw_color(0, 0, 0)
        pdf.set_line_width(1.2)
        pdf.set_font('Gothic', size=68)
        pdf.cell(152, 45, f"計 {store_total} 箱", border=1, align='C')

    return pdf.output()

# 4. メインUI
uploaded_file = st.file_uploader("写真を選択または撮影", type=['png', 'jpg', 'jpeg'])

if uploaded_file is not None:
    image = Image.open(uploaded_file)
    st.image(image, caption='アップロードされた画像', use_container_width=True)
    
    if st.button("ラベルPDFを作成する"):
        with st.spinner('AIが解析中...'):
            order_data = get_order_data(image)
            if order_data:
                pdf_data = create_b5_pdf(order_data)
                st.success("PDFが完成しました！")
                st.download_button(
                    label="📥 PDFをダウンロードして印刷",
                    data=bytes(pdf_data),
                    file_name=f"labels_{datetime.now().strftime('%H%M%S')}.pdf",
                    mime="application/pdf"
                )
            else:
                st.error("解析に失敗しました。もう一度試してください。")