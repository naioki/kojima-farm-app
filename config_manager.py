"""
設定管理モジュール
店舗名・品目名をJSONファイルで動的に管理
"""
import json
import os
from pathlib import Path
from typing import List, Dict, Optional

CONFIG_DIR = Path("config")
STORES_FILE = CONFIG_DIR / "stores.json"
ITEMS_FILE = CONFIG_DIR / "items.json"

# デフォルト値
DEFAULT_STORES = ["鎌ケ谷", "五香", "八柱", "青葉台", "咲が丘", "習志野台", "八千代台"]

DEFAULT_ITEMS = {
    "青梗菜": ["青梗菜", "チンゲン菜", "ちんげん菜", "チンゲンサイ", "ちんげんさい"],
    "胡瓜": ["胡瓜", "きゅうり", "キュウリ"],
    "長ネギ": ["長ネギ", "ネギ", "ねぎ", "長ねぎ"],
    "春菊": ["春菊", "しゅんぎく", "シュンギク"]
}

def ensure_config_dir():
    """設定ディレクトリが存在することを確認"""
    CONFIG_DIR.mkdir(exist_ok=True)

def load_stores() -> List[str]:
    """店舗名リストを読み込む"""
    ensure_config_dir()
    if STORES_FILE.exists():
        try:
            with open(STORES_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data.get('stores', DEFAULT_STORES)
        except Exception:
            return DEFAULT_STORES
    else:
        # デフォルト値を保存
        save_stores(DEFAULT_STORES)
        return DEFAULT_STORES

def save_stores(stores: List[str]):
    """店舗名リストを保存"""
    ensure_config_dir()
    with open(STORES_FILE, 'w', encoding='utf-8') as f:
        json.dump({'stores': stores}, f, ensure_ascii=False, indent=2)

def add_store(store_name: str) -> bool:
    """新しい店舗名を追加"""
    stores = load_stores()
    if store_name not in stores:
        stores.append(store_name)
        save_stores(stores)
        return True
    return False

def remove_store(store_name: str) -> bool:
    """店舗名を削除"""
    stores = load_stores()
    if store_name in stores:
        stores.remove(store_name)
        save_stores(stores)
        return True
    return False

def load_items() -> Dict[str, List[str]]:
    """品目名正規化マップを読み込む"""
    ensure_config_dir()
    if ITEMS_FILE.exists():
        try:
            with open(ITEMS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return DEFAULT_ITEMS
    else:
        # デフォルト値を保存
        save_items(DEFAULT_ITEMS)
        return DEFAULT_ITEMS

def save_items(items: Dict[str, List[str]]):
    """品目名正規化マップを保存"""
    ensure_config_dir()
    with open(ITEMS_FILE, 'w', encoding='utf-8') as f:
        json.dump(items, f, ensure_ascii=False, indent=2)

def add_item_variant(normalized_name: str, variant: str):
    """品目のバリアント（表記ゆれ）を追加"""
    items = load_items()
    if normalized_name not in items:
        items[normalized_name] = []
    if variant not in items[normalized_name]:
        items[normalized_name].append(variant)
    save_items(items)

def add_new_item(normalized_name: str, variants: Optional[List[str]] = None):
    """新しい品目を追加"""
    items = load_items()
    if normalized_name not in items:
        items[normalized_name] = variants or [normalized_name]
        save_items(items)
        return True
    return False

def remove_item(normalized_name: str) -> bool:
    """品目を削除"""
    items = load_items()
    if normalized_name in items:
        del items[normalized_name]
        save_items(items)
        return True
    return False

def auto_learn_store(store_name: str) -> str:
    """新しい店舗名を自動学習（既存のものと似ていれば統合、そうでなければ追加）"""
    stores = load_stores()
    store_name = store_name.strip()
    
    # 既存の店舗名と類似チェック
    for existing_store in stores:
        if existing_store in store_name or store_name in existing_store:
            return existing_store  # 既存の店舗名を返す
    
    # 新しい店舗名として追加
    if store_name and store_name not in stores:
        add_store(store_name)
    return store_name

def auto_learn_item(item_name: str) -> str:
    """新しい品目名を自動学習（正規化して追加）"""
    items = load_items()
    item_name = item_name.strip()
    
    # 既存の品目名と照合
    for normalized, variants in items.items():
        if item_name in variants or any(variant in item_name for variant in variants):
            return normalized
    
    # 新しい品目として追加（正規化名はそのまま使用）
    if item_name:
        add_new_item(item_name, [item_name])
    return item_name
