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


def normalize_url(web: str | None) -> str | None:
    value = (web or "").strip()
    if not value:
        return None
    if value.startswith("//"):
        return "https:" + value
    if not _SCHEME_RE.match(value):
        value = "https://" + value
    return value


def fetch(url: str) -> requests.Response:
    try:
        resp = requests.get(
            url,
            headers=REQUEST_HEADERS,
            timeout=REQUEST_TIMEOUT,
            allow_redirects=True,
        )
    except requests.RequestException as e:
        raise FetchError(f"{url}: {e}") from e
    if resp.status_code >= 400:
        raise FetchError(f"{url}: HTTP {resp.status_code}")
    return resp
