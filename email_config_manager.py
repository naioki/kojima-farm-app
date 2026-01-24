"""
メール設定管理モジュール
メール設定を安全に保存・読み込み
"""
import json
import os
from pathlib import Path
from typing import Optional, Dict

CONFIG_DIR = Path("config")
EMAIL_CONFIG_FILE = CONFIG_DIR / "email_config.json"

# IMAPサーバーの自動判定マッピング
IMAP_SERVER_MAP = {
    "gmail.com": "imap.gmail.com",
    "googlemail.com": "imap.gmail.com",
    "outlook.com": "outlook.office365.com",
    "hotmail.com": "outlook.office365.com",
    "live.com": "outlook.office365.com",
    "msn.com": "outlook.office365.com",
    "yahoo.co.jp": "imap.mail.yahoo.com",
    "yahoo.com": "imap.mail.yahoo.com",
    "icloud.com": "imap.mail.me.com",
    "me.com": "imap.mail.me.com",
    "mac.com": "imap.mail.me.com",
    "aol.com": "imap.aol.com"
}

def detect_imap_server(email_address: str) -> str:
    """メールアドレスからIMAPサーバーを自動判定"""
    if not email_address:
        return "imap.gmail.com"  # デフォルト
    
    domain = email_address.split("@")[-1].lower() if "@" in email_address else ""
    
    # 完全一致
    if domain in IMAP_SERVER_MAP:
        return IMAP_SERVER_MAP[domain]
    
    # 部分一致
    for key, server in IMAP_SERVER_MAP.items():
        if key in domain:
            return server
    
    return "imap.gmail.com"  # デフォルト

def ensure_config_dir():
    """設定ディレクトリが存在することを確認"""
    CONFIG_DIR.mkdir(exist_ok=True)

def load_email_config(st_secrets=None) -> Dict:
    """メール設定を読み込む（Secrets優先、次にファイル、最後にデフォルト）"""
    # 1. Streamlit Secretsから読み込み（最優先）
    if st_secrets:
        try:
            secrets = st_secrets.get("email", {})
            if secrets and secrets.get("email_address"):
                return {
                    "imap_server": secrets.get("imap_server", ""),
                    "email_address": secrets.get("email_address", ""),
                    "sender_email": secrets.get("sender_email", ""),
                    "days_back": secrets.get("days_back", 1)
                }
        except:
            pass
    
    # 2. 設定ファイルから読み込み
    ensure_config_dir()
    if EMAIL_CONFIG_FILE.exists():
        try:
            with open(EMAIL_CONFIG_FILE, 'r', encoding='utf-8') as f:
                config = json.load(f)
                # パスワードは保存しない（セキュリティ）
                return {
                    "imap_server": config.get("imap_server", ""),
                    "email_address": config.get("email_address", ""),
                    "sender_email": config.get("sender_email", ""),
                    "days_back": config.get("days_back", 1)
                }
        except:
            pass
    
    # 3. デフォルト値
    return {
        "imap_server": "",
        "email_address": "",
        "sender_email": "",
        "days_back": 1
    }

def save_email_config(imap_server: str, email_address: str, sender_email: str, days_back: int, save_to_file: bool = False):
    """メール設定を保存（パスワードは保存しない）"""
    if not save_to_file:
        return  # セキュリティのため、デフォルトではファイルに保存しない
    
    ensure_config_dir()
    config = {
        "imap_server": imap_server,
        "email_address": email_address,
        "sender_email": sender_email,
        "days_back": days_back
        # パスワードは保存しない
    }
    
    with open(EMAIL_CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
