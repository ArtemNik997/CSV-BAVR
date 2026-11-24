from __future__ import annotations

import re
from urllib.parse import urljoin, urlsplit

import requests
from bs4 import BeautifulSoup
from dataclasses import dataclass

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
REQUEST_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
}
REQUEST_TIMEOUT = (10, 30)

IMPRESSUM_WORDS = ("impressum", "imprint")
SKIP_HREF_PREFIXES = ("mailto:", "tel:", "javascript:", "data:", "sms:")
REMOVED_TAGS = ("script", "style", "noscript", "svg", "nav", "header", "footer", "form")

_SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.\-]*://", re.IGNORECASE)

MAX_TEXT_CHARS = 8000
HEAD_MARGIN = 100
TAIL_MARGIN = 400
TIGHT_HEAD_MARGIN = 0
TIGHT_TAIL_MARGIN = 100
KEYWORD_CONTEXT = 2
CUT_MARK = "\n...\n"

_NOISE_LINE_RE = re.compile(
    r"(?:datenschutz(?:erklärung|hinweise?)?|agb|cookies?"
    r"|cookie[- ]?(?:einstellungen|hinweis|richtlinie)"
    r"|(?:alle\s+)?cookies?\s+(?:akzeptieren|zulassen|ablehnen)"
    r"|menü|menu|startseite|home|kontakt|sitemap"
    r"|nach\s+oben|zum\s+inhalt\s+springen|suche|search"
    r"|deutsch|english|englisch|français|francais|español|italiano"
    r"|nederlands|polski|русский|čeština)",
    re.IGNORECASE,
)
_COPYRIGHT_RE = re.compile(r"(?:©|\(c\)|copyright\b)", re.IGNORECASE)

HEAD_ANCHOR_RE = re.compile(
    r"\bimpressum\b|\bimprint\b|\bdiensteanbieter\b|\banbieter\b"
    r"|angaben\s+gem(?:ä|ae)(?:ß|ss)\s*§\s*5\s*(?:tmg|ddg)",
    re.IGNORECASE,
)

_PHONE_RE = re.compile(r"(?:\+49|0049)[\d\s\-/()]{6,}")
_VAT_RE = re.compile(r"\bDE\s?\d{9}\b")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_REGISTRY_RE = re.compile(
    r"\bHR[AB]\s?\d+|\b(?:Registergericht|Amtsgericht)\b", re.IGNORECASE
)
_RESPONSIBLE_RE = re.compile(
    r"verantwortlich|vertreten\s+durch|gesch(?:ä|ae)ftsf(?:ü|ue)hrer", re.IGNORECASE
)
STRONG_SIGNAL_RES = (_PHONE_RE, _VAT_RE, _EMAIL_RE, _REGISTRY_RE)
TAIL_ANCHOR_RES = STRONG_SIGNAL_RES + (_RESPONSIBLE_RE,)

KEYWORD_RE = re.compile(
    r"stra(?:ß|ss)e|str\.|\b(?:tel|telefon|fon|fax|e-?mail|ust|hrb|hra)\b"
    r"|\w*register\w*|amtsgericht|vertreten|verantwortlich|umsatzsteuer\w*"
    r"|gesch(?:ä|ae)ftsf(?:ü|ue)hrer|\bde\s?\d{9}\b|@|\+49|0049|\b\d{5}\b",
    re.IGNORECASE,
)


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


def _resolve(base_url: str, href: str | None) -> str | None:
    href = (href or "").strip()
    if not href or href.startswith("#") or href.lower().startswith(SKIP_HREF_PREFIXES):
        return None
    url = urljoin(base_url, href)
    return url if urlsplit(url).scheme in ("http", "https") else None


def find_impressum_link(home_html: str | bytes, base_url: str) -> str | None:
    soup = BeautifulSoup(home_html, "lxml")

    base_tag = soup.find("base", href=True)
    if base_tag:
        base_url = urljoin(base_url, base_tag["href"].strip())

    candidates = []
    for a in soup.find_all("a", href=True):
        url = _resolve(base_url, a["href"])
        if url:
            candidates.append((a, url))

    for a, url in candidates:
        text = " ".join(a.get_text(" ").split()).lower()
        if text in IMPRESSUM_WORDS:
            return url

    for a, url in candidates:
        if any(word in a["href"].lower() for word in IMPRESSUM_WORDS):
            return url
    return None


def extract_visible_text(html: str | bytes) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(REMOVED_TAGS):
        tag.decompose()
    text = soup.get_text(separator="\n")
    lines = (" ".join(line.split()) for line in text.splitlines())
    return "\n".join(line for line in lines if line)


def estimate_tokens(text: str) -> int:
    return len(text) // 4


@dataclass(frozen=True)
class OptimizedText:
    text: str
    strategy: str
    chars_before: int
    head_cut: int = 0
    tail_cut: int = 0

    @property
    def chars_after(self) -> int:
        return len(self.text)

    @property
    def tokens_before(self) -> int:
        return self.chars_before // 4

    @property
    def tokens_after(self) -> int:
        return self.chars_after // 4

    @property
    def tokens_saved(self) -> int:
        return self.tokens_before - self.tokens_after

    def summary(self, company: str) -> str:
        return (
            f"{company}: {self.chars_before} -> {self.chars_after} chars "
            f"(~{self.tokens_before} -> ~{self.tokens_after} tokens), "
            f"head cut {self.head_cut}, tail cut {self.tail_cut}, "
            f"strategy={self.strategy}"
        )


def strip_noise_lines(text: str) -> str:
    kept = []
    for line in text.split("\n"):
        line = line.strip()
        if not line or _NOISE_LINE_RE.fullmatch(line) or _COPYRIGHT_RE.match(line):
            continue
        kept.append(line)
    return "\n".join(kept)


def keyword_filter(text: str, context: int = KEYWORD_CONTEXT) -> str:
    lines = text.split("\n")
    keep = set()
    for i, line in enumerate(lines):
        if KEYWORD_RE.search(line):
            keep.update(range(max(0, i - context), min(len(lines), i + context + 1)))
    return "\n".join(lines[i] for i in sorted(keep))


def head_tail_window(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    n = max(1, (max_chars - len(CUT_MARK)) // 2)
    return text[:n] + CUT_MARK + text[-n:]


def _first_head_anchor(text: str) -> int | None:
    m = HEAD_ANCHOR_RE.search(text)
    return m.start() if m else None


def _last_tail_end(text: str) -> int | None:
    end = None
    for pattern in TAIL_ANCHOR_RES:
        for m in pattern.finditer(text):
            if end is None or m.end() > end:
                end = m.end()
    return end


def _is_valid_cut(body: str, need_head_anchor: bool) -> bool:
    if need_head_anchor and not HEAD_ANCHOR_RE.search(body):
        return False
    return any(p.search(body) for p in STRONG_SIGNAL_RES)


def optimize_text(text: str, max_chars: int = MAX_TEXT_CHARS) -> OptimizedText:
    before = len(text)
    if before <= max_chars:
        return OptimizedText(text, "unchanged", before)

    cleaned = strip_noise_lines(text)
    if len(cleaned) <= max_chars:
        return OptimizedText(cleaned, "noise-only", before)

    head_pos = _first_head_anchor(cleaned)
    tail_end = _last_tail_end(cleaned)

    def bounds(head_margin, tail_margin):
        start = max(0, head_pos - head_margin) if head_pos is not None else 0
        end = (
            min(len(cleaned), tail_end + tail_margin)
            if tail_end is not None
            else len(cleaned)
        )
        return start, end

    strategy = "sandwich"
    start, end = bounds(HEAD_MARGIN, TAIL_MARGIN)
    if end - start > max_chars:
        start, end = bounds(TIGHT_HEAD_MARGIN, TIGHT_TAIL_MARGIN)
        strategy = "sandwich-tight"
    body = cleaned[start:end]

    if not _is_valid_cut(body, need_head_anchor=head_pos is not None):
        fallback, strategy = cleaned, "cleaned"
        if len(fallback) > max_chars:
            fallback, strategy = head_tail_window(fallback, max_chars), "cleaned+window"
        return OptimizedText(fallback, strategy, before)

    if len(body) > max_chars:
        body = keyword_filter(body)
        strategy += "+keywords"
    if len(body) > max_chars:
        body = head_tail_window(body, max_chars)
        strategy += "+window"
    return OptimizedText(body, strategy, before, start, len(cleaned) - end)
