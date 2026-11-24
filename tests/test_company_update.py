import pytest

from db import CompanyStatus, EXTRACTED_COLUMNS, build_company_update

SECRET = "SECRET_VALUE"
SECRET_URL = "https://secret.example/impressum"


@pytest.fixture
def row():
    values = {c: SECRET for c in EXTRACTED_COLUMNS}
    values["raw_json"] = {"secret": SECRET}
    return values


ALL_STATUSES = [
    CompanyStatus.OK,
    CompanyStatus.NO_IMPRESSUM_LINK_FOUND,
    CompanyStatus.PARSING_FAILED,
    CompanyStatus.ADDRESS_INCOMPLETE,
    CompanyStatus.ERROR,
    CompanyStatus.NO_WEBSITE,
]


# ------------------------------------------------------ SQL contract


@pytest.mark.parametrize("status", ALL_STATUSES, ids=lambda s: s.name)
def test_placeholders_match_params(status, row):
    sql, params = build_company_update(status, SECRET_URL, row)
    assert sql.count("%s") == len(params) + 1  # +1 для WHERE id


@pytest.mark.parametrize("status", ALL_STATUSES, ids=lambda s: s.name)
def test_no_value_is_interpolated_into_sql(status, row):
    sql, _ = build_company_update(status, SECRET_URL, row)
    assert SECRET not in sql
    assert "secret.example" not in sql


@pytest.mark.parametrize("status", ALL_STATUSES, ids=lambda s: s.name)
def test_statement_targets_one_row_by_id(status, row):
    sql, _ = build_company_update(status, SECRET_URL, row)
    assert sql.startswith("UPDATE companies SET ")
    assert sql.endswith("WHERE id = %s")


@pytest.mark.parametrize("status", ALL_STATUSES, ids=lambda s: s.name)
def test_updated_at_is_always_refreshed(status, row):
    sql, _ = build_company_update(status, SECRET_URL, row)
    assert "updated_at = NOW()" in sql


# ------------------------------------------- statuses 0 / 2 / 3: write all


@pytest.mark.parametrize(
    "status",
    [CompanyStatus.OK, CompanyStatus.PARSING_FAILED, CompanyStatus.ADDRESS_INCOMPLETE],
    ids=lambda s: s.name,
)
def test_success_statuses_write_every_column(status, row):
    sql, params = build_company_update(status, SECRET_URL, row)
    for column in ("impressum_url", *EXTRACTED_COLUMNS, "status"):
        assert f"{column} = %s" in sql
    assert params[0] == SECRET_URL
    assert params[-1] == int(status)


def test_raw_json_is_wrapped_for_jsonb(row):
    from psycopg2.extras import Json

    _, params = build_company_update(CompanyStatus.OK, SECRET_URL, row)
    wrapped = [p for p in params if isinstance(p, Json)]
    assert len(wrapped) == 1
    assert wrapped[0].adapted == row["raw_json"]


# ------------------------------------------------ status 4: keep the past


def test_error_without_url_touches_only_status(row):
    sql, params = build_company_update(CompanyStatus.ERROR, None, row)
    assert not any(f"{c} = NULL" in sql for c in EXTRACTED_COLUMNS)
    assert "impressum_url" not in sql
    assert params == [int(CompanyStatus.ERROR)]


def test_error_with_known_url_updates_it(row):
    sql, params = build_company_update(CompanyStatus.ERROR, SECRET_URL, row)
    assert "impressum_url = %s" in sql
    assert not any(f"{c} = NULL" in sql for c in EXTRACTED_COLUMNS)
    assert params == [int(CompanyStatus.ERROR), SECRET_URL]


# --------------------------------------------- statuses 1 / 5: clear fields


@pytest.mark.parametrize(
    "status",
    [CompanyStatus.NO_IMPRESSUM_LINK_FOUND, CompanyStatus.NO_WEBSITE],
    ids=lambda s: s.name,
)
def test_failure_statuses_clear_extraction(status, row):
    sql, _ = build_company_update(status, SECRET_URL, row)
    assert "impressum_url = NULL" in sql
    for column in EXTRACTED_COLUMNS:
        assert f"{column} = NULL" in sql


@pytest.mark.parametrize(
    "status",
    [CompanyStatus.NO_IMPRESSUM_LINK_FOUND, CompanyStatus.NO_WEBSITE],
    ids=lambda s: s.name,
)
def test_failure_statuses_ignore_passed_url(status, row):
    """Даже если URL передали, он не должен попасть в запрос."""
    sql, params = build_company_update(status, SECRET_URL, row)
    assert "impressum_url = %s" not in sql
    assert params == [int(status)]


# -------------------------------------------------------------- guards


def test_unknown_status_is_rejected(row):
    with pytest.raises(ValueError):
        build_company_update(99, SECRET_URL, row)


def test_status_accepts_plain_int(row):
    sql, params = build_company_update(0, SECRET_URL, row)
    assert params[-1] == 0
    assert isinstance(params[-1], int)
