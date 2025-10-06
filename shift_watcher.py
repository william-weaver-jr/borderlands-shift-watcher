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