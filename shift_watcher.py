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