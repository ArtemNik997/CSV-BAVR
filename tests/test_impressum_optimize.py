import pytest

from impressum import (
    CUT_MARK,
    HEAD_MARGIN,
    KEYWORD_RE,
    MAX_TEXT_CHARS,
    TAIL_MARGIN,
    OptimizedText,
    estimate_tokens,
    head_tail_window,
    keyword_filter,
    optimize_text,
    strip_noise_lines,
)

# Нейтральный текст: в нём нет якорей, ключевых слов и «сильных» сигналов
FILLER = "Dies ist ein langer Absatz ohne rechtliche Angaben und ohne Kontaktdaten."

LEGAL = [
    "Impressum",
    "Angaben gemäß § 5 TMG",
    "ACME GmbH",
    "Main Street 12",
    "10115 Berlin",
    "Telefon: +49 30 1234567",
    "E-Mail: info@acme-gmbh.de",
    "Handelsregister: HRB 123456",
    "Registergericht: Amtsgericht Berlin",
    "USt-IdNr.: DE123456789",
    "Geschäftsführer: Max Mustermann",
]


def filler(chars):
    lines, total, i = [], 0, 0
    while total < chars:
        line = f"{FILLER} {i}"
        lines.append(line)
        total += len(line) + 1
        i += 1
    return "\n".join(lines)


def long_page(legal=LEGAL, head=5000, tail=6000):
    return "\n".join(
        [
            "Startseite",
            "Menü",
            filler(head),
            *legal,
            filler(tail),
            "Datenschutz",
            "AGB",
            "© 2024 ACME GmbH",
        ]
    )


# ------------------------------------------------------ strip_noise_lines


@pytest.mark.parametrize(
    "line",
    [
        "Datenschutz",
        "DATENSCHUTZ",
        "Datenschutzerklärung",
        "AGB",
        "Cookies",
        "Cookie-Einstellungen",
        "Alle Cookies akzeptieren",
        "Menü",
        "Menu",
        "Startseite",
        "Home",
        "Kontakt",
        "Sitemap",
        "Nach oben",
        "Suche",
        "Deutsch",
        "English",
        "Français",
        "Русский",
        "© 2024 ACME GmbH",
        "(c) 2024 ACME",
        "Copyright 2024 ACME",
    ],
)
def test_noise_lines_are_removed(line):
    assert strip_noise_lines(f"before\n{line}\nafter") == "before\nafter"


@pytest.mark.parametrize(
    "line",
    [
        "DE",
        "123456789",
        "Datenschutzbeauftragter: Max Mustermann",
        "Kontakt: info@acme.de",
        "Home Office GmbH",
        "Main Street 12",
    ],
)
def test_legal_lines_are_kept(line):
    assert strip_noise_lines(f"a\n{line}\nb") == f"a\n{line}\nb"


def test_noise_removal_strips_and_drops_blank_lines():
    assert strip_noise_lines("  a  \n\n\n  b\n") == "a\nb"


# ---------------------------------------------------------- keyword_filter


@pytest.mark.parametrize(
    "line",
    [
        "Hauptstraße 1",
        "Hauptstrasse 1",
        "Hauptstr. 1",
        "Tel: 123",
        "Telefon 123",
        "Fax: 1",
        "E-Mail",
        "Email",
        "USt-IdNr.",
        "HRB 1",
        "HRA 1",
        "Handelsregister",
        "Registergericht",
        "Amtsgericht Berlin",
        "vertreten durch",
        "Verantwortlich",
        "Umsatzsteuer-ID",
        "Geschäftsführer",
        "DE123456789",
        "a@b.de",
        "+49 30 1",
        "0049 30 1",
        "10115 Berlin",
    ],
)
def test_keyword_regex_matches(line):
    assert KEYWORD_RE.search(line)


@pytest.mark.parametrize("line", ["Dies ist ein Absatz", "zeile 3", "ACME GmbH"])
def test_keyword_regex_ignores_neutral_lines(line):
    assert not KEYWORD_RE.search(line)


def test_keyword_filter_keeps_context():
    lines = [f"zeile {i}" for i in range(10)]
    lines[5] = "Hauptstraße 5"
    assert keyword_filter("\n".join(lines)) == "\n".join(lines[3:8])
    assert keyword_filter("\n".join(lines), context=0) == "Hauptstraße 5"


# --------------------------------------------------------- head_tail_window


def test_window_returns_short_text_unchanged():
    assert head_tail_window("abc", 100) == "abc"


@pytest.mark.parametrize("max_chars", [10, 100, 1000, 8000])
def test_window_fits_the_limit_and_keeps_both_ends(max_chars):
    text = "a" * 10000 + "b" * 10000
    result = head_tail_window(text, max_chars)
    assert len(result) <= max_chars
    assert result.startswith("a") and result.endswith("b")
    assert CUT_MARK in result


# ----------------------------------------------------------- optimize_text


def test_short_text_is_unchanged_even_with_noise():
    text = "Datenschutz\n" + filler(500)
    result = optimize_text(text)
    assert result.text == text
    assert result.strategy == "unchanged"
    assert (result.head_cut, result.tail_cut, result.tokens_saved) == (0, 0, 0)


def test_threshold_boundary():
    assert optimize_text("x" * MAX_TEXT_CHARS).strategy == "unchanged"
    over = optimize_text("x" * (MAX_TEXT_CHARS + 1))
    assert over.strategy == "cleaned+window"
    assert len(over.text) <= MAX_TEXT_CHARS


def test_noise_only_when_cleaned_text_fits():
    text = filler(6000) + "\n" + "\n".join(["Datenschutz"] * 300)
    assert len(text) > MAX_TEXT_CHARS
    result = optimize_text(text)
    assert result.strategy == "noise-only"
    assert result.text == strip_noise_lines(text)
    assert "Datenschutz" not in result.text


def test_sandwich_keeps_the_legal_block_and_cuts_head_and_tail():
    text = long_page()
    result = optimize_text(text)
    assert result.strategy == "sandwich"
    for part in LEGAL:
        assert part in result.text
    assert "Startseite" not in result.text and "Datenschutz" not in result.text
    assert len(result.text) < 1500
    assert result.head_cut > 4000 and result.tail_cut > 4000
    assert result.chars_before == len(text)
    assert result.tokens_saved > 0


def test_margins_are_exactly_head_and_tail_margin():
    text = "\n".join(
        [
            filler(4000),
            "Impressum",
            "E-Mail: info@acme.de",
            "n" * 200,
            "NEAR",
            "f" * 300,
            "FAR",
            filler(6000),
        ]
    )
    result = optimize_text(text)
    assert len(result.text.split("Impressum", 1)[0]) == HEAD_MARGIN
    assert len(result.text.split("info@acme.de", 1)[1]) == TAIL_MARGIN
    assert "NEAR" in result.text and "FAR" not in result.text


@pytest.mark.parametrize(
    "last_line",
    [
        "Telefon: +49 30 1234567",
        "Telefon: 0049 30 1234567",
        "USt-IdNr.: DE123456789",
        "E-Mail: info@acme-gmbh.de",
        "Handelsregister: HRB 123456",
        "Handelsregister: HRA 98765",
        "Registergericht: Berlin",
        "Amtsgericht Berlin",
        "Geschäftsführer: Max Mustermann",
        "Vertreten durch: Max Mustermann",
        "Inhaltlich verantwortlich: Max Mustermann",
    ],
)
def test_every_tail_anchor_is_kept_and_the_rest_is_cut(last_line):
    text = "\n".join(
        [
            filler(4000),
            "Impressum",
            "ACME GmbH",
            "E-Mail: info@acme-gmbh.de",
            last_line,
            filler(6000),
        ]
    )
    result = optimize_text(text)
    assert result.strategy == "sandwich"
    assert last_line in result.text
    assert result.tail_cut > 4000
    assert len(result.text) < 1000


def test_the_last_anchor_wins():
    text = "\n".join(
        [
            filler(4000),
            "Impressum",
            "E-Mail: a@b.de",
            "MIDDLE",
            "Telefon: +49 30 1234567",
            filler(6000),
        ]
    )
    result = optimize_text(text)
    assert "MIDDLE" in result.text and "+49 30 1234567" in result.text
    assert result.tail_cut > 4000


def test_no_head_anchor_means_no_head_cut_but_tail_is_still_cut():
    text = "\n".join([filler(4000), "ACME GmbH", "E-Mail: info@acme.de", filler(6000)])
    result = optimize_text(text)
    assert result.strategy == "sandwich"
    assert result.head_cut == 0
    assert result.text.startswith(FILLER + " 0")
    assert result.tail_cut > 4000


def test_drittanbieter_is_not_a_head_anchor():
    text = "\n".join(
        [
            "Wir nutzen Drittanbieter für die Statistik.",
            filler(4000),
            "Impressum",
            "E-Mail: info@acme.de",
            filler(6000),
        ]
    )
    result = optimize_text(text)
    assert result.head_cut > 3000
    assert "Drittanbieter" not in result.text


def test_no_strong_signal_falls_back_to_cleaned_text_with_window():
    text = "\n".join(
        [filler(4000), "Impressum", "ACME GmbH", "Main Street 12", filler(6000)]
    )
    result = optimize_text(text)
    assert result.strategy == "cleaned+window"
    assert len(result.text) <= MAX_TEXT_CHARS
    assert CUT_MARK in result.text
    assert (result.head_cut, result.tail_cut) == (0, 0)


def test_signals_only_before_the_head_anchor_fall_back():
    text = "\n".join(["E-Mail: info@acme.de", filler(6000), "Impressum", filler(3000)])
    result = optimize_text(text)
    assert result.strategy == "cleaned+window"
    assert "info@acme.de" in result.text


def test_tight_margins_are_used_when_the_sandwich_is_too_long():
    text = "\n".join(
        [filler(3000), "Impressum", "m" * 560, "E-Mail: info@acme.de", filler(3000)]
    )
    result = optimize_text(text, max_chars=1000)
    assert result.strategy == "sandwich-tight"
    assert result.text.startswith("Impressum")
    assert "info@acme.de" in result.text
    assert len(result.text) <= 1000


def test_keyword_filter_is_applied_to_a_very_long_block():
    text = "\n".join(
        [
            filler(2000),
            "Impressum",
            filler(2000),
            "Hauptstraße 12",
            "10115 Berlin",
            filler(2000),
            "Telefon: +49 30 1234567",
            filler(2000),
        ]
    )
    result = optimize_text(text, max_chars=1200)
    assert result.strategy == "sandwich-tight+keywords"
    for part in ("Hauptstraße 12", "10115 Berlin", "+49 30 1234567"):
        assert part in result.text
    assert len(result.text) <= 1200


def test_window_is_the_last_resort():
    lines = [
        "Impressum",
        *[f"Straße {i}" for i in range(200)],
        "E-Mail: info@acme.de",
        filler(500),
    ]
    result = optimize_text("\n".join(lines), max_chars=1000)
    assert result.strategy == "sandwich-tight+keywords+window"
    assert len(result.text) <= 1000 and CUT_MARK in result.text
    assert result.text.startswith("Impressum") and "info@acme.de" in result.text


SCENARIOS = {
    "legal block in the middle": long_page(),
    "no head anchor": "\n".join([filler(4000), "E-Mail: info@acme.de", filler(6000)]),
    "no signals": filler(12000),
    "single huge line": "x" * 50000,
    "signals before head anchor": "\n".join(
        ["E-Mail: info@acme.de", filler(6000), "Impressum", filler(3000)]
    ),
    "only noise": "\n".join(["Datenschutz"] * 2000),
    "empty": "",
}


@pytest.mark.parametrize("text", SCENARIOS.values(), ids=SCENARIOS.keys())
def test_result_never_exceeds_the_limit(text):
    result = optimize_text(text)
    assert len(result.text) <= MAX_TEXT_CHARS
    assert result.chars_before == len(text)


def test_optimizing_twice_changes_nothing_more():
    first = optimize_text(long_page())
    second = optimize_text(first.text)
    assert second.strategy == "unchanged"
    assert second.text == first.text


# ------------------------------------------------------ metrics and summary


def test_estimate_tokens():
    assert estimate_tokens("") == 0
    assert estimate_tokens("abc") == 0
    assert estimate_tokens("x" * 400) == 100


def test_metrics_and_summary():
    result = OptimizedText("x" * 40, "s", 100, head_cut=5, tail_cut=7)
    assert (result.chars_after, result.tokens_before, result.tokens_after) == (
        40,
        25,
        10,
    )
    assert result.tokens_saved == 15
    summary = result.summary("ACME")
    assert summary == (
        "ACME: 100 -> 40 chars (~25 -> ~10 tokens), "
        "head cut 5, tail cut 7, strategy=s"
    )
    assert summary.isascii()
