from __future__ import annotations

import re
import os
import json
import time
import requests

from urllib.parse import urljoin, urlsplit
from bs4 import BeautifulSoup
from dataclasses import dataclass
from db import CompanyStatus

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

SYSTEM_PROMPT = """\
You extract legally required company information ("Impressum" / "Imprint") from
German-language website text.
Return ONLY a valid JSON object (no markdown fences, no commentary) that parses
with json.loads. The JSON must have exactly these keys:
{
  "company_name": "<string or null>",
  "postal_address": {
    "street": "<string or null>",
    "house_no": "<string or null>",
    "postcode": "<string or null>",
    "city": "<string or null>",
    "country": "<string or null>"
  },
  "handelsregister": "<string or null>",
  "vat_number": "<string or null>",
  "phone": "<string or null>",
  "email": "<string or null>",
  "responsible_person": "<string or null>"
}
Rules:
- Extract ONLY information actually present in the text; use null when absent.
- Do not invent or guess values.
- phone: keep the international format when possible (e.g. +49 ...).
- vat_number: German VAT ids look like DE followed by 9 digits.
- email: lowercase.
- responsible_person: the named person or role legally responsible for the
  content (e.g. Geschaeftsfuehrer, Vorstand, "inhaltlich verantwortlich").
- Output valid JSON only.
"""

DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_TIMEOUT = 60
DEEPSEEK_MAX_TOKENS = 1024
RETRY_DELAYS = (2, 4, 8)
DEFAULT_MODEL = "deepseek-chat"

RESPONSE_FIELDS = (
    "company_name",
    "handelsregister",
    "vat_number",
    "phone",
    "email",
    "responsible_person",
)
ADDRESS_FIELDS = ("street", "house_no", "postcode", "city", "country")

EMPTY_TOKENS = frozenset(
    {
        "",
        "null",
        "none",
        "n/a",
        "na",
        "n.a.",
        "nan",
        "-",
        "--",
        "nicht angegeben",
        "keine angabe",
    }
)

REQUIRED_ADDRESS_FIELDS = ("postcode", "city", "street", "house_no")


class FetchError(Exception):
    """Page is unavailable: network, timeout or HTTP 4xx/5xx (status 4)."""


class ApiError(Exception):
    """Raised when the DeepSeek API request fails."""


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


def _content_from_body(body) -> str:
    """Retrieves choices[0].message.content. Raises a ValueError if the body is unsuitable."""
    if not isinstance(body, dict):
        raise ValueError("response body is not a JSON object")
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("no choices in response")
    first = choices[0] if isinstance(choices[0], dict) else {}
    message = first.get("message") or {}
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str) or not content.strip():
        raise ValueError("empty content")
    return content.strip()


def _normalize_fields(data: dict) -> dict:
    """Formats the response to match the expected schema: missing keys -> None."""
    result = {field: data.get(field) for field in RESPONSE_FIELDS}
    address = data.get("postal_address")
    if not isinstance(address, dict):
        address = {}
    result["postal_address"] = {f: address.get(f) for f in ADDRESS_FIELDS}
    return result


def _strip_code_fences(text: str) -> str:
    """Removes ```json ... ``` if the model has added them."""
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if lines and lines[-1].strip() == "```":
        lines = lines[1:-1]
    text = "\n".join(lines).strip()
    if text.lower().startswith("json"):
        text = text[4:].strip()
    return text


def _clean(value) -> str | None:
    """Empty, space and garbage value -> None, else normalized string"""
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    value = " ".join(value.split())
    if not value or value.lower() in EMPTY_TOKENS:
        return None
    return value


def _address(data) -> dict:
    address = data.get("postal_address")
    return address if isinstance(address, dict) else {}


def has_any_field(data) -> bool:
    if not isinstance(data, dict) or not data:
        return False
    for field in RESPONSE_FIELDS:
        if _clean(data.get(field)) is not None:
            return True
    return any(_clean(v) is not None for v in _address(data).values())


def decide_status(data) -> int:
    if not has_any_field(data):
        return CompanyStatus.PARSING_FAILED  # 2
    address = _address(data)
    if any(_clean(address.get(f)) is None for f in REQUIRED_ADDRESS_FIELDS):
        return CompanyStatus.ADDRESS_INCOMPLETE  # 3
    return CompanyStatus.OK  # 0


def format_postal_address(address) -> str | None:
    street = _clean(address.get("street"))
    house_no = _clean(address.get("house_no"))
    postcode = _clean(address.get("postcode"))
    city = _clean(address.get("city"))
    country = _clean(address.get("country"))

    parts = [
        " ".join(p for p in (street, house_no) if p),
        " ".join(p for p in (postcode, city) if p),
        country,
    ]
    return ", ".join(p for p in parts if p) or None


def map_to_row(data, csv_company_name) -> dict:
    """Prepares values ​​for the `companies` columns. `Status` is determined separately via `decide_status`"""
    data = data if isinstance(data, dict) else {}
    address = _address(data)
    email = _clean(data.get("email"))

    return {
        "legal_name": _clean(data.get("company_name")) or _clean(csv_company_name),
        "postal_address": format_postal_address(address),
        "handelsregister": _clean(data.get("handelsregister")),
        "vat_number": _clean(data.get("vat_number")),
        "phone": _clean(data.get("phone")),
        "email": email.lower() if email else None,
        "responsible_person": _clean(data.get("responsible_person")),
        "raw_json": data.get("_raw", data),
    }


def extract_with_deepseek(text: str) -> dict:
    """
    Extracts Impressum data via the DeepSeek API.

    Returns a normalized dict. An empty dict indicates that nothing was
    extracted (stage 9 will set the status to 2). An ApiError signifies a
    transport or API error (status 4). The API key is never included in the
    exception message.
    """
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        raise ApiError("DEEPSEEK_API_KEY is not set. Please check your environment.")

    if not text or not text.strip():
        return {}

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": (os.environ.get("DEEPSEEK_MODEL") or "").strip() or DEFAULT_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    "Extract the Impressum data from the text below "
                    "and return JSON:\n\n" + text
                ),
            },
        ],
        "response_format": {"type": "json_object"},
        "max_tokens": DEEPSEEK_MAX_TOKENS,
        "temperature": 0,
    }

    transport_error = None
    reached_api = False

    for attempt in range(len(RETRY_DELAYS) + 1):
        try:
            response = requests.post(
                DEEPSEEK_URL,
                headers=headers,
                json=payload,
                timeout=DEEPSEEK_TIMEOUT,
            )
            if response.status_code in (401, 403):
                raise ApiError(
                    f"DeepSeek authentication failed: HTTP {response.status_code}. "
                    f"Check the API key."
                )
            response.raise_for_status()
        except ApiError:
            raise
        except requests.RequestException as e:
            transport_error = e
        else:
            reached_api = True
            try:
                content = _content_from_body(response.json())
                data = json.loads(_strip_code_fences(content))
                if not isinstance(data, dict):
                    raise ValueError("DeepSeek response is not a JSON object")
                result = _normalize_fields(data)
                result["_raw"] = data
                return result
            except ValueError:
                pass

        if attempt < len(RETRY_DELAYS):
            time.sleep(RETRY_DELAYS[attempt])

    if reached_api:
        return {}
    raise ApiError(f"DeepSeek request failed after retries: {transport_error}")


def extract_from_text(text: str) -> dict:
    if not text or not text.strip():
        return {}
    return extract_with_deepseek(text)
