import argparse
import csv
import os
import sys
import time

import qscript


def preview_video(video_url, view_seconds):
    from playwright.sync_api import sync_playwright

    launch_kwargs = {"headless": False}
    channel = os.environ.get("QS_BROWSER_CHANNEL")
    if channel:
        launch_kwargs["channel"] = channel

    with sync_playwright() as p:
        browser = p.chromium.launch(**launch_kwargs)
        try:
            page = browser.new_page()
            page.goto(video_url, timeout=30000)
            if view_seconds > 0:
                time.sleep(view_seconds)
            else:
                try:
                    input(
                        f"Video demonstration is opened in browser at {video_url}\n"
                        "Press Enter in this terminal to close the browser... "
                    )
                except EOFError:
                    pass
        finally:
            browser.close()


def process_uploaded(server_url, info, timeout):
    from video_client import ProcessVideo, WaitForJob

    job_id = info.get("id")
    if job_id is None:
        raise RuntimeError("server did not return a job id (is the service updated?)")

    if info.get("process_status", "none") == "none":
        ProcessVideo(server_url, job_id)

    if timeout <= 0:
        return None

    job = WaitForJob(server_url, job_id, timeout=timeout)
    if job.get("status") != "done":
        raise RuntimeError(job.get("error") or "processing failed")
    if not job.get("processed_video_url"):
        raise RuntimeError("server returned no processed_video_url")
    return job["processed_video_url"]


def non_negative_float(value):
    try:
        number = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError("Number expected, received %r" % value)
    if number < 0:
        raise argparse.ArgumentTypeError("Value can't be negative")
    return number


def main():
    parser = argparse.ArgumentParser(description="CSV-driven browser video recorder")
    parser.add_argument(
        "--csv", default="input.csv", help="Path to input CSV (default input.csv)"
    )
    parser.add_argument(
        "--out", default=None, help="Directory for video (default value in qscript)"
    )
    parser.add_argument(
        "--server",
        default=None,
        help="URL, example: http://127.0.0.1:8000; "
        "if specified, each video is loaded and opened in the browser",
    )
    parser.add_argument(
        "--view-seconds",
        type=non_negative_float,
        default=10,
        help="how many seconds should the browser be kept open during a preview; "
        "0 = until Enter is pressed (default 10)",
    )
    args = parser.parse_args()

    if args.server and not args.server.lower().startswith(("http://", "https://")):
        parser.error("--server must start with http:// or https://")

    if args.out:
        qscript.PATH_TO_VIDEO_FOLDER = args.out

    total = 0
    saved = 0
    uploaded = 0

    with open(args.csv, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f, delimiter=";", quotechar='"')
        for i, row in enumerate(reader, start=1):
            total += 1
            company = row.get("Company Name", "")
            print("[%d] Processing: %s" % (i, company))
            try:
                path = qscript.CaptureVideo_Magento(
                    row["URL"],
                    row["Company Name"],
                    row["Language"],
                    row["Country"],
                    row["Postcode"],
                    row["City"],
                    row["Street"],
                    row["HouseNo"],
                )
            except Exception as e:
                print("[%d] FAILED: %s" % (i, e), file=sys.stderr)
                continue

            if not path:
                print("[%d] FAILED (video not saved)" % i, file=sys.stderr)
                continue

            saved += 1
            print("[%d] Saved: %s" % (i, path))

            if not args.server:
                continue

            try:
                from video_client import UploadVideo

                video_url = UploadVideo(args.server, path, company)
                uploaded += 1
                print("[%d] Uploaded: %s" % (i, video_url))
            except Exception as e:
                print("[%d] UPLOAD FAILED: %s" % (i, e), file=sys.stderr)
                continue

            try:
                preview_video(video_url, args.view_seconds)
            except Exception as e:
                print("[%d] PREVIEW FAILED: %s" % (i, e), file=sys.stderr)

    summary = "Done: %d of %d videos saved." % (saved, total)
    if args.server:
        summary += " Uploaded: %d of %d." % (uploaded, saved)
    print(summary)

    ok = saved == total and (not args.server or uploaded == saved)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
