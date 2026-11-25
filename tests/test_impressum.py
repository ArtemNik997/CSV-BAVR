import pytest
import requests

import json

import impressum
from impressum import (
    FetchError,
    extract_visible_text,
    fetch,
    find_impressum_link,
    normalize_url,
    has_any_field,
    decide_status,
    map_to_row,
    format_postal_address,
    decode_cf_email,
    RESPONSE_FIELDS,
    ADDRESS_FIELDS,
)
from db import CompanyStatus

HOME = "https://www.example.de/"
LEGAL = "https://www.example.de/legal"


def make_response(status=200, content=b"<html></html>", url="https://example.de/"):
    resp = requests.Response()
    resp.status_code = status
    resp._content = content
    resp.url = url
    return resp


class FakeGet:
    def __init__(self):
        self.calls = []
        self.result = make_response()

    def __call__(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@pytest.fixture
def fake_get(monkeypatch):
    fake = FakeGet()
    monkeypatch.setattr(impressum.requests, "get", fake)
    return fake


def page(*anchors, head=""):
    return "<html><head>%s</head><body>%s</body></html>" % (head, "".join(anchors))


# ------------------------------------------------------------ constants


def test_constants_match_the_task():
    assert impressum.REQUEST_TIMEOUT == (10, 30)
    assert impressum.IMPRESSUM_WORDS == ("impressum", "imprint")
    assert set(impressum.REMOVED_TAGS) == {
        "script",
        "style",
        "noscript",
        "svg",
        "nav",
        "header",
        "footer",
        "form",
    }


# --------------------------------------------------------- normalize_url


@pytest.mark.parametrize("value", [None, "", "   ", "\n", "\t "])
def test_blank_url_gives_none(value):
    assert normalize_url(value) is None


@pytest.mark.parametrize(
    "value, expected",
    [
        ("www.example.de", "https://www.example.de"),
        ("example.de", "https://example.de"),
        ("example.de/path?x=1", "https://example.de/path?x=1"),
        ("www.example.de:8080/a", "https://www.example.de:8080/a"),
        ("localhost:8080", "https://localhost:8080"),
        ("  www.example.de  ", "https://www.example.de"),
        ("//www.example.de", "https://www.example.de"),
        ("  //www.example.de", "https://www.example.de"),
        ("https://www.example.de", "https://www.example.de"),
        ("http://www.example.de", "http://www.example.de"),
        ("HTTPS://WWW.EXAMPLE.DE", "HTTPS://WWW.EXAMPLE.DE"),
        ("Http://Example.de/A", "Http://Example.de/A"),
        ("  https://x.de/ ", "https://x.de/"),
        ("ftp://files.example.de", "ftp://files.example.de"),
    ],
)
def test_normalize_url(value, expected):
    assert normalize_url(value) == expected


# ----------------------------------------------------------------- fetch


def test_fetch_returns_the_response(fake_get):
    fake_get.result = make_response(200, b"ok", "https://example.de/final")
    resp = fetch("https://example.de")
    assert resp is fake_get.result
    assert resp.url == "https://example.de/final"
    assert resp.content == b"ok"


def test_fetch_sends_browser_headers_timeout_and_follows_redirects(fake_get):
    fetch("https://example.de")
    url, kwargs = fake_get.calls[0]
    assert url == "https://example.de"
    assert kwargs["timeout"] == (10, 30)
    assert kwargs["allow_redirects"] is True
    assert "Mozilla" in kwargs["headers"]["User-Agent"]
    assert "python-requests" not in kwargs["headers"]["User-Agent"]
    assert kwargs["headers"]["Accept-Language"].startswith("de")


@pytest.mark.parametrize("status", [400, 403, 404, 429, 500, 503])
def test_fetch_http_errors_raise_fetch_error(fake_get, status):
    fake_get.result = make_response(status)
    with pytest.raises(FetchError) as exc:
        fetch("https://example.de/missing")
    assert str(status) in str(exc.value)
    assert "https://example.de/missing" in str(exc.value)


@pytest.mark.parametrize(
    "error",
    [
        requests.ConnectionError("dns failure"),
        requests.Timeout("too slow"),
        requests.exceptions.SSLError("bad certificate"),
        requests.TooManyRedirects("loop"),
        requests.exceptions.InvalidSchema("no adapter"),
    ],
    ids=lambda e: type(e).__name__,
)
def test_fetch_network_errors_raise_fetch_error(fake_get, error):
    fake_get.result = error
    with pytest.raises(FetchError) as exc:
        fetch("https://example.de")
    assert exc.value.__cause__ is error
    assert "https://example.de" in str(exc.value)


# ----------------------------------------------- find_impressum_link: text


@pytest.mark.parametrize(
    "anchor",
    [
        '<a href="/legal">Impressum</a>',
        '<a href="/legal">Imprint</a>',
        '<a href="/legal">IMPRESSUM</a>',
        '<a href="/legal">iMpReSsUm</a>',
        '<a href="/legal">  Impressum  </a>',
        '<a href="/legal">\n   Impressum\n</a>',
        '<a href="/legal">&nbsp;Impressum&nbsp;</a>',
        '<a href="/legal"><span>Impressum</span></a>',
        '<a href="/legal"><span> Impressum </span></a>',
    ],
)
def test_link_is_found_by_visible_text(anchor):
    assert find_impressum_link(page(anchor), HOME) == LEGAL


def test_text_match_beats_an_earlier_href_only_match():
    html = page(
        '<a href="/impressum-old">Rechtliches</a>',
        '<a href="/legal">Impressum</a>',
    )
    assert find_impressum_link(html, HOME) == LEGAL


def test_first_text_match_wins():
    html = page(
        '<a href="/first">Impressum</a>',
        '<a href="/second">Impressum</a>',
    )
    assert find_impressum_link(html, HOME) == "https://www.example.de/first"


@pytest.mark.parametrize(
    "text",
    ["Impressum & Datenschutz", "Impressum anzeigen", "Mein Impressum", "Impress"],
)
def test_text_must_match_exactly(text):
    html = page('<a href="/legal">%s</a>' % text)
    assert find_impressum_link(html, HOME) is None


def test_title_attribute_is_not_used():
    html = page('<a href="/legal" title="Impressum">Rechtliches</a>')
    assert find_impressum_link(html, HOME) is None


# ------------------------------------------------ find_impressum_link: href


@pytest.mark.parametrize(
    "anchor, expected",
    [
        (
            '<a href="/de/impressum.html"><i class="icon"></i></a>',
            "https://www.example.de/de/impressum.html",
        ),
        ('<a href="/IMPRESSUM">Legal</a>', "https://www.example.de/IMPRESSUM"),
        (
            '<a href="/rechtliches/imprint">Rechtliches</a>',
            "https://www.example.de/rechtliches/imprint",
        ),
        (
            '<a href="/impressum-datenschutz">Impressum &amp; Datenschutz</a>',
            "https://www.example.de/impressum-datenschutz",
        ),
    ],
)
def test_link_is_found_by_href_when_text_does_not_match(anchor, expected):
    assert find_impressum_link(page(anchor), HOME) == expected


def test_relative_links_on_a_site_whose_domain_contains_the_word():
    html = page('<a href="/kontakt">Kontakt</a>', '<a href="/preise">Preise</a>')
    assert find_impressum_link(html, "https://www.imprint-print.de/") is None


def test_anchor_without_href_is_ignored():
    assert find_impressum_link(page('<a name="impressum">Impressum</a>'), HOME) is None


# ---------------------------------------------- find_impressum_link: URLs


@pytest.mark.parametrize(
    "base, href, expected",
    [
        ("https://x.de/", "/impressum", "https://x.de/impressum"),
        ("https://x.de/a/b/", "/impressum", "https://x.de/impressum"),
        ("https://x.de/a/b/", "impressum", "https://x.de/a/b/impressum"),
        ("https://x.de/a/b", "impressum", "https://x.de/a/impressum"),
        ("https://x.de/a/b/", "../impressum.html", "https://x.de/a/impressum.html"),
        (
            "https://x.de/a/",
            "//cdn.example.org/impressum",
            "https://cdn.example.org/impressum",
        ),
        ("http://x.de/", "impressum.html#top", "http://x.de/impressum.html#top"),
        ("https://x.de/", "https://other.org/imprint", "https://other.org/imprint"),
        ("https://x.de/?lang=de", "?page=impressum", "https://x.de/?page=impressum"),
    ],
)
def test_relative_and_absolute_hrefs_are_resolved(base, href, expected):
    html = page('<a href="%s">Impressum</a>' % href)
    assert find_impressum_link(html, base) == expected


@pytest.mark.parametrize(
    "href",
    [
        "mailto:impressum@example.de",
        "tel:+4930123",
        "javascript:void(0)",
        "JavaScript:openImpressum()",
        "#impressum",
        "",
        "   ",
        "data:text/html,impressum",
        "sms:123",
        "ftp://files.example.de/impressum",
        "file:///etc/impressum",
    ],
)
def test_unusable_hrefs_are_skipped(href):
    html = page('<a href="%s">Impressum</a>' % href)
    assert find_impressum_link(html, HOME) is None


def test_unusable_link_is_skipped_in_favour_of_a_valid_one():
    html = page(
        '<a href="mailto:impressum@example.de">Impressum</a>',
        '<a href="/impressum">Impressum</a>',
    )
    assert find_impressum_link(html, HOME) == "https://www.example.de/impressum"


def test_base_tag_with_absolute_url_is_honoured():
    html = page(
        '<a href="impressum">Impressum</a>',
        head='<base href="https://cdn.example.de/site/">',
    )
    assert find_impressum_link(html, HOME) == "https://cdn.example.de/site/impressum"


def test_base_tag_with_relative_url_is_honoured():
    html = page('<a href="impressum">Impressum</a>', head='<base href="/shop/">')
    assert find_impressum_link(html, HOME) == "https://www.example.de/shop/impressum"


# ------------------------------------------ find_impressum_link: no result


@pytest.mark.parametrize(
    "html",
    [
        "",
        "   ",
        "<html></html>",
        "<p>no links here</p>",
        "<html",
        page('<a href="/kontakt">Kontakt</a>', '<a href="/agb">AGB</a>'),
    ],
)
def test_no_link_gives_none(html):
    assert find_impressum_link(html, HOME) is None


def test_bytes_input_is_accepted():
    html = page('<a href="/legal">Impressum</a>').encode("utf-8")
    assert find_impressum_link(html, HOME) == LEGAL


# --------------------------------------------------- extract_visible_text


@pytest.mark.parametrize(
    "tag",
    ["script", "style", "noscript", "svg", "nav", "header", "footer", "form"],
)
def test_unwanted_tags_are_removed(tag):
    html = "<html><body><%s>SECRET</%s><p>Visible</p></body></html>" % (tag, tag)
    text = extract_visible_text(html)
    assert "SECRET" not in text
    assert "Visible" in text


def test_nested_unwanted_content_is_removed():
    html = (
        "<html><body><footer><nav><a href='/x'>Datenschutz</a></nav></footer>"
        "<p>ACME GmbH</p></body></html>"
    )
    assert extract_visible_text(html) == "ACME GmbH"


def test_head_scripts_and_styles_are_removed():
    html = (
        "<html><head><script>var secret = 1;</script><style>.a{color:red}</style>"
        "</head><body><p>Visible</p></body></html>"
    )
    text = extract_visible_text(html)
    assert "secret" not in text and "color" not in text
    assert "Visible" in text


def test_block_elements_become_separate_lines():
    html = (
        "<html><body><h1>Impressum</h1><p>Angaben gemäß § 5 TMG</p>"
        "<p>ACME GmbH</p></body></html>"
    )
    assert extract_visible_text(html) == "Impressum\nAngaben gemäß § 5 TMG\nACME GmbH"


def test_br_separated_address_becomes_lines():
    html = "<p>ACME GmbH<br>Main Street 12<br>10115 Berlin</p>"
    assert extract_visible_text(html) == "ACME GmbH\nMain Street 12\n10115 Berlin"


def test_inline_tags_split_into_lines():
    html = '<p>Telefon: <a href="tel:+4930123">+49 30 123</a></p>'
    assert extract_visible_text(html) == "Telefon:\n+49 30 123"


def test_whitespace_is_normalized_and_blank_lines_removed():
    html = """
    <html><body>
       <div>
          <p>  ACME   GmbH  </p>

          <p>Main\tStreet   12</p>
       </div>
    </body></html>"""
    assert extract_visible_text(html) == "ACME GmbH\nMain Street 12"


def test_non_breaking_spaces_are_collapsed():
    assert (
        extract_visible_text("<p>Tel.&nbsp;+49&nbsp;30&nbsp;123</p>")
        == "Tel. +49 30 123"
    )


def test_html_comments_are_excluded():
    html = "<p>Visible</p><!-- SECRET comment -->"
    assert extract_visible_text(html) == "Visible"


@pytest.mark.parametrize(
    "html",
    [
        "",
        "   ",
        "\n\n",
        "<html></html>",
        "<footer>only footer</footer>",
        "<script>x</script>",
    ],
)
def test_nothing_visible_gives_empty_string(html):
    assert extract_visible_text(html) == ""


def test_result_is_plain_text_without_markup():
    text = extract_visible_text('<p class="x">ACME <b>GmbH</b></p>')
    assert "<" not in text and ">" not in text


# ------------------------------------------------------- encodings (bytes)


def test_utf8_bytes_with_meta_charset():
    html = (
        '<html><head><meta charset="utf-8"></head>'
        "<body><p>Geschäftsführer: Müller</p></body></html>"
    ).encode("utf-8")
    assert "Geschäftsführer: Müller" in extract_visible_text(html)


def test_latin1_bytes_with_meta_charset():
    html = (
        '<html><head><meta charset="iso-8859-1"></head>'
        "<body><p>Geschäftsführer: Müller</p></body></html>"
    ).encode("latin-1")
    assert "Geschäftsführer: Müller" in extract_visible_text(html)


def test_utf8_bytes_with_bom():
    html = b"\xef\xbb\xbf" + "<html><body><p>Geschäftsführer</p></body></html>".encode(
        "utf-8"
    )
    assert "Geschäftsführer" in extract_visible_text(html)


def test_str_input_is_accepted():
    assert extract_visible_text("<p>Geschäftsführer</p>") == "Geschäftsführer"


# ------------------------------------------------------------ whole flow


def test_home_to_impressum_flow(monkeypatch):
    pages = {
        "https://www.example.de": make_response(
            content=b'<html><body><a href="/impressum">Impressum</a></body></html>',
            url="https://www.example.de/",
        ),
        "https://www.example.de/impressum": make_response(
            content=(
                "<html><body><nav>Menu</nav><h1>Impressum</h1>"
                "<p>ACME GmbH</p><footer>Datenschutz</footer></body></html>"
            ).encode("utf-8"),
            url="https://www.example.de/impressum",
        ),
    }
    monkeypatch.setattr(impressum.requests, "get", lambda url, **kw: pages[url])

    home = fetch(normalize_url("www.example.de"))
    link = find_impressum_link(home.content, home.url)
    assert link == "https://www.example.de/impressum"

    text = extract_visible_text(fetch(link).content)
    assert text == "Impressum\nACME GmbH"


def test_relative_link_is_resolved_against_the_final_url_after_redirect(monkeypatch):
    home = make_response(
        content=b'<html><body><a href="impressum">Impressum</a></body></html>',
        url="https://www.example.de/de/",
    )
    monkeypatch.setattr(impressum.requests, "get", lambda url, **kw: home)

    resp = fetch("http://example.de")
    assert (
        find_impressum_link(resp.content, resp.url)
        == "https://www.example.de/de/impressum"
    )


# ------------------------------------------------------------ mapping

FULL_ADDRESS = {
    "street": "Main Street",
    "house_no": "12",
    "postcode": "10115",
    "city": "Berlin",
    "country": "Germany",
}


@pytest.fixture
def full_data():
    return {
        "company_name": "ACME GmbH",
        "postal_address": dict(FULL_ADDRESS),
        "handelsregister": "HRB 12345",
        "vat_number": "DE123456789",
        "phone": "+49 30 123456",
        "email": "INFO@ACME.DE",
        "responsible_person": "Max Mustermann",
    }


# ------------------------------------------------------- decide_status


def test_complete_data_is_ok():
    assert decide_status({"postal_address": FULL_ADDRESS}) == CompanyStatus.OK


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"company_name": None},
        {"company_name": "null"},
        {"phone": "N/A"},
        {"company_name": "   "},
        {"_raw": {"company_name": "ACME"}},
    ],
    ids=["empty", "all-none", "null-string", "na-string", "whitespace", "raw-only"],
)
def test_no_detected_fields_is_parsing_failed(data):
    assert decide_status(data) == CompanyStatus.PARSING_FAILED


@pytest.mark.parametrize("missing", ["postcode", "city", "street", "house_no"])
def test_any_missing_address_part_is_incomplete(missing):
    address = {k: v for k, v in FULL_ADDRESS.items() if k != missing}
    assert (
        decide_status({"postal_address": address}) == CompanyStatus.ADDRESS_INCOMPLETE
    )


def test_country_is_not_required_for_completeness():
    address = {k: v for k, v in FULL_ADDRESS.items() if k != "country"}
    assert decide_status({"postal_address": address}) == CompanyStatus.OK


def test_missing_vat_phone_person_do_not_downgrade():
    data = {
        "postal_address": FULL_ADDRESS,
        "vat_number": None,
        "phone": None,
        "responsible_person": None,
    }
    assert decide_status(data) == CompanyStatus.OK


def test_only_phone_is_incomplete_not_failed():
    assert decide_status({"phone": "+49 30 1"}) == CompanyStatus.ADDRESS_INCOMPLETE


def test_only_country_is_incomplete():
    assert (
        decide_status({"postal_address": {"country": "Germany"}})
        == CompanyStatus.ADDRESS_INCOMPLETE
    )


def test_non_dict_address_is_ignored():
    assert decide_status({"postal_address": "Berlin"}) == CompanyStatus.PARSING_FAILED


def test_junk_address_values_are_empty():
    data = {"postal_address": {"city": "n/a", "street": "-"}}
    assert decide_status(data) == CompanyStatus.PARSING_FAILED


# ------------------------------------------------------- has_any_field


@pytest.mark.parametrize("field", list(RESPONSE_FIELDS))
def test_every_top_level_field_counts(field):
    assert has_any_field({field: "x"}) is True


@pytest.mark.parametrize("field", list(ADDRESS_FIELDS))
def test_every_address_subfield_counts(field):
    assert has_any_field({"postal_address": {field: "x"}}) is True


def test_raw_is_not_a_detected_field():
    assert has_any_field({"_raw": {"company_name": "ACME"}}) is False


@pytest.mark.parametrize("value", [None, "", "   ", "null", "NULL", "n/a", "N/A", "-"])
def test_empty_like_values_do_not_count(value):
    assert has_any_field({"company_name": value}) is False


@pytest.mark.parametrize("data", [{}, None, "string", 42, []])
def test_non_dict_or_empty_input_is_false(data):
    assert has_any_field(data) is False


# -------------------------------------------------- format_postal_address


@pytest.mark.parametrize(
    "address, expected",
    [
        (FULL_ADDRESS, "Main Street 12, 10115 Berlin, Germany"),
        (
            {
                "street": "Main Street",
                "house_no": "12",
                "postcode": "10115",
                "city": "Berlin",
            },
            "Main Street 12, 10115 Berlin",
        ),
        (
            {
                "street": "Hauptstr.",
                "postcode": "10115",
                "city": "Berlin",
                "country": "Germany",
            },
            "Hauptstr., 10115 Berlin, Germany",
        ),
        ({"house_no": "12", "city": "Berlin"}, "12, Berlin"),
        ({"postcode": "10115", "country": "Germany"}, "10115, Germany"),
        ({"city": "Berlin"}, "Berlin"),
        ({}, None),
        ({"street": "  Main   Street ", "house_no": " 12 "}, "Main Street 12"),
        (
            {
                "street": "Musterstraße",
                "house_no": "5a",
                "postcode": "01067",
                "city": "Dresden",
                "country": "Deutschland",
            },
            "Musterstraße 5a, 01067 Dresden, Deutschland",
        ),
    ],
    ids=[
        "full",
        "no-country",
        "no-house-no",
        "house+city",
        "zip+country",
        "city-only",
        "empty",
        "extra-spaces",
        "umlaut",
    ],
)
def test_address_format(address, expected):
    assert format_postal_address(address) == expected


def test_empty_address_gives_none_not_empty_string():
    assert format_postal_address({}) is None


# ----------------------------------------------------------- map_to_row


def test_legal_name_comes_from_json(full_data):
    assert map_to_row(full_data, "CSV Name")["legal_name"] == "ACME GmbH"


@pytest.mark.parametrize("json_name", [None, "", "   ", "null"])
def test_legal_name_falls_back_to_csv(json_name):
    row = map_to_row({"company_name": json_name}, "ACME GmbH")
    assert row["legal_name"] == "ACME GmbH"


def test_fallback_does_not_make_status_ok():
    data = {"company_name": None}
    assert map_to_row(data, "ACME GmbH")["legal_name"] == "ACME GmbH"
    assert decide_status(data) == CompanyStatus.PARSING_FAILED


def test_email_is_lowercased_in_mapping(full_data):
    assert map_to_row(full_data, "X")["email"] == "info@acme.de"


def test_phone_and_vat_are_kept_as_is(full_data):
    row = map_to_row(full_data, "X")
    assert row["phone"] == "+49 30 123456"
    assert row["vat_number"] == "DE123456789"


def test_address_is_joined(full_data):
    assert (
        map_to_row(full_data, "X")["postal_address"]
        == "Main Street 12, 10115 Berlin, Germany"
    )


def test_raw_json_uses_the_full_parsed_object():
    payload = {"company_name": "A", "fax": "+49 1", "extra": {"x": 1}}
    data = {k: payload.get(k) for k in RESPONSE_FIELDS}
    data["postal_address"] = {}
    data["_raw"] = payload

    row = map_to_row(data, "A")

    assert row["raw_json"] == payload
    assert "fax" not in row
    assert "extra" not in row


def test_raw_json_falls_back_to_data_without_raw_key():
    data = {"company_name": "A"}
    assert map_to_row(data, "A")["raw_json"] == data


def test_row_has_exactly_the_eight_extracted_columns():
    from db import EXTRACTED_COLUMNS

    assert set(map_to_row({}, "X")) == set(EXTRACTED_COLUMNS)


def test_empty_data_leaves_extraction_null():
    row = map_to_row({}, "ACME GmbH")
    assert row["legal_name"] == "ACME GmbH"
    assert row["raw_json"] == {}
    for key in (
        "postal_address",
        "handelsregister",
        "vat_number",
        "phone",
        "email",
        "responsible_person",
    ):
        assert row[key] is None


def test_map_does_not_mutate_the_source(full_data):
    snapshot = json.dumps(full_data, sort_keys=True)
    map_to_row(full_data, "X")
    assert json.dumps(full_data, sort_keys=True) == snapshot


def test_numeric_house_no_is_accepted():
    data = {"postal_address": {**FULL_ADDRESS, "house_no": 12}}
    assert (
        map_to_row(data, "X")["postal_address"]
        == "Main Street 12, 10115 Berlin, Germany"
    )


# ------------------------------------------------------- cfemail decoding


def test_cfemail_data_attribute_is_decoded():
    # test@example.de, key=0x3a
    encoded = "3a4e5f494e7a5f425b574a565f145e5f"
    html = (
        "<html><body>"
        '<p>Kontakt: <span class="cf_email" '
        f'data-cfemail="{encoded}">[email protected]</span></p>'
        "</body></html>"
    )
    text = extract_visible_text(html)
    assert "test@example.de" in text
    assert encoded not in text


def test_cfemail_cdn_link_is_decoded():
    encoded = "3a4e5f494e7a5f425b574a565f145e5f"
    html = (
        "<html><body>"
        '<p>Email: <a href="/cdn-cgi/l/email-protection#'
        f'{encoded}">[email protected]</a></p>'
        "</body></html>"
    )
    text = extract_visible_text(html)
    assert "test@example.de" in text


def test_cfemail_invalid_hex_does_not_crash():
    html = (
        "<html><body>"
        '<p>Before <span data-cfemail="ZZZZ">broken</span> After</p>'
        "</body></html>"
    )
    text = extract_visible_text(html)
    assert "Before" in text
    assert "After" in text


def test_cfemail_empty_page_stays_empty():
    html = '<html><body><span data-cfemail="zz"></span></body></html>'
    text = extract_visible_text(html)
    assert text == ""


# ----------------------------------------------------------- map_to_row: email validation


@pytest.mark.parametrize(
    "bad_email",
    [
        "not-an-email",
        "",
        "   ",
        "missing-at-sign.de",
        "@no-local.de",
        "no-domain@",
        "[email protected]",
    ],
)
def test_map_to_row_invalid_email_gives_none(bad_email):
    row = map_to_row({"email": bad_email}, "ACME GmbH")
    assert row["email"] is None


def test_map_to_row_valid_email_is_lowercased():
    row = map_to_row({"email": "INFO@ACME.DE"}, "X")
    assert row["email"] == "info@acme.de"


def test_map_to_row_email_with_plus_tag_is_accepted():
    row = map_to_row({"email": "User.Name+Tag@Domain.CO.UK"}, "X")
    assert row["email"] == "user.name+tag@domain.co.uk"
