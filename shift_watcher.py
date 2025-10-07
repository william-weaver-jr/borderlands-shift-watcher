#!/usr/bin/env python3
"""
shift_watcher.py
- Fetches a list of pages/feeds
- Extracts candidate SHIFT/Golden Key codes with regex heuristics
- Stores discovered codes in SQLite (to avoid duplicate notifications)
- Sends notification for newly found codes via a webhook or SMTP

Usage:
  python shift_watcher.py --config config.yaml
"""

import re
import sqlite3
import argparse
import time
import smtplib
import json
from email.message import EmailMessage
from typing import List
import requests
from bs4 import BeautifulSoup
import yaml

# -----------------------
# Helper functions
# -----------------------

CODE_PATTERNS = [
    # Generic alphanumeric groups separated by dashes (common pattern for codes)
    re.compile(r'\b[A-Z0-9]{4,6}(?:[-\s][A-Z0-9]{4,6}){1,4}\b'),
    # Some SHIFT codes are short strings like "SHIFT-XXXX-XXXX" or "XXXX-XXXX-XXXX"
    re.compile(r'\bSHIFT[-\s]?[A-Z0-9]{4,6}(?:[-\s][A-Z0-9]{4,6}){1,4}\b', re.IGNORECASE),
    # Single continuous tokens of length 10-20
    re.compile(r'\b[A-Z0-9]{10,20}\b'),
]

HEADERS = {
    "User-Agent": "ShiftWatcher/1.0 (+https://example.com) Python/requests"
}

def fetch_url(url: str, timeout=15) -> str:
    resp = requests.get(url, headers=HEADERS, timeout=timeout)
    resp.raise_for_status()
    return resp.text

def extract_text_from_html(html: str) -> str:
    soup = BeautifulSoup(html, 'html.parser')
    # Remove scripts/styles then get visibile text
    for s in soup(["scipt", "style", "head", "noscript"]):
        s.extract()
    return soup.get_text(separator="\n")

def find_codes_in_text(text: str) -> List[str]:
    found = set()
    # Normalize: uppercase, replace unicode dashes with '-'
    norm = text.upper().replace('\u2013', '-').replace('\u2014', '-')
    for pat in CODE_PATTERNS:
        for m in pat.findall(norm):
            token = re.sub(r'[\s]+', '-', m.strip())  # unify spacing to hyphen
            # Basic filtering: require at least one letter/digit and some dashes or length
            if len(token) >= 8:
                # remove punctuation at ends
                token = token.strip('-.,;:')
                found.add(token)
    return sorted(found)

# -----------------------
# DB (SQLite) to store seen codes
# -----------------------
def init_db(db_path="shift_codes.db"):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("""
    CREATE TABLE IF NOT EXISTS codes (
        id INTEGER PRIMARY KEY,
        code TEXT UNIQUE,
        source TEXT,
        discovered_at INTEGER
    )
    """)
    conn.commit()
    return conn

def store_new_codes(conn, codes_with_sources):
    c = conn.cursor()
    new = []
    now = int(time.time())
    for code, source in codes_with_sources:
        try:
            c.execute("INSERT INTO codes (code, source, discovered_at) VALUES (?, ?, ?)", (code, source, now))
            new.append((code, source))
        except sqlite3.IntegrityError:
            # already exists
            pass
    conn.commit()
    return new

# -----------------------
# Notification (Webhook / Email)
# -----------------------
def notify_via_webhook(webhook_url: str, new_codes):
    if not webhook_url or not new_codes:
        return
    # Example: Discord webhook JSON payload
    # For Slack, simpler JSON might work; both accept POST with JSON
    content = "New SHIFT / Golden Key codes found:\n" + "\n".join([f"{c} — {s}" for c,s in new_codes])
    payload = {"content": content}
    resp = requests.post(webhook_url, json=payload, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    return resp.status_code

def notify_via_email(smtp_cfg, new_codes):
    if not smtp_cfg or not new_codes:
        return
    msg = EmailMessage()
    msg["Subject"] = f"[ShiftWatcher] {len(new_codes)} new SHIFT codes"
    msg["From"] = smtp_cfg["from"]
    msg["To"] = ", ".join(smtp_cfg["to"])
    body = "New SHIFT / Golden Key codes discovered:\n\n" + "\n".join([f"{c} — {s}" for c,s in new_codes])
    msg.set_content(body)
    # connect and send
    with smtplib.SMTP(smtp_cfg["host"], smtp_cfg.get("port", 587), timeout=30) as s:
        if smtp_cfg.get("starttls", True):
            s.starttls()
        if smtp_cfg.get("username"):
            s.login(smtp_cfg["username"], smtp_cfg["password"])
        s.send_message(msg)
    return True

# -----------------------
# Main scanning logic
# -----------------------
def scan_sources(sources):
    results = []
    for src in sources:
        url = src.get("url")
        typ = src.get("type", "html")
        try:
            text = fetch_url(url)
            if typ == "html":
                text = extract_text_from_html(text)
            # allow optional trimming or pre-processing if provided
            codes = find_codes_in_text(text)
            for code in codes:
                results.append((code, url))
        except Exception as e:
            print(f"Error fetching {url}: {e}")
    return results

# -----------------------
# CLI / YAML config
# -----------------------
DEFAULT_CONFIG = {
    "db_path": "shift_codes.db",
    "sources": [
        # Example sources — replace/extend to taste
        {"url": "https://www.reddit.com/r/Borderlands/search?q=shift&restrict_sr=1", "type": "html"},
        {"url": "https://borderlands.com/news/", "type": "html"},
        {"url": "https://www.reddit.com/r/Borderlands/new/", "type": "html"},
        {"url": "https://www.ign.com/wikis/borderlands-4/Borderlands_4_SHiFT_Codes", "type": "html"},
        {"url": "https://www.reddit.com/r/Borderlands/comments/1nxh9lr/new_shift_codes_for_golden_keys_in_bl4/", "type": "html"},
        # You can add Gearbox forums, official twitter/X pages, third-party sites, etc.
    ],
    "notify": {
        "webhook": "",  # e.g. Discord webhook URL
        "email": {      # optional SMTP config
            "host": "",
            "port": 587,
            "starttls": True,
            "from": "",
            "to": [""],
            "username": "",
            "password": ""
        }
    }
}

def load_config(path):
    """
    Load configuration from a YAML file and merge it with the default configuration.

    Args:
        path (str): Path to the YAML configuration file.

    Returns:
        dict: Merged configuration dictionary.
    """
    with open(path, "r") as f:
        cfg = yaml.safe_load(f)
    # merge defaults
    conf = DEFAULT_CONFIG.copy()
    conf.update(cfg or {})
    return conf

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", "-c", default="config.yaml")
    args = parser.parse_args()

    cfg = load_config(args.config)
    conn = init_db(cfg.get("db_path", "shift_codes.db"))

    print("Scanning sources...")
    found = scan_sources(cfg["sources"])
    # deduplicate by code (take first source)
    by_code = {}
    for code, src in found:
        if code not in by_code:
            by_code[code] = src
    pairs = sorted([(c, by_code[c]) for c in by_code])

    new = store_new_codes(conn, pairs)
    if new:
        print(f"Found {len(new)} new code(s). Notifying...")
        # send notifications
        webhook = cfg.get("notify", {}).get("webhook")
        try:
            if webhook:
                notify_via_webhook(webhook, new)
        except Exception as e:
            print("Webhook notify failed:", e)

        smtp_cfg = cfg.get("notify", {}).get("email")
        try:
            if smtp_cfg and smtp_cfg.get("host"):
                notify_via_email(smtp_cfg, new)
        except Exception as e:
            print("Email notify failed:", e)

    else:
        print("No new codes found.")

if __name__ == "__main__":
    main()