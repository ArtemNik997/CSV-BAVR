import argparse
import csv
import os
import sys
import time

from qscript import CaptureVideo_Magento


def preview_video(video_url: str, view_seconds: float):
    from playwright.sync_api import sync_playwright
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        try:
            page = browser.new_page()
            page.goto(video_url, timeout=30000)
            
            if view_seconds > 0:
                time.sleep(view_seconds)
            else:
                try:
                    input("Нажмите Enter, чтобы закрыть браузер... ")
                except EOFError:
                    pass
        finally:
            browser.close()


def main():
    parser = argparse.ArgumentParser(
        description="CSV-driven Browser Automation Video Recorder with Upload & Preview"
    )
    
    parser.add_argument("--csv", default="input.csv", help="Path to input CSV file")
    parser.add_argument("--out", default="./video", help="Output directory for videos")
    parser.add_argument(
        "--server", 
        default=None, 
        help="URL of the web service (e.g., http://127.0.0.1:8000)"
    )
    parser.add_argument(
        "--view-seconds", 
        type=float, 
        default=10, 
        help="Preview duration in seconds (>0) or wait for Enter (0)"
    )
    
    args = parser.parse_args()
    
    if args.view_seconds < 0:
        parser.error("--view-seconds cannot be negative")
        
    if args.server and not args.server.startswith(("http://", "https://")):
        parser.error("--server must start with http:// or https://")
    
    os.makedirs(args.out, exist_ok=True)
    
    try:
        with open(args.csv, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f, delimiter=';')
            rows = list(reader)
    except FileNotFoundError:
        print(f"Error: CSV file '{args.csv}' not found.", file=sys.stderr)
        sys.exit(1)
    
    saved_count = 0
    upload_count = 0
    
    for i, row in enumerate(rows, 1):
        company_name = row.get('Company Name', '').strip()
        city = row.get('City', '').strip()
        
        if not company_name or not city:
            print(f"[{i}] Skipping row {i}: missing Company Name or City", file=sys.stderr)
            continue
            
        print(f"[{i}] Processing: {company_name} ({city})")
        
        
        path = CaptureVideo_Magento(city, args.out)
        
        if path:
            print(f"[{i}] Saved: {path}")
            saved_count += 1
            
            if args.server:
                try:
                    from video_client import UploadVideo
                    
                    url = UploadVideo(args.server, path, company_name)
                    print(f"[{i}] Uploaded: {url}")
                    upload_count += 1
                    
                    preview_video(url, args.view_seconds)
                    
                except Exception as e:
                    print(f"[{i}] UPLOAD/PREVIEW FAILED: {e}", file=sys.stderr)
        else:
            print(f"[{i}] Failed to record video for {company_name}", file=sys.stderr)
    
    print(f"\nDone. Saved: {saved_count}/{len(rows)}")
    if args.server:
        print(f"Uploaded: {upload_count}/{saved_count}")


if __name__ == 'main':
    main()