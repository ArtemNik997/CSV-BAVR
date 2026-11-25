from datetime import datetime, timezone
from urllib.parse import quote

import pytest

import web_service
from db import COMPANIES_COLUMNS, CompanyStatus
from web_service import COMPANY_SELECT, company_to_dict

NOW = datetime(2026, 11, 23, 12, 0, tzinfo=timezone.utc)
NOW_ISO = "2026-11-23T12:00:00+00:00"
COLUMN_NAMES = [name for name, _ in COMPANIES_COLUMNS]


def make_record(id=1, company_name="ACME GmbH", status=None, **overrides):
    record = {"id": id, "company_name": company_name}
    record.update({name: None for name in COLUMN_NAMES})
    record.update(created_at=NOW, updated_at=NOW, status=status)
    record.update(overrides)
    return record


# ---------------------------------------------------------- serialization


def test_company_select_lists_every_column_once():
    selected = [c.strip() for c in COMPANY_SELECT.split(",")]
    assert set(selected) == {"id", "company_name", *COLUMN_NAMES}
    assert len(selected) == len(set(selected))


def test_result_contains_all_columns_and_status_name():
    keys = set(company_to_dict(make_record()))
    assert keys == {"id", "company_name", "status_name", *COLUMN_NAMES}


def test_dates_become_iso_strings():
    data = company_to_dict(make_record())
    assert data["created_at"] == NOW_ISO
    assert data["updated_at"] == NOW_ISO


def test_missing_dates_stay_none():
    data = company_to_dict(make_record(created_at=None, updated_at=None))
    assert data["created_at"] is None
    assert data["updated_at"] is None


@pytest.mark.parametrize("status", list(CompanyStatus), ids=lambda s: s.name)
def test_status_name_for_every_code(status):
    data = company_to_dict(make_record(status=int(status)))
    assert data["status"] == int(status)
    assert data["status_name"] == status.name


def test_pending_company_has_null_status_and_name():
    data = company_to_dict(make_record(status=None))
    assert data["status"] is None
    assert data["status_name"] is None


def test_unknown_status_code_does_not_break_serialization():
    data = company_to_dict(make_record(status=99))
    assert data["status"] == 99
    assert data["status_name"] is None


def test_raw_json_is_passed_through():
    raw = {
        "company_name": "ACME GmbH",
        "postal_address": {"street": "Main Street", "house_no": "12"},
    }
    assert company_to_dict(make_record(raw_json=raw))["raw_json"] == raw


def test_input_record_is_not_mutated():
    record = make_record(status=0)
    company_to_dict(record)
    assert record["created_at"] is NOW
    assert "status_name" not in record


# ---------------------------------------------------------------- routing


def test_company_name_converter_accepts_slashes():
    adapter = web_service.app.url_map.bind("localhost")
    endpoint, args = adapter.match("/companies/Acme: Web/Dev?", method="GET")
    assert web_service.app.view_functions[endpoint] is web_service.get_company
    assert args == {"company_name": "Acme: Web/Dev?"}


def test_collection_route_is_separate_from_single_company():
    adapter = web_service.app.url_map.bind("localhost")
    endpoint, args = adapter.match("/companies", method="GET")
    assert web_service.app.view_functions[endpoint] is web_service.list_companies
    assert args == {}


@pytest.mark.parametrize("path", ["/companies", "/companies/ACME"])
def test_only_get_is_allowed(client, no_db, path):
    assert client.post(path).status_code == 405


def test_empty_company_name_is_404_without_db(client, no_db):
    assert client.get("/companies/").status_code == 404


# ------------------------------------------------------------- fake DB


class ReadCursor:
    def __init__(self, db):
        self.db = db
        self.rows = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=()):
        params = tuple(params)
        self.db.queries.append((sql, params))
        rows = list(self.db.records)
        if "WHERE status = %s" in sql:
            rows = [r for r in rows if r["status"] == params[0]]
        elif "WHERE status IS NULL" in sql:
            rows = [r for r in rows if r["status"] is None]
        elif "WHERE company_name = %s" in sql:
            rows = [r for r in rows if r["company_name"] == params[0]]
        self.rows = rows

    def fetchall(self):
        return list(self.rows)

    def fetchone(self):
        return self.rows[0] if self.rows else None


class ReadDB:
    def __init__(self, records):
        self.records = list(records)
        self.queries = []

    def cursor(self):
        return ReadCursor(self)


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
    def install(*records):
        db = ReadDB(records)
        monkeypatch.setattr(web_service, "get_db", lambda: db)
        return db

    return install


# ----------------------------------------------------------- GET /companies


def test_list_without_filter_returns_everything(client, fake_db):
    db = fake_db(
        make_record(1, "A", status=None),
        make_record(2, "B", status=0),
        make_record(3, "C", status=3),
    )
    resp = client.get("/companies")
    assert resp.status_code == 200
    assert [c["company_name"] for c in resp.get_json()] == ["A", "B", "C"]

    sql, params = db.queries[-1]
    assert "WHERE" not in sql
    assert sql.rstrip().endswith("ORDER BY id")
    assert params == ()


def test_list_of_empty_table_is_empty_array(client, fake_db):
    fake_db()
    resp = client.get("/companies")
    assert resp.status_code == 200
    assert resp.get_json() == []


def test_list_items_are_serialized(client, fake_db):
    fake_db(make_record(1, "A", status=3))
    item = client.get("/companies").get_json()[0]
    assert item["status"] == 3
    assert item["status_name"] == "ADDRESS_INCOMPLETE"
    assert item["created_at"] == NOW_ISO


def test_filter_by_status_code(client, fake_db):
    db = fake_db(
        make_record(1, "A", status=None),
        make_record(2, "B", status=3),
        make_record(3, "C", status=3),
    )
    resp = client.get("/companies", query_string={"status": "3"})
    assert resp.status_code == 200
    assert [c["company_name"] for c in resp.get_json()] == ["B", "C"]

    sql, params = db.queries[-1]
    assert "WHERE status = %s" in sql
    assert params == (3,)


def test_status_zero_is_a_real_filter_not_ignored(client, fake_db):
    fake_db(
        make_record(1, "A", status=0),
        make_record(2, "B", status=None),
        make_record(3, "C", status=3),
    )
    resp = client.get("/companies", query_string={"status": "0"})
    assert [c["company_name"] for c in resp.get_json()] == ["A"]


@pytest.mark.parametrize("value", ["pending", "PENDING", " Pending "])
def test_filter_pending_selects_unprocessed(client, fake_db, value):
    db = fake_db(
        make_record(1, "A", status=None),
        make_record(2, "B", status=0),
        make_record(3, "C", status=None),
    )
    resp = client.get("/companies", query_string={"status": value})
    assert [c["company_name"] for c in resp.get_json()] == ["A", "C"]

    sql, params = db.queries[-1]
    assert "WHERE status IS NULL" in sql
    assert params == ()


@pytest.mark.parametrize("value", ["abc", "7", "6", "-1", "", " ", "1.5"])
def test_invalid_status_is_400_without_db(client, no_db, value):
    resp = client.get("/companies", query_string={"status": value})
    assert resp.status_code == 400
    assert "status" in resp.get_json()["error"]


# ------------------------------------------------ GET /companies/<name>


def test_one_company_is_returned(client, fake_db):
    fake_db(make_record(1, "ACME GmbH", status=0, web="www.acme-gmbh.de"))
    resp = client.get("/companies/ACME%20GmbH")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["company_name"] == "ACME GmbH"
    assert body["web"] == "www.acme-gmbh.de"
    assert body["status_name"] == "OK"
    assert body["updated_at"] == NOW_ISO


def test_unknown_company_is_404(client, fake_db):
    fake_db(make_record(1, "ACME GmbH"))
    resp = client.get("/companies/Unknown")
    assert resp.status_code == 404
    assert resp.get_json() == {"error": "Company not found"}


def test_lookup_is_exact_and_case_sensitive(client, fake_db):
    fake_db(make_record(1, "ACME GmbH"))
    assert client.get("/companies/acme%20gmbh").status_code == 404


def test_surrounding_spaces_in_url_are_stripped(client, fake_db):
    db = fake_db(make_record(1, "ACME GmbH"))
    resp = client.get("/companies/%20ACME%20GmbH%20")
    assert resp.status_code == 200
    assert db.queries[-1][1] == ("ACME GmbH",)


@pytest.mark.parametrize(
    "name",
    [
        "ACME GmbH",
        "Müller & Söhne GmbH",
        "ООО Ромашка",
        "Acme: Web/Dev?",
        "100% Dev #1",
    ],
)
def test_special_names_round_trip(client, fake_db, name):
    db = fake_db(make_record(1, name))
    resp = client.get("/companies/" + quote(name, safe=""))
    assert resp.status_code == 200
    assert resp.get_json()["company_name"] == name

    sql, params = db.queries[-1]
    assert "WHERE company_name = %s" in sql
    assert params == (name,)
    assert name not in sql


def test_name_is_never_interpolated_into_sql(client, fake_db):
    evil = "x'; DROP TABLE companies; --"
    db = fake_db(make_record(1, "ACME GmbH"))
    resp = client.get("/companies/" + quote(evil, safe=""))
    assert resp.status_code == 404

    sql, params = db.queries[-1]
    assert "DROP TABLE" not in sql
    assert params == (evil,)