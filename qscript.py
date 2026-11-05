import math
import os
import random
import re
import shutil
import time

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

PATH_TO_VIDEO_FOLDER = "./video"

VIDEO_SIZE = {"width": 1280, "height": 720}

SEL_SEARCH_BOX = 'textarea[name="q"]'
SEL_SEARCH_BUTTON = 'form button[type="submit"]'
SEL_FIRST_RESULT = 'a[data-testid="result-title-a"]'

ENV_HEADLESS = "QS_HEADLESS"
ENV_CHANNEL = "QS_BROWSER_CHANNEL"

_script_path = os.path.join(os.path.dirname(__file__), "cursor_script.js")
with open(_script_path, "r", encoding="utf-8") as _f:
    INIT_SCRIPT = _f.read()


def _is_nav_error(err):
    msg = str(err).lower()
    return (
        "context was destroyed" in msg
        or "navigation" in msg
        or "navigating" in msg
        or "target closed" in msg and "navigat" in msg
    )


def _pause(seconds):
    time.sleep(seconds)


def _safe_name(name):
    name = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", name or "").strip().strip(".")
    return name or "video"


def _evaluate_safe(page, script, arg=None, retries=3):
    for _ in range(retries):
        try:
            if arg is None:
                return page.evaluate(script)
            return page.evaluate(script, arg)
        except PlaywrightError as e:
            if _is_nav_error(e):
                time.sleep(0.4)
                continue
            return None
    return None


def _restore_cursor(page, cur):
    _evaluate_safe(
        page,
        "([x, y]) => window.__qs && window.__qs.moveTo(x, y)",
        [cur["x"], cur["y"]],
    )


def ShowCallout(page, text, bgColor="yellow", textColor="black", fade=2000,
                hold=1500, wait=False):
    _evaluate_safe(
        page,
        "([t, bg, c, f, h]) => window.__qs && window.__qs.showCallout(t, bg, c, f, h)",
        [text, bgColor, textColor, fade, hold],
    )
    if wait:
        time.sleep((fade * 2 + hold) / 1000.0)


def _ease(t):
    return t * t * (3 - 2 * t)


def _move_smooth(page, cur, x1, y1, duration=None):
    x0, y0 = cur["x"], cur["y"]
    dist = math.hypot(x1 - x0, y1 - y0)
    if duration is None:
        duration = min(1.5, max(0.6, dist / 800.0))
    steps = max(25, int(duration * 60))
    curve = random.uniform(-1, 1) * min(40.0, dist * 0.08)
    nx, ny = (-(y1 - y0), (x1 - x0))
    norm = math.hypot(nx, ny) or 1.0
    nx, ny = nx / norm, ny / norm

    for i in range(1, steps + 1):
        t = i / float(steps)
        e = _ease(t)
        bend = math.sin(math.pi * e) * curve
        x = x0 + (x1 - x0) * e + nx * bend
        y = y0 + (y1 - y0) * e + ny * bend
        page.mouse.move(x, y)
        time.sleep(duration / steps)

    page.mouse.move(x1, y1)
    cur["x"], cur["y"] = x1, y1


def _element_center(page, selector, timeout=30000):
    last_err = None
    for _ in range(6):
        try:
            loc = page.locator(selector).first
            loc.wait_for(state="visible", timeout=timeout)
            loc.scroll_into_view_if_needed(timeout=5000)
            box = loc.bounding_box()
            if box:
                return box["x"] + box["width"] / 2.0, box["y"] + box["height"] / 2.0
        except PlaywrightError as e:
            last_err = e
            if _is_nav_error(e):
                time.sleep(0.5)
                continue
            raise
        time.sleep(0.3)
    raise RuntimeError("Не удалось получить координаты элемента %s: %s" % (selector, last_err))


def _click_here(page):
    page.mouse.down()
    time.sleep(random.uniform(0.06, 0.13))
    page.mouse.up()


def _type_human(page, text):
    for ch in text:
        page.keyboard.type(ch)
        delay = random.uniform(0.06, 0.18)
        if ch in " ,.-":
            delay += random.uniform(0.05, 0.15)
        if random.random() < 0.06:
            delay += random.uniform(0.2, 0.35)
        time.sleep(delay)


def _wait_for_results(page, timeout_s=45):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            loc = page.locator(SEL_FIRST_RESULT)
            if loc.count() > 0 and loc.first.is_visible():
                return True
        except PlaywrightError as e:
            if not _is_nav_error(e):
                raise
        time.sleep(0.5)
    return False


def _wait_load(page, timeout=30000):
    try:
        page.wait_for_load_state("domcontentloaded", timeout=timeout)
    except PlaywrightError:
        pass


def _debug_dump(page, company_name):
    try:
        print("[DEBUG] URL: %s" % page.url)
        print("[DEBUG] Title: %s" % page.title())
        text = page.evaluate(
            "() => ((document.body && document.body.innerText) || '').slice(0, 300)"
        )
        print("[DEBUG] Page text: %s" % str(text).replace("\n", " "))
    except Exception as e:
        print("[DEBUG] Не удалось прочитать страницу: %s" % e)
    try:
        folder = os.path.abspath(PATH_TO_VIDEO_FOLDER)
        shot = os.path.join(folder, _safe_name(company_name) + "_debug.png")
        page.screenshot(path=shot)
        print("[DEBUG] Screenshot: %s" % shot)
    except Exception as e:
        print("[DEBUG] Не удалось сохранить скриншот: %s" % e)


def _launch_kwargs():
    kwargs = {"headless": os.environ.get(ENV_HEADLESS, "0") != "0"}
    channel = os.environ.get(ENV_CHANNEL)
    if channel:
        kwargs["channel"] = channel
    return kwargs


def _context_kwargs(folder, language):
    kwargs = {
        "viewport": VIDEO_SIZE,
        "record_video_dir": folder,
        "record_video_size": VIDEO_SIZE,
    }
    if language and re.match(r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*$", language.strip()):
        kwargs["locale"] = language.strip()
    return kwargs


def _run_scenario(page, url, company_name, city):
    cur = {"x": 100.0, "y": 100.0}

    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_selector(SEL_SEARCH_BOX, state="visible", timeout=30000)
    _pause(1.0)

    page.mouse.move(cur["x"], cur["y"])
    _restore_cursor(page, cur)
    ShowCallout(page, "Company: %s" % company_name)
    _pause(0.6)

    sx, sy = _element_center(page, SEL_SEARCH_BOX)
    _move_smooth(page, cur, sx, sy)
    _pause(0.3)

    _click_here(page)
    _pause(0.4)
    city = (city or "").strip()
    query = "Magento developer in %s" % city if city else "Magento developer"
    _type_human(page, query)
    _pause(0.8)

    try:
        bx, by = _element_center(page, SEL_SEARCH_BUTTON, timeout=10000)
        _move_smooth(page, cur, bx, by)
        _pause(0.3)
        _click_here(page)
    except PlaywrightError:
        page.keyboard.press("Enter")

    _wait_load(page)
    if not _wait_for_results(page):
        _debug_dump(page, company_name)
        raise RuntimeError("Результаты поиска не появились")
    _restore_cursor(page, cur)
    _pause(0.8)
    ShowCallout(page, "Opening the first result", hold=1000)

    rx, ry = _element_center(page, SEL_FIRST_RESULT)
    _move_smooth(page, cur, rx, ry)
    _pause(0.4)
    _click_here(page)

    _wait_load(page)
    _pause(3.5)


def CaptureVideo_Magento(
    url,
    company_name,
    language,
    country,
    postcode,
    city,
    street,
    house_no,
):
    folder = os.path.abspath(PATH_TO_VIDEO_FOLDER)
    os.makedirs(folder, exist_ok=True)
    dst = os.path.join(folder, _safe_name(company_name) + ".webm")

    scenario_ok = False
    src = None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(**_launch_kwargs())
            try:
                context = browser.new_context(**_context_kwargs(folder, language))
                video = None
                try:
                    context.add_init_script(INIT_SCRIPT)
                    page = context.new_page()
                    video = page.video
                    try:
                        _run_scenario(page, url, company_name, city)
                        scenario_ok = True
                    except Exception as e:
                        print("[ERROR] %s: %s" % (company_name, e))
                finally:
                    context.close()
                    if video is not None:
                        try:
                            src = video.path()
                        except Exception:
                            src = None
            finally:
                browser.close()
    except Exception as e:
        print("[ERROR] %s: %s" % (company_name, e))
        scenario_ok = False

    if not scenario_ok:
        if src and os.path.exists(src):
            try:
                if os.environ.get("QS_KEEP_FAILED") == "1":
                    failed = os.path.join(folder, _safe_name(company_name) + "_FAILED.webm")
                    if os.path.exists(failed):
                        os.remove(failed)
                    shutil.move(src, failed)
                    print("[DEBUG] Видео сбоя: %s" % failed)
                else:
                    os.remove(src)
            except OSError:
                pass
        return None

    try:
        if not src or not os.path.exists(src):
            return None
        if os.path.exists(dst):
            os.remove(dst)
        shutil.move(src, dst)
        if os.path.getsize(dst) <= 0:
            return None
        return dst
    except Exception as e:
        print("[ERROR] %s: не удалось сохранить видео: %s" % (company_name, e))
        return None