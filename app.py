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
API_KEY = st.secrets["GEMINI_API_KEY"]
client = genai.Client(api_key=API_KEY)

# 2. AI画像解析
def get_order_data(image):
    prompt = """注文画像を解析し、以下の計算ルールを適用してJSON形式で返してください。
    【ルール】胡瓜(3本P):30/ケース, 胡瓜(バラ):100/ケース(50以上なら50本ケース1,未満端数), 春菊:30/ケース, 青梗菜:20/ケース, 長ネギ(2本P):30/ケース
    【出力】[{"store":"店舗名","item":"品目名","spec":"入数","boxes":ケース数,"remainder":端数}]"""
    
    response = client.models.generate_content(model="gemini-2.0-flash-lite", contents=[prompt, image])
    try:
        text = response.text
        if "```json" in text: text = text.split("```json")[1].split("```")[0]
        return json.loads(text.strip())
    except:
        return None

# 3. PDF作成（エラー対策済み・特大文字・1枚まとめ）
def create_b5_pdf(data):
    grouped = defaultdict(list)
    for entry in data:
        grouped[entry['store']].append(entry)

    pdf = FPDF(orientation='P', unit='mm', format=(182, 257))
    
    # --- エラー対策：フォントの場所を特定する ---
    current_dir = os.path.dirname(__file__)
    font_path = os.path.join(current_dir, 'ipaexg.ttf')
    pdf.add_font('Gothic', fname=font_path)
    
    pdf.add_page()
    current_y = 15

    for store_name, items in grouped.items():
        # 次の店舗を書くスペースが足りない場合は改ページ
        if current_y > 180:
            pdf.add_page()
            current_y = 15

        # --- 店舗名 ---
        pdf.set_text_color(60, 60, 60)
        pdf.set_font('Gothic', size=48)
        pdf.set_xy(15, current_y)
        pdf.multi_cell(152, 22, store_name, align='C')
        current_y = pdf.get_y() + 2

        # --- 区切り線 ---
        pdf.set_draw_color(120, 120, 120)
        pdf.set_line_width(0.5)
        pdf.line(15, current_y, 167, current_y)
        current_y += 8

        # --- 商品リスト ---
        pdf.set_text_color(0, 0, 0)
        store_total = 0
        for item in items:
            pdf.set_xy(15, current_y)
            pdf.set_font('Gothic', size=32)
            pdf.cell(95, 18, item['item'], align='L')
            
            pdf.set_font('Gothic', size=28)
            detail = f"{item['boxes']}ケース"
            if item['remainder'] > 0: detail += f" +端数{item['remainder']}"
            pdf.cell(57, 18, detail, align='R')
            
            store_total += item['boxes'] + (1 if item['remainder'] > 0 else 0)
            current_y += 20

        # --- 店舗合計 ---
        current_y += 4
        pdf.set_xy(15, current_y)
        pdf.set_draw_color(0, 0, 0)
        pdf.set_line_width(1.5)
        pdf.set_font('Gothic', size=60)
        pdf.cell(152, 35, f"計 {store_total} ケース", border=1, align='C')
        
        current_y += 50 # 店舗間の余白

    return pdf.output()

# 4. メインUI（以下略）
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