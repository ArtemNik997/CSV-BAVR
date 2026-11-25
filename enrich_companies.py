import argparse
import logging
import os
import sys
import time

from db import CompanyStatus, connect, write_company_result
from impressum import (
    normalize_url,
    fetch,
    find_impressum_link,
    extract_visible_text,
    optimize_text,
    extract_from_text,
    decide_status,
    map_to_row,
    FetchError,
    ApiError,
)

POLITENESS_DELAY = 1.5
LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(message)s"
LOG_DATEFMT = "%H:%M:%S"


def setup_logging(verbose: bool) -> logging.Logger:
    logger = logging.getLogger("enrich")
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)

    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=LOG_DATEFMT))
        logger.addHandler(handler)

    return logger


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Enriching company data via the DeepSeek API"
    )
    parser.add_argument(
        "--company",
        metavar="NAME",
        help="Process only this company (exact match)",
    )
    parser.add_argument(
        "--limit", type=int, metavar="N", help="Process no more than N companies"
    )
    parser.add_argument(
        "--status",
        type=int,
        choices=range(6),
        metavar="N",
        help="Process only companies with this status (0-5)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Include already successful companies (status 0) in the sample",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Detailed output (DEBUG level)"
    )

    args = parser.parse_args(argv)

    if args.limit is not None and args.limit <= 0:
        parser.error("--limit must be a positive number")

    return args


def build_select_query(args):
    clauses = []
    params = []

    if args.company is not None:
        clauses.append("company_name = %s")
        params.append(args.company)

        if args.status is not None:
            clauses.append("status = %s")
            params.append(int(args.status))

    elif args.status is not None:
        clauses.append("status = %s")
        params.append(int(args.status))

    elif not args.force:
        clauses.append("(status IS NULL OR status <> %s)")
        params.append(int(CompanyStatus.OK))

    sql = "SELECT id, company_name, web FROM companies"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)

    sql += " ORDER BY id"

    if args.limit is not None:
        sql += " LIMIT %s"
        params.append(int(args.limit))

    return sql, params


def fetch_companies(args, log):
    sql, params = build_select_query(args)
    log.debug("SQL: %s | Params: %s", sql, params)

    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
    finally:
        conn.close()

    return rows


def process_company(rec: dict, log: logging.Logger) -> tuple[int, int]:
    cid = rec["id"]
    name = rec["company_name"]
    raw_web = rec.get("web")

    impressum_url = None
    tokens_saved = 0

    try:
        url = normalize_url(raw_web)
        if url is None:
            log.warning("%s | no website (web is empty)", name)
            write_company_result(cid, CompanyStatus.NO_WEBSITE)
            return CompanyStatus.NO_WEBSITE, 0

        log.info("%s | loading url: %s", name, url)
        home_resp = fetch(url)

        impressum_url = find_impressum_link(home_resp.content, home_resp.url)
        if impressum_url is None:
            log.warning("%s | link to Impressum not found on %s", name, home_resp.url)
            write_company_result(cid, CompanyStatus.NO_IMPRESSUM_LINK_FOUND)
            return CompanyStatus.NO_IMPRESSUM_LINK_FOUND, 0

        log.info("%s | Impressum found: %s", name, impressum_url)

        page_resp = fetch(impressum_url)
        text = extract_visible_text(page_resp.content)

        optimized = optimize_text(text)
        tokens_saved = optimized.tokens_saved
        log.info("%s", optimized.summary(name))

        data = extract_from_text(optimized.text)

        status = decide_status(data)
        row = map_to_row(data, name)

        log.info("%s | status=%d (%s)", name, int(status), CompanyStatus(status).name)
        write_company_result(cid, status, impressum_url, row)

        return status, tokens_saved

    except (FetchError, ApiError) as e:
        log.error("%s | %s", name, e)
        save_error(cid, impressum_url, log)
        return CompanyStatus.ERROR, tokens_saved

    except Exception:
        log.exception("%s | unknown error", name)
        save_error(cid, impressum_url, log)
        return CompanyStatus.ERROR, tokens_saved


def save_error(cid, impressum_url, log):
    try:
        write_company_result(cid, CompanyStatus.ERROR, impressum_url)
    except Exception:
        log.exception("Failed to save status ERROR for id=%s", cid)


def main(argv=None) -> int:
    args = parse_args(argv)
    log = setup_logging(args.verbose)

    if not os.environ.get("DEEPSEEK_API_KEY"):
        log.error(
            "DEEPSEEK_API_KEY is not set. " "Add it to .env and restart the stack."
        )
        return 2

    try:
        rows = fetch_companies(args, log)
    except Exception as e:
        log.error("Failed to connect to the database: %s", e)
        return 2

    if not rows:
        log.info("No companies to process")
        return 0

    log.info("Companies selected: %d", len(rows))

    counts = {}
    total_saved = 0

    for idx, rec in enumerate(rows):
        status, saved = process_company(rec, log)

        counts[status] = counts.get(status, 0) + 1
        total_saved += saved

        if idx < len(rows) - 1:
            time.sleep(POLITENESS_DELAY)

    log.info("=" * 50)
    log.info("Companies processed: %d", len(rows))
    for code in sorted(counts):
        try:
            label = CompanyStatus(code).name
        except ValueError:
            label = f"UNKNOWN({code})"
        log.info("  %-25s (%d): %d", label, code, counts[code])
    log.info("Total token savings: ~%d", total_saved)
    log.info("=" * 50)

    return 0


if __name__ == "__main__":
    sys.exit(main())
