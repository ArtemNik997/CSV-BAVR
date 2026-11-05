import argparse
import csv
import sys

import qscript


def main():
    parser = argparse.ArgumentParser(description="CSV-driven browser video recorder")
    parser.add_argument("--csv", default="input.csv", help="путь к входному CSV (по умолчанию input.csv)")
    parser.add_argument("--out", default=None, help="папка для видео (по умолчанию значение из qscript)")
    args = parser.parse_args()

    if args.out:
        qscript.PATH_TO_VIDEO_FOLDER = args.out

    total = 0
    saved = 0

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

            if path:
                saved += 1
                print("[%d] Saved: %s" % (i, path))
            else:
                print("[%d] FAILED (video not saved)" % i, file=sys.stderr)

    print("Done: %d of %d videos saved." % (saved, total))
    return 0 if saved == total else 1


if __name__ == "__main__":
    sys.exit(main())
