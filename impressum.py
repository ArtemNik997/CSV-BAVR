from __future__ import annotations

import re
from urllib.parse import urljoin, urlsplit

import requests
from bs4 import BeautifulSoup

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
REQUEST_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
}
REQUEST_TIMEOUT = (10, 30)  # (connect, read), секунд

IMPRESSUM_WORDS = ("impressum", "imprint")
SKIP_HREF_PREFIXES = ("mailto:", "tel:", "javascript:", "data:", "sms:")
REMOVED_TAGS = ("script", "style", "noscript", "svg", "nav", "header", "footer", "form")

_SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.\-]*://", re.IGNORECASE)


class FetchError(Exception):
    """Page is unavailable: network, timout or HTTP 4xx/5xx (status 4)."""
