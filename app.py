import streamlit as st
import json
import os
from datetime import datetime
from PIL import Image
from fpdf import FPDF
from collections import defaultdict
from google import genai
import io

# 1. 初期設定とUI
st.set_page_config(page_title="小島農園 配送システム", layout="centered")
st.title("📄 小島農園 配送伝票作成")
st.write("和郷園形式をベースにした、トナー節約・高視認性デザインです。")

# SecretsからAPIキーを取得
API_KEY = st.secrets["GEMINI_API_KEY"]
client = genai.Client(api_key=API_KEY)

# 2. AIによる画像解析（Gemini 2.0 Flash Lite）
def get_order_data(image):
    prompt = """画像を解析し、以下の計算ルールを適用してJSON形式で返してください。
    【ルール】胡瓜(3本P):30/箱, 胡瓜(バラ):100/箱(50以上なら50本箱1,未満バラ), 春菊:30/箱, 青梗菜:20/箱, 長ネギ(2本P):30/箱
    【出力JSON】[{"store":"店舗名","item":"品目名","spec":"規格(例:2-3株)","unit":"入数","boxes":ケース数,"remainder":端数}]"""
    
    response = client.models.generate_content(model="gemini-2.0-flash-lite", contents=[prompt, image])
    try:
        text = response.text
        if "```json" in text: text = text.split("```json")[1].split("```")[0]
        return json.loads(text.strip())
    except:
        return None

# 3. PDF作成（B5・和郷園リスペクト・トナー節約デザイン）
def create_b5_pdf(data):
    grouped = defaultdict(list)
    for entry in data:
        grouped[entry['store']].append(entry)

    # B5サイズ (182mm x 257mm)
    pdf = FPDF(orientation='P', unit='mm', format=(182, 257))
    pdf.add_font('Gothic', fname='ipag.ttf')
    
    for store_name, items in grouped.items():
        pdf.add_page()
        
        # --- ヘッダー（和郷園風のタイトル） ---
        pdf.set_text_color(40, 40, 40) # 濃い目のグレー（トナー節約）
        pdf.set_font('Gothic', size=22)
        pdf.cell(0, 15, "小 島 農 園 ( 千 葉 県 産 )", align='C', ln=True)
        
        # 店舗名（「様」なし、ドカンと中央に）
        pdf.set_font('Gothic', size=32)
        pdf.ln(5)
        pdf.cell(0, 20, store_name, align='C', ln=True)
        pdf.ln(5)

        # --- テーブルヘッダー（和郷園の表組みを再現） ---
        pdf.set_draw_color(100, 100, 100) # 薄い黒の線
        pdf.set_line_width(0.3)
        pdf.set_font('Gothic', size=12)
        
        # カラム設定
        cols = [70, 35, 30, 27] # 品目名, 規格, 入数, ケース数
        h = 12
        pdf.cell(cols[0], h, " 商品名", border=1)
        pdf.cell(cols[1], h, " 規格", border=1)
        pdf.cell(cols[2], h, " 入数", border=1)
        pdf.cell(cols[3], h, " ケース数", border=1, ln=True)

        # --- テーブル内容 ---
        pdf.set_font('Gothic', size=16)
        total_cases = 0
        for item in items:
            pdf.cell(cols[0], h, f" {item['item']}", border=1)
            pdf.set_font('Gothic', size=12)
            pdf.cell(cols[1], h, f" {item['spec']}", border=1)
            pdf.cell(cols[2], h, f" {item['unit']}", border=1)
            
            # ケース数計算（フル箱 + 端数がある場合は+1箱）
            item_boxes = item['boxes'] + (1 if item['remainder'] > 0 else 0)
            pdf.set_font('Gothic', size=16)
            pdf.cell(cols[3], h, f" {item_boxes}", border=1, ln=True, align='C')
            total_cases += item_boxes

        # --- フッター（TOTAL数） ---
        pdf.ln(10)
        pdf.set_font('Gothic', size=14)
        pdf.cell(135, 20, " TOTAL ケース数", border=0, align='R')
        pdf.set_font('Gothic', size=45)
        pdf.cell(27, 20, f" {total_cases}", border=0, ln=True, align='L')
        
        # 和郷園風の区切り太線（最下部）
        pdf.set_line_width(1.0)
        pdf.line(10, 245, 172, 245)
        pdf.set_font('Gothic', size=10)
        pdf.text(10, 250, f"出荷日: {datetime.now().strftime('%Y年%m月%d日')}  生産者: 小島農園")

    return pdf.output()

# 4. メイン処理
uploaded_file = st.file_uploader("注文画像をアップロード", type=['png', 'jpg', 'jpeg'])

if uploaded_file:
    image = Image.open(uploaded_file)
    st.image(image, caption='確認用画像', use_container_width=True)
    
    if st.button("配送伝票PDFを生成"):
        with st.spinner('AIが伝票を作成中...'):
            order_data = get_order_data(image)
            if order_data:
                pdf_bytes = create_b5_pdf(order_data)
                st.success("伝票が完成しました！")
                st.download_button(
                    label="📥 B5伝票をダウンロード",
                    data=bytes(pdf_bytes),
                    file_name=f"kojima_label_{datetime.now().strftime('%m%d%H%M')}.pdf",
                    mime="application/pdf"
                )