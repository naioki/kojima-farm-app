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
import pandas as pd
import traceback
import pytesseract
from config_manager import (
    load_stores, save_stores, add_store, remove_store,
    load_items, save_items, add_item_variant, add_new_item, remove_item,
    auto_learn_store, auto_learn_item
)

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

# 2. 動的設定の読み込み
def get_known_stores():
    """店舗名リストを取得（動的）"""
    return load_stores()

def get_item_normalization():
    """品目名正規化マップを取得（動的）"""
    return load_items()

# 3. 品目名正規化関数（動的設定対応）
def normalize_item_name(item_name, auto_learn=True):
    """品目名を正規化する（動的設定対応）"""
    if not item_name:
        return ""
    item_name = str(item_name).strip()
    item_normalization = get_item_normalization()
    
    for normalized, variants in item_normalization.items():
        if item_name in variants or any(variant in item_name for variant in variants):
            return normalized
    
    # 見つからない場合、自動学習
    if auto_learn:
        return auto_learn_item(item_name)
    return item_name

# 4. 店舗名検証関数（動的設定対応）
def validate_store_name(store_name, auto_learn=True):
    """店舗名を検証し、最も近い店舗名を返す（動的設定対応）"""
    if not store_name:
        return None
    store_name = str(store_name).strip()
    known_stores = get_known_stores()
    
    # 完全一致
    if store_name in known_stores:
        return store_name
    # 部分一致
    for known_store in known_stores:
        if known_store in store_name or store_name in known_store:
            return known_store
    
    # 見つからない場合、自動学習
    if auto_learn:
        return auto_learn_store(store_name)
    return None

# 5. OCRでテキスト抽出
def extract_text_with_ocr(image):
    """OCRを使用して画像からテキストを抽出"""
    try:
        # pytesseractが利用可能かチェック
        if 'pytesseract' not in globals():
            return None
        # pytesseractの設定（日本語対応）
        text = pytesseract.image_to_string(image, lang='jpn')
        return text.strip()
    except NameError:
        # pytesseractがインポートされていない場合
        return None
    except Exception as e:
        # その他のOCRエラー（Tesseractがインストールされていない等）
        return None

# 6. AIテキスト解析（OCR結果を解析、トークン節約）
def get_order_data_from_text(text, max_retries=3):
    """OCRで抽出したテキストをAIで解析（画像解析よりトークン消費が少ない）"""
    known_stores = get_known_stores()
    item_normalization = get_item_normalization()
    
    # 品目名リストを生成
    item_list = ", ".join(item_normalization.keys())
    store_list = "、".join(known_stores)
    
    prompt = f"""以下のテキストは注文メールの内容です。以下の厳密なルールに従ってJSONで返してください。

【店舗名リスト（参考）】
{store_list}
※上記リストにない店舗名も読み取ってください。

【品目名の正規化ルール】
{json.dumps(item_normalization, ensure_ascii=False, indent=2)}

【重要ルール】
1. 店舗名の後に「:」または改行がある場合、その後の行は全てその店舗の注文です
2. 品目名がない行（例：「50×1」）は、直前の品目の続きとして処理してください
3. 「/」で区切られた複数の注文は、同じ店舗・同じ品目として統合してください
   - 例：「胡瓜バラ100×7 / 50×1」→ 胡瓜バラ100本×7箱 + 端数50本
4. 「胡瓜バラ」と「胡瓜3本」は別の規格として扱ってください
5. unit, boxes, remainderには「数字のみ」を入れてください

【計算ルール】
- 胡瓜(3本P): 30本/箱 → unit=30
- 胡瓜(バラ): 100本/箱（50本以上なら50本箱1、未満はバラ）→ unit=100
- 春菊: 30袋/箱 → unit=30
- 青梗菜: 20袋/箱 → unit=20
- 長ネギ(2本P): 30本/箱 → unit=30

【数量計算の例】
- 「胡瓜3本×100」→ unit=30, boxes=10, remainder=0 (30本/箱 × 10箱 = 300本 = 3本×100)
- 「胡瓜バラ100×7 / 50×1」→ unit=100, boxes=7, remainder=50 (100本/箱 × 7箱 + 50本 = 750本)
- 「春菊×50」→ unit=30, boxes=1, remainder=20 (30袋/箱 × 1箱 + 20袋 = 50袋)

【出力JSON形式】
[{{"store":"店舗名","item":"品目名","spec":"規格","unit":数字,"boxes":数字,"remainder":数字}}]

必ず全ての店舗と品目を漏れなく読み取ってください。

テキスト内容:
{text}
"""
    
    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(model="gemini-2.0-flash", contents=prompt)
            response_text = response.text
            if "```json" in response_text:
                response_text = response_text.split("```json")[1].split("```")[0]
            elif "```" in response_text:
                parts = response_text.split("```")
                for i, part in enumerate(parts):
                    if "{" in part and "[" in part:
                        response_text = part.strip()
                        break
            
            data = json.loads(response_text.strip())
            return data
        except json.JSONDecodeError as e:
            if attempt < max_retries - 1:
                st.warning(f"JSON解析エラー（試行 {attempt + 1}/{max_retries}）: {e}\n再試行します...")
                continue
            else:
                st.error(f"JSON解析エラー: {e}\n応答テキスト: {response_text[:500]}")
                return None
        except Exception as e:
            if attempt < max_retries - 1:
                st.warning(f"解析エラー（試行 {attempt + 1}/{max_retries}）: {e}\n再試行します...")
                continue
            else:
                st.error(f"解析エラー: {e}")
                return None
    
    return None

# 7. AI画像解析（Gemini 2.0 Flash）- プロンプト強化版（フォールバック用）
def get_order_data_from_image(image, max_retries=3):
    """画像を直接AIで解析（OCRが失敗した場合のフォールバック）"""
    known_stores = get_known_stores()
    item_normalization = get_item_normalization()
    
    item_list = ", ".join(item_normalization.keys())
    store_list = "、".join(known_stores)
    
    prompt = f"""画像を解析し、以下の厳密なルールに従ってJSONで返してください。

【店舗名リスト（参考）】
{store_list}
※上記リストにない店舗名も読み取ってください。

【品目名の正規化ルール】
{json.dumps(item_normalization, ensure_ascii=False, indent=2)}

【重要ルール】
1. 店舗名の後に「:」または改行がある場合、その後の行は全てその店舗の注文です
2. 品目名がない行（例：「50×1」）は、直前の品目の続きとして処理してください
3. 「/」で区切られた複数の注文は、同じ店舗・同じ品目として統合してください
   - 例：「胡瓜バラ100×7 / 50×1」→ 胡瓜バラ100本×7箱 + 端数50本
4. 「胡瓜バラ」と「胡瓜3本」は別の規格として扱ってください
5. unit, boxes, remainderには「数字のみ」を入れてください

【計算ルール】
- 胡瓜(3本P): 30本/箱 → unit=30
- 胡瓜(バラ): 100本/箱（50本以上なら50本箱1、未満はバラ）→ unit=100
- 春菊: 30袋/箱 → unit=30
- 青梗菜: 20袋/箱 → unit=20
- 長ネギ(2本P): 30本/箱 → unit=30

【数量計算の例】
- 「胡瓜3本×100」→ unit=30, boxes=10, remainder=0 (30本/箱 × 10箱 = 300本 = 3本×100)
- 「胡瓜バラ100×7 / 50×1」→ unit=100, boxes=7, remainder=50 (100本/箱 × 7箱 + 50本 = 750本)
- 「春菊×50」→ unit=30, boxes=1, remainder=20 (30袋/箱 × 1箱 + 20袋 = 50袋)

【出力JSON形式】
[{{"store":"店舗名","item":"品目名","spec":"規格","unit":数字,"boxes":数字,"remainder":数字}}]

必ず全ての店舗と品目を漏れなく読み取ってください。"""
    
    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(model="gemini-2.0-flash", contents=[prompt, image])
            response_text = response.text
            if "```json" in response_text:
                response_text = response_text.split("```json")[1].split("```")[0]
            elif "```" in response_text:
                parts = response_text.split("```")
                for i, part in enumerate(parts):
                    if "{" in part and "[" in part:
                        response_text = part.strip()
                        break
            
            data = json.loads(response_text.strip())
            return data
        except json.JSONDecodeError as e:
            if attempt < max_retries - 1:
                st.warning(f"JSON解析エラー（試行 {attempt + 1}/{max_retries}）: {e}\n再試行します...")
                continue
            else:
                st.error(f"JSON解析エラー: {e}\n応答テキスト: {response_text[:500]}")
                return None
        except Exception as e:
            if attempt < max_retries - 1:
                st.warning(f"解析エラー（試行 {attempt + 1}/{max_retries}）: {e}\n再試行します...")
                continue
            else:
                st.error(f"解析エラー: {e}")
                return None
    
    return None

# 8. ハイブリッド解析（OCR優先、失敗時は画像解析）
def get_order_data(image, use_ocr=True, max_retries=3):
    """OCR + AIハイブリッド解析（トークン節約）"""
    if use_ocr:
        try:
            # まずOCRでテキスト抽出を試みる
            with st.spinner('OCRでテキスト抽出中...'):
                ocr_text = extract_text_with_ocr(image)
            
            if ocr_text and len(ocr_text.strip()) > 10:  # 十分なテキストが抽出できた場合
                st.info(f"✅ OCRでテキスト抽出成功（{len(ocr_text)}文字）")
                with st.expander("📄 OCR抽出テキストを確認"):
                    st.text(ocr_text)
                
                # OCR結果をAIで解析（テキストのみなのでトークン消費が少ない）
                with st.spinner('AIがテキストを解析中...'):
                    order_data = get_order_data_from_text(ocr_text, max_retries)
                
                if order_data:
                    return order_data
                else:
                    st.warning("⚠️ OCRテキストの解析に失敗。画像解析に切り替えます...")
            else:
                # OCRが利用できない、または十分なテキストが抽出できなかった場合
                # 警告を出さずに静かに画像解析に切り替え
                pass
        except Exception:
            # OCR関連のエラーは無視して画像解析にフォールバック
            pass
    
    # OCRが失敗した場合、またはuse_ocr=Falseの場合、画像解析にフォールバック
    with st.spinner('AIが画像を直接解析中...'):
        return get_order_data_from_image(image, max_retries)

# 9. ルールベース検証・補完関数（自動学習対応）
def validate_and_fix_order_data(order_data, auto_learn=True):
    """AIが読み取ったデータを検証し、必要に応じて修正する（自動学習対応）"""
    if not order_data:
        return []
    
    validated_data = []
    errors = []
    learned_stores = []
    learned_items = []
    
    known_stores = get_known_stores()
    
    for i, entry in enumerate(order_data):
        # 必須フィールドのチェック
        store = entry.get('store', '').strip()
        item = entry.get('item', '').strip()
        
        # 店舗名の検証と修正（自動学習）
        validated_store = validate_store_name(store, auto_learn=auto_learn)
        if not validated_store and store:
            if auto_learn:
                validated_store = auto_learn_store(store)
                if validated_store not in learned_stores:
                    learned_stores.append(validated_store)
            else:
                errors.append(f"行{i+1}: 不明な店舗名「{store}」")
                # 最も近い店舗名を推測
                for known_store in known_stores:
                    if any(char in store for char in known_store):
                        validated_store = known_store
                        break
        
        # 品目名の正規化（自動学習）
        normalized_item = normalize_item_name(item, auto_learn=auto_learn)
        if not normalized_item and item:
            if auto_learn:
                normalized_item = auto_learn_item(item)
                if normalized_item not in learned_items:
                    learned_items.append(normalized_item)
            else:
                errors.append(f"行{i+1}: 品目名「{item}」を正規化できませんでした")
        
        # 数量の検証
        unit = safe_int(entry.get('unit', 0))
        boxes = safe_int(entry.get('boxes', 0))
        remainder = safe_int(entry.get('remainder', 0))
        
        # 数量が0の場合は警告
        if unit == 0 and boxes == 0 and remainder == 0:
            errors.append(f"行{i+1}: 数量が全て0です（店舗: {store}, 品目: {item}）")
        
        # 検証済みデータを追加
        validated_entry = {
            'store': validated_store or store,
            'item': normalized_item or item,
            'spec': entry.get('spec', '').strip(),
            'unit': unit,
            'boxes': boxes,
            'remainder': remainder
        }
        validated_data.append(validated_entry)
    
    # 自動学習の結果を表示
    if auto_learn:
        if learned_stores:
            st.success(f"✨ 新しい店舗名を学習しました: {', '.join(learned_stores)}")
        if learned_items:
            st.success(f"✨ 新しい品目名を学習しました: {', '.join(learned_items)}")
    
    # エラーがある場合は表示
    if errors:
        st.warning("⚠️ 検証で以下の問題が見つかりました:")
        for error in errors:
            st.write(f"- {error}")
    
    return validated_data

# 7. PDF作成（B5サイズ：一覧表 ＋ 伝票）
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

# 10. メイン画面レイアウト
st.title("📦 配送伝票作成システム")

# タブ作成
tab1, tab2, tab3 = st.tabs(["📸 画像解析", "📧 メール自動読み取り", "⚙️ 設定管理"])

# セッション状態の初期化
if 'order_data' not in st.session_state:
    st.session_state.order_data = None
if 'validated_data' not in st.session_state:
    st.session_state.validated_data = None
if 'image_uploaded' not in st.session_state:
    st.session_state.image_uploaded = None

# ===== タブ1: 画像解析 =====
with tab1:
    uploaded_file = st.file_uploader("注文画像をアップロード", type=['png', 'jpg', 'jpeg'])
    
    if uploaded_file:
        image = Image.open(uploaded_file)
        st.image(image, caption="アップロード画像", use_container_width=True)
        
        # 新しい画像がアップロードされた場合はセッション状態をリセット
        if st.session_state.image_uploaded != uploaded_file.name:
            st.session_state.order_data = None
            st.session_state.validated_data = None
            st.session_state.image_uploaded = uploaded_file.name
        
        col1, col2 = st.columns(2)
        
        with col1:
            if st.button("🔍 AI解析を実行", type="primary", use_container_width=True):
                with st.spinner('AIが解析中...'):
                    order_data = get_order_data(image)
                    if order_data:
                        # 検証と修正
                        validated_data = validate_and_fix_order_data(order_data)
                        st.session_state.order_data = order_data
                        st.session_state.validated_data = validated_data
                        st.success(f"✅ {len(validated_data)}件のデータを読み取りました")
                        st.rerun()
                    else:
                        st.error("解析に失敗しました。画像を確認してください。")
        
        with col2:
            if st.button("🔄 解析結果をリセット", use_container_width=True):
                st.session_state.order_data = None
                st.session_state.validated_data = None
                st.rerun()
        
        # 結果確認・編集画面
        if st.session_state.validated_data:
            st.divider()
            st.subheader("📝 解析結果の確認・編集")
            st.write("以下のテーブルでデータを確認・編集できます。編集後は「PDFを生成」ボタンを押してください。")
            
            # 編集可能なデータフレームの準備
            df_data = []
            for entry in st.session_state.validated_data:
                df_data.append({
                    '店舗名': entry.get('store', ''),
                    '品目': entry.get('item', ''),
                    '規格': entry.get('spec', ''),
                    '入数(unit)': entry.get('unit', 0),
                    '箱数(boxes)': entry.get('boxes', 0),
                    '端数(remainder)': entry.get('remainder', 0),
                    '合計数量': (entry.get('unit', 0) * entry.get('boxes', 0)) + entry.get('remainder', 0)
                })
            
            df = pd.DataFrame(df_data)
            
            # データエディタ
            edited_df = st.data_editor(
                df,
                use_container_width=True,
                num_rows="dynamic",
                column_config={
                    '店舗名': st.column_config.SelectboxColumn(
                        '店舗名',
                        help='店舗名を選択してください',
                        options=get_known_stores(),
                        required=True
                    ),
                    '品目': st.column_config.TextColumn(
                        '品目',
                        help='品目名を入力してください',
                        required=True
                    ),
                    '規格': st.column_config.TextColumn(
                        '規格',
                        help='規格を入力してください（例: 3本P、バラ）'
                    ),
                    '入数(unit)': st.column_config.NumberColumn(
                        '入数(unit)',
                        help='1箱あたりの入数',
                        min_value=0,
                        step=1
                    ),
                    '箱数(boxes)': st.column_config.NumberColumn(
                        '箱数(boxes)',
                        help='フル箱の数',
                        min_value=0,
                        step=1
                    ),
                    '端数(remainder)': st.column_config.NumberColumn(
                        '端数(remainder)',
                        help='端数の数量',
                        min_value=0,
                        step=1
                    ),
                    '合計数量': st.column_config.NumberColumn(
                        '合計数量',
                        help='自動計算: 入数×箱数+端数',
                        disabled=True
                    )
                }
            )
            
            # 編集後のデータを更新（合計数量を再計算）
            edited_df['合計数量'] = edited_df['入数(unit)'] * edited_df['箱数(boxes)'] + edited_df['端数(remainder)']
            
            # データが変更されたかチェック（合計数量の列を除く）
            df_for_compare = df.drop(columns=['合計数量'])
            edited_df_for_compare = edited_df.drop(columns=['合計数量'])
            
            if not df_for_compare.equals(edited_df_for_compare):
                updated_data = []
                for _, row in edited_df.iterrows():
                    # 品目名の正規化
                    normalized_item = normalize_item_name(row['品目'])
                    # 店舗名の検証
                    validated_store = validate_store_name(row['店舗名']) or row['店舗名']
                    
                    updated_data.append({
                        'store': validated_store,
                        'item': normalized_item,
                        'spec': str(row['規格']).strip(),
                        'unit': int(row['入数(unit)']),
                        'boxes': int(row['箱数(boxes)']),
                        'remainder': int(row['端数(remainder)'])
                    })
                
                st.session_state.validated_data = updated_data
                st.info("✅ データを更新しました。PDFを生成する場合は下のボタンを押してください。")
            
            st.divider()
            
            # PDF生成ボタン
            if st.button("📄 PDFを生成", type="primary", use_container_width=True, key="pdf_gen_tab1"):
                if st.session_state.validated_data:
                    try:
                        # 最終的な検証
                        final_data = validate_and_fix_order_data(st.session_state.validated_data)
                        
                        # PDF作成
                        pdf_bytes = create_b5_pdf(final_data)
                        st.success("✅ 伝票が完成しました！")

                        # LINE用集計の表示
                        st.subheader("📋 LINE用集計（コピー用）")
                        summary_packs = defaultdict(int)
                        for entry in final_data:
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
                            mime="application/pdf")
                    except Exception as e:
                        st.error(f"❌ PDF生成エラーが発生しました")
                        st.error(f"エラー詳細: {str(e)}")
                        with st.expander("🔍 詳細なエラー情報（開発者用）"):
                            st.code(traceback.format_exc(), language="python")
                        st.info("💡 解決方法: データを確認し、数値が正しく入力されているか確認してください。")
