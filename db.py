import os
import time
from enum import IntEnum

import psycopg2
from psycopg2.extras import RealDictCursor


class CompanyStatus(IntEnum):
    OK = 0
    NO_IMPRESSUM_LINK_FOUND = 1
    PARSING_FAILED = 2
    ADDRESS_INCOMPLETE = 3
    ERROR = 4
    NO_WEBSITE = 5


COMPANIES_COLUMNS = (
    ("language", "TEXT"),
    ("country", "TEXT"),
    ("postcode", "TEXT"),
    ("city", "TEXT"),
    ("street", "TEXT"),
    ("house_no", "TEXT"),
    ("web", "TEXT"),
    ("status", "INTEGER"),
    ("impressum_url", "TEXT"),
    ("legal_name", "TEXT"),
    ("postal_address", "TEXT"),
    ("handelsregister", "TEXT"),
    ("vat_number", "TEXT"),
    ("phone", "TEXT"),
    ("email", "TEXT"),
    ("responsible_person", "TEXT"),
    ("raw_json", "JSONB"),
    ("created_at", "TIMESTAMPTZ NOT NULL DEFAULT NOW()"),
    ("updated_at", "TIMESTAMPTZ NOT NULL DEFAULT NOW()"),
)

_COLS_SQL = ",\n".join(f"{n} {t}" for n, t in COMPANIES_COLUMNS)
_ADD_SQL = ",\n".join(f"ADD COLUMN IF NOT EXISTS {n} {t}" for n, t in COMPANIES_COLUMNS)


def _db_params():
    try:
        return dict(
            host=os.environ["DB_HOST"],
            port=int(os.environ.get("DB_PORT", "5432")),
            dbname=os.environ["DB_NAME"],
            user=os.environ["DB_USER"],
            password=os.environ["DB_PASSWORD"],
            connect_timeout=5,
        )
    except KeyError as e:
        raise RuntimeError(f"Environment variable {e.args[0]} is not set")


def connect():
    return psycopg2.connect(cursor_factory=RealDictCursor, **_db_params())


def init_db(retries=60, delay=2):
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            conn = connect()
            break
        except psycopg2.OperationalError as e:
            last_error = e
            print(f"[init_db] DB not ready ({attempt}/{retries}): {e}", flush=True)
            time.sleep(delay)
    else:
        raise RuntimeError("Database is not available") from last_error

    try:
        with conn, conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS videos (
                    id SERIAL PRIMARY KEY,
                    company_name TEXT NOT NULL UNIQUE,
                    file_path TEXT NOT NULL,
                    video_url TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            cur.execute("""
                ALTER TABLE videos
                    ADD COLUMN IF NOT EXISTS zoom_sidecar JSONB,
                    ADD COLUMN IF NOT EXISTS process_status TEXT NOT NULL DEFAULT 'none',
                    ADD COLUMN IF NOT EXISTS process_error TEXT,
                    ADD COLUMN IF NOT EXISTS processed_file_path TEXT,
                    ADD COLUMN IF NOT EXISTS processed_video_url TEXT,
                    ADD COLUMN IF NOT EXISTS processed_at TIMESTAMPTZ
            """)
            cur.execute("""
                UPDATE videos
                SET process_status = 'failed',
                    process_error = 'interrupted by service restart'
                WHERE process_status IN ('pending', 'processing')
            """)
            cur.execute(f"""
                CREATE TABLE IF NOT EXISTS companies (
                    id SERIAL PRIMARY KEY,
                    company_name TEXT NOT NULL UNIQUE,
                    {_COLS_SQL}
                )
            """)
            cur.execute(f"ALTER TABLE companies {_ADD_SQL}")

    finally:
        conn.close()
