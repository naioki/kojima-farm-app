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
st.write("写真をアップロードしてください。B5サイズで店舗・端数をまとめます。")

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
        # モデル名は安定版の gemini-2.0-flash を使用
        response = client.models.generate_content(model="gemini-2.0-flash", contents=[prompt, image])
        text = response.text
        if "```json" in text: text = text.split("```json")[1].split("```")[0]
        return json.loads(text.strip())
    except Exception as e:
        st.error(f"解析エラー: {str(e)}")
        return None

# 3. PDF作成（店舗ごとの分断を防止するロジック）
def create_b5_pdf(data):
    grouped = defaultdict(list)
    for entry in data:
        grouped[entry['store']].append(entry)

    # B5サイズ (182mm x 257mm)
    pdf = FPDF(orientation='P', unit='mm', format=(182, 257))
    current_dir = os.path.dirname(__file__)
    font_path = os.path.join(current_dir, 'ipaexg.ttf')
    pdf.add_font('Gothic', fname=font_path)
    
    pdf.add_page()
    pdf.set_text_color(40, 40, 40)
    
    current_y = 15
    grand_total = 0

    for store_name, items in grouped.items():
        # --- この店舗を描画するのに必要な高さを計算 ---
        # 店舗名(20mm) + 線(5mm) + 商品(15mm×個数) + 合計(35mm) + 余白(10mm)
        needed_height = 20 + 5 + (len(items) * 15) + 35 + 10
        
        # もし残りのスペースが足りなければ、この店舗を書く前に改ページ
        if current_y + needed_height > 230: # 下から27mmの余裕
            pdf.add_page()
            current_y = 15

        # --- 店舗名 ---
        pdf.set_font('Gothic', size=42)
        pdf.set_xy(15, current_y)
        pdf.cell(152, 20, store_name, align='C', ln=True)
        current_y = pdf.get_y() + 1
        
        # --- 区切り線 ---
        pdf.set_draw_color(150, 150, 150)
        pdf.line(15, current_y, 167, current_y)
        current_y += 5

        # --- 商品リスト ---
        store_cases = 0
        for item in items:
            pdf.set_font('Gothic', size=28)
            pdf.set_xy(15, current_y)
            pdf.cell(90, 14, item['item'], align='L')
            
            detail = f"{item['boxes']}ケース"
            if item['remainder'] > 0:
                detail += f" +端数{item['remainder']}"
            pdf.cell(62, 14, detail, align='R', ln=True)
            
            # 各店舗の合計（端数がある場合も1ケースとしてカウントして車に載せるイメージ）
            store_cases += item['boxes'] + (1 if item['remainder'] > 0 else 0)
            current_y = pdf.get_y()

        # --- 店舗ごとの小計 ---
        current_y += 3
        pdf.set_xy(25, current_y) # 少し右に寄せて配置
        pdf.set_draw_color(0, 0, 0)
        pdf.set_line_width(1.0)
        pdf.set_font('Gothic', size=45)
        pdf.cell(132, 28, f"計 {store_cases} ケース", border=1, align='C')
        
        grand_total += store_cases
        current_y = pdf.get_y() + 25 # 店舗間の間隔

    # --- 最後に「全店舗の合計」を一番下に固定で表示 ---
    # 複数ページになっても、最後のページの最下部にこれが出ることで積み忘れを防ぎます
    pdf.set_xy(15, 215)
    pdf.set_draw_color(0, 0, 0)
    pdf.set_line_width(2.0) # 全合計は一番太い枠線
    pdf.set_font('Gothic', size=60)
    pdf.cell(152, 35, f"総計 {grand_total} ケース", border=1, align='C')

    return pdf.output()

# 4. メインUI（変更なし）
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