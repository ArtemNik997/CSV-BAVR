import io
import re

import pytest

import web_service
from web_service import CompanyCsvError, parse_companies_csv

HEADER = "URL;Company Name;Language;Country;Postcode;City;Street;HouseNo;Web"

ENRICHMENT_COLUMNS = {
    "status",
    "impressum_url",
    "legal_name",
    "postal_address",
    "handelsregister",
    "vat_number",
    "phone",
    "email",
    "responsible_person",
    "raw_json",
}


def line(
    name="ACME GmbH",
    url="https://duckduckgo.com",
    language="de",
    country="Germany",
    postcode="10115",
    city="Berlin",
    street="Main Street",
    house_no="12",
    web="https://www.acme-gmbh.de",
):
    fields = [url, name, language, country, postcode, city, street, house_no, web]
    return ";".join('"%s"' % f.replace('"', '""') for f in fields)


def csv_text(*lines, header=HEADER, eol="\n"):
    return eol.join([header, *lines]) + eol


def csv_bytes(*lines, header=HEADER, bom=False, eol="\n"):
    data = csv_text(*lines, header=header, eol=eol).encode("utf-8")
    return (b"\xef\xbb\xbf" + data) if bom else data


# ---------------------------------------------------------------- parser


def test_row_is_mapped_to_db_column_order():
    rows, total = parse_companies_csv(csv_text(line()))
    assert total == 1
    assert rows == [
        (
            "ACME GmbH",
            "de",
            "Germany",
            "10115",
            "Berlin",
            "Main Street",
            "12",
            "https://www.acme-gmbh.de",
        )
    ]


def test_url_column_is_not_stored():
    rows, _ = parse_companies_csv(csv_text(line(url="https://example.org/recorder")))
    assert all("https://example.org/recorder" not in row for row in rows)


def test_web_is_stored_as_is_without_scheme():
    rows, _ = parse_companies_csv(csv_text(line(web="www.example.de")))
    assert rows[0][-1] == "www.example.de"


def test_values_are_stripped_and_empty_become_none():
    rows, _ = parse_companies_csv(csv_text(line(name="  ACME  ", city="   ", web="")))
    name, _lang, _country, _zip, city, _street, _no, web = rows[0]
    assert name == "ACME"
    assert city is None
    assert web is None


def test_missing_web_column_gives_none():
    header = "URL;Company Name;Language;Country;Postcode;City;Street;HouseNo"
    text = header + '\n"https://x.de";"ACME";"de";"Germany";"1";"Berlin";"Str";"1"\n'
    rows, total = parse_companies_csv(text)
    assert total == 1
    assert rows[0][0] == "ACME"
    assert rows[0][-1] is None


def test_short_row_does_not_fail():
    text = HEADER + '\n"https://x.de";"ACME"\n'
    rows, total = parse_companies_csv(text)
    assert total == 1
    assert rows == [("ACME", None, None, None, None, None, None, None)]


def test_extra_fields_are_ignored():
    text = HEADER + '\n"u";"ACME";"de";"DE";"1";"B";"S";"1";"w";"extra";"more"\n'
    rows, total = parse_companies_csv(text)
    assert total == 1
    assert rows[0][0] == "ACME"


def test_header_names_are_stripped():
    header = " URL ; Company Name ;Language;Country;Postcode;City;Street;HouseNo;Web"
    rows, _ = parse_companies_csv(csv_text(line(), header=header))
    assert rows[0][0] == "ACME GmbH"


def test_company_name_column_may_be_anywhere():
    text = 'Web;Company Name\n"www.a.de";"ACME"\n'
    rows, _ = parse_companies_csv(text)
    assert rows == [("ACME", None, None, None, None, None, None, "www.a.de")]


@pytest.mark.parametrize("blank_name", ["", "   "])
def test_row_without_company_name_is_skipped_but_counted(blank_name):
    rows, total = parse_companies_csv(csv_text(line("A"), line(blank_name), line("B")))
    assert total == 3
    assert [r[0] for r in rows] == ["A", "B"]


def test_row_of_only_separators_is_skipped_but_counted():
    rows, total = parse_companies_csv(csv_text(line("A"), ";;;;;;;;"))
    assert total == 2
    assert [r[0] for r in rows] == ["A"]


def test_blank_lines_are_not_counted():
    text = HEADER + "\n\n" + line("A") + "\n\n\n" + line("B") + "\n"
    rows, total = parse_companies_csv(text)
    assert total == 2
    assert [r[0] for r in rows] == ["A", "B"]


def test_duplicates_in_file_are_kept_for_the_db_to_resolve():
    rows, total = parse_companies_csv(csv_text(line("Beta"), line("Beta", city="Rome")))
    assert total == 2
    assert [r[0] for r in rows] == ["Beta", "Beta"]
    assert rows[0][4] == "Berlin" and rows[1][4] == "Rome"


def test_quoted_semicolon_and_doubled_quotes():
    rows, _ = parse_companies_csv(csv_text(line('Foo; Bar "Inc"')))
    assert rows[0][0] == 'Foo; Bar "Inc"'


def test_non_ascii_names():
    rows, _ = parse_companies_csv(
        csv_text(line("Müller & Söhne GmbH"), line("ООО Ромашка", city="Минск"))
    )
    assert rows[0][0] == "Müller & Söhne GmbH"
    assert rows[1][0] == "ООО Ромашка"
    assert rows[1][4] == "Минск"


def test_crlf_line_endings():
    rows, total = parse_companies_csv(csv_text(line("A"), line("B"), eol="\r\n"))
    assert total == 2
    assert [r[0] for r in rows] == ["A", "B"]


def test_embedded_newline_in_quoted_field_is_preserved():
    text = HEADER + '\n"u";"ACME";"de";"DE";"1";"B";"Line1\nLine2";"1";"w"\n'
    rows, total = parse_companies_csv(text)
    assert total == 1
    assert rows[0][5] == "Line1\nLine2"


def test_missing_company_name_column_lists_found_columns():
    with pytest.raises(CompanyCsvError) as exc:
        parse_companies_csv("URL;Name\n" + '"u";"x"\n')
    message = str(exc.value)
    assert "Company Name" in message
    assert "URL" in message and "Name" in message


def test_wrong_delimiter_is_reported():
    comma_header = HEADER.replace(";", ",")
    with pytest.raises(CompanyCsvError) as exc:
        parse_companies_csv(comma_header + "\n")
    assert "';'" in str(exc.value)


@pytest.mark.parametrize("text", ["", "\n\n"])
def test_empty_file_is_an_error(text):
    with pytest.raises(CompanyCsvError):
        parse_companies_csv(text)


def test_header_only_file_gives_no_rows():
    rows, total = parse_companies_csv(HEADER + "\n")
    assert rows == [] and total == 0


# ---------------------------------------------------------- SQL contract


def test_upsert_does_not_touch_enrichment_columns():
    sql = web_service.UPSERT_COMPANY_SQL
    update_part = sql.split("DO UPDATE SET")[1].split("RETURNING")[0]
    assigned = set(re.findall(r"(\w+)\s*=", update_part))
    assert assigned == {
        "language",
        "country",
        "postcode",
        "city",
        "street",
        "house_no",
        "web",
        "updated_at",
    }
    assert not assigned & ENRICHMENT_COLUMNS


def test_upsert_reports_insert_or_update():
    assert "RETURNING (xmax = 0) AS inserted" in web_service.UPSERT_COMPANY_SQL
    assert "ON CONFLICT (company_name)" in web_service.UPSERT_COMPANY_SQL


# ----------------------------------------------------------------- route


class FakeCursor:
    def __init__(self, db):
        self.db = db
        self._row = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params):
        name = params[0]
        if name == self.db.fail_on:
            raise RuntimeError("boom")
        self.db.executed.append((sql, params))
        self._row = {"inserted": name not in self.db.names}
        self.db.names.add(name)

    def fetchone(self):
        return self._row


class FakeDB:
    def __init__(self, existing=(), fail_on=None):
        self.names = set(existing)
        self.fail_on = fail_on
        self.executed = []
        self.committed = False
        self.rolled_back = False

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


@pytest.fixture
def client():
    web_service.app.config["TESTING"] = True
    return web_service.app.test_client()


@pytest.fixture
def no_db(monkeypatch):
    def forbidden():
        raise AssertionError("the database must not be touched")

    monkeypatch.setattr(web_service, "get_db", forbidden)


@pytest.fixture
def fake_db(monkeypatch):
    def install(**kwargs):
        db = FakeDB(**kwargs)
        monkeypatch.setattr(web_service, "get_db", lambda: db)
        return db

    return install


def upload(client, data, filename="companies.csv"):
    return client.post(
        "/upload_companies",
        data={"file": (io.BytesIO(data), filename)},
        content_type="multipart/form-data",
    )


def test_no_file_field_is_400(client, no_db):
    resp = client.post("/upload_companies", data={}, content_type="multipart/form-data")
    assert resp.status_code == 400
    assert "No file" in resp.get_json()["error"]


def test_empty_filename_is_400(client, no_db):
    resp = upload(client, csv_bytes(line()), filename="")
    assert resp.status_code == 400


def test_non_utf8_file_is_400(client, no_db):
    resp = upload(client, b"Company Name\n\xff\xfe\n")
    assert resp.status_code == 400
    assert "UTF-8" in resp.get_json()["error"]


def test_missing_company_name_header_is_400(client, no_db):
    resp = upload(client, b"URL;Name\n\"u\";\"x\"\n")
    assert resp.status_code == 400
    assert "Company Name" in resp.get_json()["error"]


def test_empty_file_is_400(client, no_db):
    resp = upload(client, b"")
    assert resp.status_code == 400


def test_import_counts_inserted_and_updated(client, fake_db):
    db = fake_db(existing={"B"})
    resp = upload(client, csv_bytes(line("A"), line("B"), line("A")))
    assert resp.status_code == 200
    # A: new, B: already in the DB, second A: duplicate inside the file
    assert resp.get_json() == {"imported": 1, "updated": 2, "total_rows": 3}
    assert db.committed and not db.rolled_back
    assert [params[0] for _sql, params in db.executed] == ["A", "B", "A"]


def test_import_passes_eight_values_in_column_order(client, fake_db):
    db = fake_db()
    upload(client, csv_bytes(line("ACME GmbH")))
    (sql, params), = db.executed
    assert sql == web_service.UPSERT_COMPANY_SQL
    assert params == (
        "ACME GmbH",
        "de",
        "Germany",
        "10115",
        "Berlin",
        "Main Street",
        "12",
        "https://www.acme-gmbh.de",
    )


def test_rows_without_name_count_in_total_but_are_not_written(client, fake_db):
    db = fake_db()
    resp = upload(client, csv_bytes(line("A"), line(""), line("B")))
    assert resp.get_json() == {"imported": 2, "updated": 0, "total_rows": 3}
    assert len(db.executed) == 2


def test_utf8_bom_is_accepted(client, fake_db):
    header = "Company Name;Web"
    db = fake_db()
    data = (header + '\n"ACME";"www.a.de"\n').encode("utf-8")
    resp = upload(client, b"\xef\xbb\xbf" + data)
    assert resp.status_code == 200
    assert db.executed[0][1][0] == "ACME"


def test_non_ascii_names_reach_the_db(client, fake_db):
    db = fake_db()
    upload(client, csv_bytes(line("Müller & Söhne GmbH"), line("ООО Ромашка")))
    assert [p[0] for _s, p in db.executed] == ["Müller & Söhne GmbH", "ООО Ромашка"]


def test_database_error_rolls_back_and_returns_500(client, fake_db):
    db = fake_db(fail_on="B")
    resp = upload(client, csv_bytes(line("A"), line("B")))
    assert resp.status_code == 500
    assert resp.get_json()["error"].startswith("Database error")
    assert db.rolled_back and not db.committed