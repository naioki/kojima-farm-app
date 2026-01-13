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
st.write("写真をアップロードしてください。B5サイズで1店舗1枚のラベルを作成します。")

# APIキーの設定
try:
    API_KEY = st.secrets["GEMINI_API_KEY"]
    client = genai.Client(api_key=API_KEY)
except Exception as e:
    st.error(f"APIキーの設定を確認してください: {e}")
    st.stop()

# 2. AI画像解析
def get_order_data(image):
    prompt = """注文画像を解析し、以下の計算ルールを適用してJSON形式で返してください。
    【ルール】胡瓜(3本P):30/ケース, 胡瓜(バラ):100/ケース(50以上なら50本ケース1,未満端数), 春菊:30/ケース, 青梗菜:20/ケース, 長ネギ(2本P):30/ケース
    【出力】[{"store":"店舗名","item":"品目名","spec":"入数","boxes":ケース数,"remainder":端数}]"""
    
    try:
        response = client.models.generate_content(model="gemini-2.0-flash", contents=[prompt, image])
        text = response.text
        if "```json" in text: text = text.split("```json")[1].split("```")[0]
        return json.loads(text.strip())
    except Exception as e:
        st.error(f"解析エラー: {str(e)}")
        return None

# 3. PDF作成（1店舗1枚・文字かぶり防止・特大文字）
def create_b5_pdf(data):
    grouped = defaultdict(list)
    for entry in data:
        grouped[entry['store']].append(entry)

    # B5サイズ (182mm x 257mm)
    pdf = FPDF(orientation='P', unit='mm', format=(182, 257))
    current_dir = os.path.dirname(__file__)
    font_path = os.path.join(current_dir, 'ipaexg.ttf')
    pdf.add_font('Gothic', fname=font_path)
    
    for store_name, items in grouped.items():
        # --- 店舗ごとに必ず新しいページを作成 ---
        pdf.add_page()
        pdf.set_text_color(40, 40, 40)
        
        # 1. 店舗名（超特大：サイズを60にアップ）
        pdf.set_font('Gothic', size=60)
        pdf.set_y(15)
        pdf.multi_cell(152, 25, store_name, align='C')
        
        # 2. 区切り線（太く）
        pdf.set_draw_color(100, 100, 100)
        pdf.set_line_width(1.0)
        pdf.line(15, pdf.get_y() + 5, 167, pdf.get_y() + 5)
        
        # 3. 商品リスト（フォントサイズ40で視認性向上）
        pdf.set_y(pdf.get_y() + 15)
        store_cases_total = 0
        for item in items:
            pdf.set_font('Gothic', size=40)
            # 商品名
            pdf.cell(90, 22, item['item'], align='L')
            # 数量
            detail = f"{item['boxes']}ケース"
            if item['remainder'] > 0:
                detail += f"\n+端数{item['remainder']}"
                # 端数がある場合は少し行間を調整
                pdf.set_font('Gothic', size=32)
                pdf.multi_cell(62, 11, detail, align='R')
            else:
                pdf.cell(62, 22, detail, align='R', ln=True)
            
            store_cases_total += item['boxes'] + (1 if item['remainder'] > 0 else 0)
            pdf.ln(5) # 商品間のスペース

        # 4. 店舗合計（ページ下部に固定・最大級サイズ）
        # Y=200の位置に配置することで、商品数が増えても被りにくくします
        pdf.set_xy(15, 205)
        pdf.set_draw_color(0, 0, 0)
        pdf.set_line_width(2.0)
        pdf.set_font('Gothic', size=85)
        pdf.cell(152, 40, f"計 {store_cases_total} ケース", border=1, align='C')

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