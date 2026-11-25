import logging
import types

import pytest

import enrich_companies
from db import CompanyStatus


@pytest.fixture
def log():
    logger = logging.getLogger("test_enrich")
    logger.setLevel(logging.CRITICAL)
    return logger


@pytest.fixture
def spy_write(monkeypatch):
    calls = []

    def fake_write(cid, status, impressum_url=None, row=None):
        calls.append(
            {
                "cid": cid,
                "status": int(status),
                "impressum_url": impressum_url,
                "row": row,
            }
        )

    monkeypatch.setattr(enrich_companies, "write_company_result", fake_write)
    return calls


def _make_args(**kwargs):
    defaults = {
        "company": None,
        "limit": None,
        "status": None,
        "force": False,
        "verbose": False,
    }
    defaults.update(kwargs)
    return types.SimpleNamespace(**defaults)


def _ok_response(content=b"<html>OK</html>", url="https://example.de/"):
    resp = types.SimpleNamespace()
    resp.content = content
    resp.url = url
    resp.status_code = 200
    return resp


FULL_DATA = {
    "company_name": "ACME GmbH",
    "postal_address": {
        "street": "Main Street",
        "house_no": "12",
        "postcode": "10115",
        "city": "Berlin",
        "country": "Germany",
    },
}


# build_select_query


class TestBuildSelectQuery:
    def test_default_excludes_ok_includes_null(self):
        sql, params = enrich_companies.build_select_query(_make_args())
        assert "(status IS NULL OR status <> %s)" in sql
        assert params == [int(CompanyStatus.OK)]

    def test_force_removes_where(self):
        sql, params = enrich_companies.build_select_query(_make_args(force=True))
        assert "WHERE" not in sql
        assert params == []

    def test_status_filter(self):
        sql, params = enrich_companies.build_select_query(_make_args(status=4))
        assert "status = %s" in sql
        assert params == [4]

    def test_company_any_status(self):
        sql, params = enrich_companies.build_select_query(
            _make_args(company="ACME GmbH")
        )
        assert "company_name = %s" in sql
        assert "status" not in sql.split("WHERE")[1]
        assert params == ["ACME GmbH"]

    def test_company_with_status(self):
        sql, params = enrich_companies.build_select_query(
            _make_args(company="ACME GmbH", status=0)
        )
        assert "company_name = %s" in sql
        assert "status = %s" in sql
        assert params == ["ACME GmbH", 0]

    def test_limit_parameterized(self):
        sql, params = enrich_companies.build_select_query(_make_args(limit=5))
        assert sql.endswith("LIMIT %s")
        assert params[-1] == 5

    def test_company_and_limit(self):
        sql, params = enrich_companies.build_select_query(
            _make_args(company="X", limit=3)
        )
        assert "company_name = %s" in sql
        assert "LIMIT %s" in sql
        assert params == ["X", 3]

    def test_order_by_id_always_present(self):
        for kwargs in [
            {},
            {"force": True},
            {"status": 2},
            {"company": "A"},
            {"company": "A", "status": 0},
            {"limit": 1},
        ]:
            sql, _ = enrich_companies.build_select_query(_make_args(**kwargs))
            assert "ORDER BY id" in sql, f"Missing ORDER BY for {kwargs}"

    def test_placeholders_match_params(self):
        for kwargs in [
            {},
            {"force": True},
            {"status": 3},
            {"company": "ACME GmbH"},
            {"company": "ACME GmbH", "status": 0},
            {"limit": 10},
            {"company": "X", "limit": 5},
            {"status": 4, "limit": 2},
        ]:
            sql, params = enrich_companies.build_select_query(_make_args(**kwargs))
            assert sql.count("%s") == len(
                params
            ), f"Mismatch for {kwargs}: {sql.count('%s')} placeholders vs {len(params)} params"

    def test_no_value_interpolated_into_sql(self):
        evil = "x'; DROP TABLE companies; --"
        sql, params = enrich_companies.build_select_query(_make_args(company=evil))
        assert "DROP TABLE" not in sql
        assert params[0] == evil


# parse_args: --limit validation


class TestParseArgsLimit:

    @pytest.mark.parametrize("value", ["0", "-1", "-100"])
    def test_non_positive_limit_raises(self, value):
        with pytest.raises(SystemExit) as exc_info:
            enrich_companies.parse_args(["--limit", value])
        assert exc_info.value.code == 2

    def test_positive_limit_is_accepted(self):
        args = enrich_companies.parse_args(["--limit", "1"])
        assert args.limit == 1

    def test_no_limit_gives_none(self):
        args = enrich_companies.parse_args([])
        assert args.limit is None


# process_company: status 5 (NO_WEBSITE)


class TestProcessCompanyNoWebsite:

    @pytest.mark.parametrize("web", [None, "", "   ", "\t\n"])
    def test_empty_web_gives_status_5(self, web, log, spy_write, monkeypatch):
        rec = {"id": 1, "company_name": "TestCo", "web": web}

        status, saved = enrich_companies.process_company(rec, log)

        assert status == CompanyStatus.NO_WEBSITE
        assert saved == 0
        assert len(spy_write) == 1
        assert spy_write[0]["status"] == int(CompanyStatus.NO_WEBSITE)
        assert spy_write[0]["row"] is None
        assert spy_write[0]["impressum_url"] is None

    def test_no_network_calls_for_status_5(self, log, spy_write, monkeypatch):
        fetch_called = []
        monkeypatch.setattr(
            enrich_companies,
            "fetch",
            lambda *a, **kw: fetch_called.append(True),
        )
        api_called = []
        monkeypatch.setattr(
            enrich_companies,
            "extract_from_text",
            lambda *a, **kw: api_called.append(True),
        )

        rec = {"id": 1, "company_name": "TestCo", "web": ""}
        enrich_companies.process_company(rec, log)

        assert fetch_called == [], "fetch() should not be called for empty web"
        assert (
            api_called == []
        ), "extract_from_text() should not be called for empty web"


# process_company: status 1 (NO_IMPRESSUM_LINK_FOUND)


class TestProcessCompanyNoImpressumLink:

    def test_no_link_gives_status_1(self, log, spy_write, monkeypatch):
        monkeypatch.setattr(
            enrich_companies, "normalize_url", lambda w: "https://test.de/"
        )
        monkeypatch.setattr(
            enrich_companies, "fetch", lambda url: _ok_response(url=url)
        )
        monkeypatch.setattr(enrich_companies, "find_impressum_link", lambda *a: None)

        rec = {"id": 2, "company_name": "NoLinkCo", "web": "https://test.de/"}
        status, saved = enrich_companies.process_company(rec, log)

        assert status == CompanyStatus.NO_IMPRESSUM_LINK_FOUND
        assert saved == 0
        assert len(spy_write) == 1
        assert spy_write[0]["status"] == int(CompanyStatus.NO_IMPRESSUM_LINK_FOUND)
        assert spy_write[0]["row"] is None

    def test_no_api_call_when_link_missing(self, log, spy_write, monkeypatch):
        monkeypatch.setattr(
            enrich_companies, "normalize_url", lambda w: "https://test.de/"
        )
        monkeypatch.setattr(
            enrich_companies, "fetch", lambda url: _ok_response(url=url)
        )
        monkeypatch.setattr(enrich_companies, "find_impressum_link", lambda *a: None)

        api_called = []
        monkeypatch.setattr(
            enrich_companies,
            "extract_from_text",
            lambda *a, **kw: api_called.append(True),
        )

        rec = {"id": 2, "company_name": "NoLinkCo", "web": "https://test.de/"}
        enrich_companies.process_company(rec, log)

        assert api_called == []


# process_company: Status 0 (OK)


class TestProcessCompanyOK:

    def _patch_full_success(self, monkeypatch):
        monkeypatch.setattr(
            enrich_companies, "normalize_url", lambda w: "https://test.de/"
        )
        monkeypatch.setattr(
            enrich_companies,
            "fetch",
            lambda url: _ok_response(content=b"<html>Impressum text</html>", url=url),
        )
        monkeypatch.setattr(
            enrich_companies,
            "find_impressum_link",
            lambda *a: "https://test.de/impressum",
        )
        monkeypatch.setattr(
            enrich_companies,
            "extract_visible_text",
            lambda html: "Impressum\nACME GmbH\nMain Street 12\n10115 Berlin",
        )

        opt = types.SimpleNamespace()
        opt.text = "text"
        opt.tokens_saved = 500
        opt.summary = lambda name: f"{name}: optimized"
        monkeypatch.setattr(enrich_companies, "optimize_text", lambda text: opt)

        monkeypatch.setattr(
            enrich_companies, "extract_from_text", lambda text: FULL_DATA
        )
        monkeypatch.setattr(
            enrich_companies, "decide_status", lambda data: CompanyStatus.OK
        )
        monkeypatch.setattr(
            enrich_companies,
            "map_to_row",
            lambda data, name: {
                "legal_name": "ACME GmbH",
                "postal_address": "Main Street 12, 10115 Berlin, Germany",
            },
        )

    def test_full_success_gives_status_0(self, log, spy_write, monkeypatch):
        self._patch_full_success(monkeypatch)

        rec = {"id": 3, "company_name": "ACME GmbH", "web": "https://test.de/"}
        status, saved = enrich_companies.process_company(rec, log)

        assert status == CompanyStatus.OK
        assert saved == 500
        assert len(spy_write) == 1
        assert spy_write[0]["status"] == int(CompanyStatus.OK)
        assert spy_write[0]["impressum_url"] == "https://test.de/impressum"
        assert spy_write[0]["row"] is not None
        assert spy_write[0]["row"]["legal_name"] == "ACME GmbH"

    def test_two_fetch_calls_on_success(self, log, spy_write, monkeypatch):
        fetch_calls = []

        def tracking_fetch(url):
            fetch_calls.append(url)
            return _ok_response(content=b"<html></html>", url=url)

        monkeypatch.setattr(
            enrich_companies, "normalize_url", lambda w: "https://test.de/"
        )
        monkeypatch.setattr(enrich_companies, "fetch", tracking_fetch)
        monkeypatch.setattr(
            enrich_companies,
            "find_impressum_link",
            lambda *a: "https://test.de/impressum",
        )
        monkeypatch.setattr(
            enrich_companies, "extract_visible_text", lambda html: "text"
        )

        opt = types.SimpleNamespace()
        opt.text = "text"
        opt.tokens_saved = 0
        opt.summary = lambda name: "ok"
        monkeypatch.setattr(enrich_companies, "optimize_text", lambda text: opt)
        monkeypatch.setattr(
            enrich_companies, "extract_from_text", lambda text: FULL_DATA
        )
        monkeypatch.setattr(
            enrich_companies, "decide_status", lambda d: CompanyStatus.OK
        )
        monkeypatch.setattr(
            enrich_companies, "map_to_row", lambda d, n: {"legal_name": n}
        )

        rec = {"id": 3, "company_name": "ACME GmbH", "web": "https://test.de/"}
        enrich_companies.process_company(rec, log)

        assert len(fetch_calls) == 2
        assert fetch_calls[0] == "https://test.de/"
        assert fetch_calls[1] == "https://test.de/impressum"


# process_company: Status 4 (ERROR)


class TestProcessCompanyError:

    def test_fetch_error_on_homepage_gives_status_4(self, log, spy_write, monkeypatch):
        from impressum import FetchError

        monkeypatch.setattr(
            enrich_companies, "normalize_url", lambda w: "https://test.de/"
        )
        monkeypatch.setattr(
            enrich_companies,
            "fetch",
            lambda url: (_ for _ in ()).throw(FetchError("DNS failure")),
        )

        rec = {"id": 4, "company_name": "ErrCo", "web": "https://test.de/"}
        status, saved = enrich_companies.process_company(rec, log)

        assert status == CompanyStatus.ERROR
        assert len(spy_write) == 1
        assert spy_write[0]["status"] == int(CompanyStatus.ERROR)
        assert spy_write[0]["impressum_url"] is None

    def test_fetch_error_on_impressum_keeps_url(self, log, spy_write, monkeypatch):
        from impressum import FetchError

        call_count = []

        def selective_fetch(url):
            call_count.append(url)
            if "/impressum" in url:
                raise FetchError("HTTP 404")
            return _ok_response(url=url)

        monkeypatch.setattr(
            enrich_companies, "normalize_url", lambda w: "https://test.de/"
        )
        monkeypatch.setattr(enrich_companies, "fetch", selective_fetch)
        monkeypatch.setattr(
            enrich_companies,
            "find_impressum_link",
            lambda *a: "https://test.de/impressum",
        )

        rec = {"id": 5, "company_name": "ErrCo2", "web": "https://test.de/"}
        status, saved = enrich_companies.process_company(rec, log)

        assert status == CompanyStatus.ERROR
        assert len(spy_write) == 1
        assert spy_write[0]["status"] == int(CompanyStatus.ERROR)
        assert spy_write[0]["impressum_url"] == "https://test.de/impressum"

    def test_api_error_gives_status_4_and_keeps_url(self, log, spy_write, monkeypatch):
        from impressum import ApiError

        monkeypatch.setattr(
            enrich_companies, "normalize_url", lambda w: "https://test.de/"
        )
        monkeypatch.setattr(
            enrich_companies,
            "fetch",
            lambda url: _ok_response(content=b"<html></html>", url=url),
        )
        monkeypatch.setattr(
            enrich_companies,
            "find_impressum_link",
            lambda *a: "https://test.de/impressum",
        )
        monkeypatch.setattr(
            enrich_companies, "extract_visible_text", lambda html: "text"
        )

        opt = types.SimpleNamespace()
        opt.text = "text"
        opt.tokens_saved = 0
        opt.summary = lambda name: "ok"
        monkeypatch.setattr(enrich_companies, "optimize_text", lambda text: opt)

        monkeypatch.setattr(
            enrich_companies,
            "extract_from_text",
            lambda text: (_ for _ in ()).throw(ApiError("HTTP 401")),
        )

        rec = {"id": 6, "company_name": "ApiErrCo", "web": "https://test.de/"}
        status, saved = enrich_companies.process_company(rec, log)

        assert status == CompanyStatus.ERROR
        assert spy_write[0]["impressum_url"] == "https://test.de/impressum"

    def test_unexpected_exception_gives_status_4(self, log, spy_write, monkeypatch):
        monkeypatch.setattr(
            enrich_companies, "normalize_url", lambda w: "https://test.de/"
        )
        monkeypatch.setattr(
            enrich_companies,
            "fetch",
            lambda url: (_ for _ in ()).throw(RuntimeError("surprise")),
        )

        rec = {"id": 7, "company_name": "BugCo", "web": "https://test.de/"}
        status, saved = enrich_companies.process_company(rec, log)

        assert status == CompanyStatus.ERROR
        assert len(spy_write) == 1

    def test_process_company_never_raises(self, log, monkeypatch):
        monkeypatch.setattr(
            enrich_companies, "normalize_url", lambda w: "https://test.de/"
        )
        monkeypatch.setattr(
            enrich_companies,
            "fetch",
            lambda url: (_ for _ in ()).throw(Exception("total chaos")),
        )
        monkeypatch.setattr(
            enrich_companies,
            "write_company_result",
            lambda *a, **kw: (_ for _ in ()).throw(ConnectionError("db down")),
        )

        rec = {"id": 8, "company_name": "ChaosCo", "web": "https://test.de/"}
        status, saved = enrich_companies.process_company(rec, log)
        assert status == CompanyStatus.ERROR
