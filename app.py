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

# Secretsから取得（Streamlit Cloud等で設定）
COMPANY_NAME = st.secrets.get("COMPANY_NAME", "(株)アイプラス")
API_KEY = st.secrets.get("GEMINI_API_KEY", "")

client = genai.Client(api_key=API_KEY)

def safe_int(v):
    if v is None: return 0
    if isinstance(v, int): return v
    s = re.sub(r'\D', '', str(v))
    return int(s) if s else 0

# 2. AI画像解析（Gemini 2.0 Flash）
def get_order_data(image):
    prompt = """画像を解析し、以下の計算ルールを適用してJSONで返してください。
    【重要】
    1. unit, boxes, remainderには「数字のみ」を入れてください。
    2. 「青梗菜」は「チンゲン菜」「ちんげん菜」と表記されている場合もあります。これらをすべて「青梗菜」として統一して読み取ってください。
    
    【ルール】胡瓜(3本P):30/箱, 胡瓜(バラ):100/箱(50以上なら50本箱1,未満バラ), 春菊:30/箱, 青梗菜:20/箱, 長ネギ(2本P):30/箱
    【出力JSON例】[{"store":"店舗名","item":"品目名","spec":"規格","unit":30,"boxes":5,"remainder":0}]"""
    
    try:
        response = client.models.generate_content(model="gemini-2.0-flash", contents=[prompt, image])
        text = response.text
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0]
        return json.loads(text.strip())
    except Exception as e:
        st.error(f"解析エラー: {e}")
        return None

# 3. PDF作成（B5サイズ：一覧表 ＋ 伝票）
def create_b5_pdf(data):
    # B5サイズ (182mm x 257mm)
    pdf = FPDF(orientation='P', unit='mm', format=(182, 257))
    
    # フォント登録（ipaexg.ttfが実行ディレクトリに必要）
    if os.path.exists('ipaexg.ttf'):
        pdf.add_font('Gothic', fname='ipaexg.ttf')
        pdf.add_font('Gothic', style='B', fname='ipaexg.ttf')
        font_name = 'Gothic'
    else:
        font_name = 'Arial' # フォントがない場合の予備
    
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
    pdf.set_font(font_name, style='B', size=14)
    for entry in data:
        r_val = safe_int(entry.get('remainder', 0))
        rem_box = 1 if r_val > 0 else 0
        pdf.cell(55, 12, f" {entry.get('store','')}", border=1)
        pdf.cell(55, 12, f" {entry.get('item','')}", border=1)
        pdf.cell(25, 12, f" {entry.get('boxes',0)}", border=1, align='C')
        pdf.cell(25, 12, f" {rem_box}", border=1, align='C', ln=True)

    # --- 2ページ目以降：個別伝票 ---
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
        pdf.set_font(font_name, style='B', size=24); pdf.cell(col2/2, h, f" {u_val}", border=1)
        pdf.cell(col2/2, h, f" {b_val} ケース", border=1, ln=True)
        
        # 端数
        pdf.set_font(font_name, style='B', size=18); pdf.cell(col1, h, " 端数", border=1)
        pdf.set_font(font_name, style='B', size=24); pdf.cell(col2/2, h, f" {r_val if r_val > 0 else ''}", border=1)
        rem_box = 1 if r_val > 0 else 0
        pdf.cell(col2/2, h, f" {rem_box} ケース", border=1, ln=True)
        
        # TOTAL
        pdf.set_font(font_name, style='B', size=20); pdf.cell(col1, h, " TOTAL 数", border=1)
        pdf.set_font(font_name, style='B', size=42)
        total_qty = (u_val * b_val) + r_val
        pdf.cell(col2, h, f" {total_qty}", border=1, ln=True)

    return pdf.output()

# 4. メイン画面レイアウト
st.title("📦 配送伝票作成システム")
uploaded_file = st.file_uploader("注文画像をアップロード", type=['png', 'jpg', 'jpeg'])

if uploaded_file:
    image = Image.open(uploaded_file)
    st.image(image, caption="アップロード画像", use_container_width=True)
    
    if st.button("配送伝票を生成"):
        with st.spinner('AIが解析中...'):
            order_data = get_order_data(image)
            if order_data:
                # PDF作成
                pdf_bytes = create_b5_pdf(order_data)
                st.success("伝票が完成しました！")

                # LINE用集計の表示
                st.subheader("📋 LINE用集計（コピー用）")
                summary_packs = defaultdict(int)
                for entry in order_data:
                    total = (safe_int(entry.get('unit',0)) * safe_int(entry.get('boxes',0))) + safe_int(entry.get('remainder',0))
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
                    data=bytes(pdf_bytes),
                    file_name=f"label_{datetime.now().strftime('%m%d%H%M')}.pdf",
                    mime="application/pdf"
