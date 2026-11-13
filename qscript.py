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
ENV_DEBUG = "QS_DEBUG"
ENV_ZOOM_RING = "QS_ZOOM_RING"
ENV_AUTO_ZOOM = "QS_AUTO_ZOOM"

VIDEO_FPS = 25
ZOOM_FACTOR = 2.0
ZOOM_RAMP_SEC = 0.5
ZOOM_TIME_OFFSET = 0.0

_CURRENT_PAGE = None
_ZOOM_EVENTS = []
_ZOOM_T0 = None


def _reset_zoom_clock():
    global _ZOOM_T0
    del _ZOOM_EVENTS[:]
    _ZOOM_T0 = None


def _start_zoom_clock():
    global _ZOOM_T0
    _ZOOM_T0 = time.time()


def make_zoom_event(events, t0, epoch_sec, x, y):
    typ = "in" if len(events) % 2 == 0 else "out"
    t = round(epoch_sec - t0 + ZOOM_TIME_OFFSET, 3)
    t = max(t, events[-1]["t"] if events else 0.0)  # t не убывает и не отрицателен
    events.append(
        {"t": t, "x": round(float(x), 1), "y": round(float(y), 1), "type": typ}
    )
    return typ


def _on_zoom_event(payload):
    try:
        return make_zoom_event(
            _ZOOM_EVENTS,
            _ZOOM_T0,
            float(payload["ts"]) / 1000.0,
            payload["x"],
            payload["y"],
        )
    except (KeyError, TypeError, ValueError):
        return ""


INIT_SCRIPT = r"""
(() => {
  if (window.top !== window) return;
  if (window.__qsInstalled) return;
  window.__qsInstalled = true;

  const SIZE = 32;
  const R = 32;
  const TIP_X = 3, TIP_Y = 2;
  const state = { x: 100, y: 100 };
  let hideTimer = null;

  const css = `
    #__qs_hl { position: fixed; left: 0; top: 0; width: ${R * 2}px; height: ${R * 2}px;
      border-radius: 50%; background: rgba(255, 221, 0, 0.35);
      pointer-events: none; z-index: 2147483646; will-change: transform; }
    #__qs_cur { position: fixed; left: 0; top: 0; width: ${SIZE}px; height: ${SIZE}px;
      pointer-events: none; z-index: 2147483647; will-change: transform; }
    .__qs_ripple { position: fixed; box-sizing: border-box; border-radius: 50%;
      border: 3px solid rgba(255, 140, 0, 0.9); pointer-events: none;
      z-index: 2147483645; transform: translate(-50%, -50%); }
    #__qs_callout { position: fixed; top: 24px; left: 50%; transform: translateX(-50%);
      width: max-content; max-width: 80vw; box-sizing: border-box; padding: 10px 18px;
      font: 16pt Arial, sans-serif; border: 1px solid #000; border-radius: 4px;
      opacity: 0; pointer-events: none; z-index: 2147483647; }
    #__qs_zoomring { position: fixed; box-sizing: border-box; width: 64px; height: 64px;
      border-radius: 50%; border: 5px solid #00c853; pointer-events: none;
      z-index: 2147483645; transform: translate(-50%, -50%); }
  `;

  function place(x, y) {
    state.x = x; state.y = y;
    const hl = document.getElementById('__qs_hl');
    const cur = document.getElementById('__qs_cur');
    if (hl)  hl.style.transform  = `translate(${x - R}px, ${y - R}px)`;
    if (cur) cur.style.transform = `translate(${x - TIP_X}px, ${y - TIP_Y}px)`;
  }

  function build() {
    if (!document.documentElement) return;
    if (document.getElementById('__qs_cur')) return;

    const style = document.createElement('style');
    style.id = '__qs_style';
    style.textContent = css;
    document.documentElement.appendChild(style);

    const hl = document.createElement('div');
    hl.id = '__qs_hl';
    document.documentElement.appendChild(hl);

    const cur = document.createElement('div');
    cur.id = '__qs_cur';
    cur.innerHTML =
      '<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 32 32">' +
      '<path d="M3 2 L3 26 L9.5 20 L14 30 L18 28.2 L13.6 18.4 L22 18.4 Z" ' +
      'fill="#ffffff" stroke="#000000" stroke-width="2" stroke-linejoin="round"/></svg>';
    document.documentElement.appendChild(cur);

    place(state.x, state.y);
  }

  function ripple(x, y) {
    if (!document.documentElement) return;
    const el = document.createElement('div');
    el.className = '__qs_ripple';
    el.style.left = x + 'px';
    el.style.top = y + 'px';
    document.documentElement.appendChild(el);
    const anim = el.animate(
      [ { width: '0px', height: '0px', opacity: 0.9 },
        { width: (R * 2) + 'px', height: (R * 2) + 'px', opacity: 0 } ],
      { duration: 600, easing: 'ease-out', fill: 'forwards' }
    );
    anim.onfinish = () => el.remove();
  }

  function showZoomRing(x, y, type) {
    if (window.__qsNoZoomRing || !document.documentElement) return;
    const el = document.createElement('div');
    el.className = '__qs_zoomring';
    el.style.left = x + 'px';
    el.style.top = y + 'px';
    el.style.borderColor = type === 'in' ? '#00c853' : '#f44336'; // зелёное = in, красное = out
    document.documentElement.appendChild(el);
    const anim = el.animate(
        [
        { transform: 'translate(-50%, -50%) scale(0.6)', opacity: 1 },
        { transform: 'translate(-50%, -50%) scale(1.4)', opacity: 0 },
        ],
        { duration: 800, easing: 'ease-out', fill: 'forwards' },
    );
    anim.onfinish = () => el.remove();
  }

  function hideCallout(fadeMs) {
    const el = document.getElementById('__qs_callout');
    if (!el) return;
    const f = (fadeMs === undefined || fadeMs === null) ? 2000 : fadeMs;
    el.style.transition = `opacity ${f}ms ease`;
    el.style.opacity = '0';
  }

  function showCallout(text, bg, color, fadeMs, holdMs) {
    if (!document.documentElement) return;
    let el = document.getElementById('__qs_callout');
    if (!el) {
      el = document.createElement('div');
      el.id = '__qs_callout';
      document.documentElement.appendChild(el);
    }
    const f = (fadeMs === undefined || fadeMs === null) ? 2000 : fadeMs;
    el.textContent = text;
    el.style.background = bg || 'yellow';
    el.style.color = color || 'black';
    el.style.transition = 'none';
    el.style.opacity = '0';
    void el.offsetWidth;
    el.style.transition = `opacity ${f}ms ease`;
    el.style.opacity = '1';
    clearTimeout(hideTimer);
    if (holdMs !== undefined && holdMs !== null && holdMs >= 0) {
      hideTimer = setTimeout(() => hideCallout(f), f + holdMs);
    }
  }

  window.addEventListener('mousemove', e => place(e.clientX, e.clientY), true);
  window.addEventListener('mousedown', e => ripple(e.clientX, e.clientY), true);
  window.addEventListener(
    'keydown',
    (e) => {
        if (e.key !== 'F5') return;
        e.preventDefault(); // без этого F5 перезагрузит страницу
        e.stopPropagation();
        if (!e.repeat) window.__zoomAt(state.x, state.y);
    },
    true,
  );


  window.__qs = {
    moveTo: place,
    showCallout: showCallout,
    hideCallout: hideCallout,
    get x() { return state.x; },
    get y() { return state.y; },
  };

  window.__zoomAt = function (x, y) {
    const px = x === undefined || x === null ? state.x : x;
    const py = y === undefined || y === null ? state.y : y;
    if (typeof window.__zoomEmit !== 'function') return Promise.resolve('');
    return window.__zoomEmit({ x: px, y: py, ts: Date.now() }).then((type) => {
        if (type) showZoomRing(px, py, type);
        return type;
    });
  };


  build();
  document.addEventListener('DOMContentLoaded', build);
  new MutationObserver(build).observe(document, { childList: true, subtree: true });
})();
"""


def _is_nav_error(err):
    msg = str(err).lower()
    return (
        "context was destroyed" in msg
        or "navigation" in msg
        or "navigating" in msg
        or ("target closed" in msg and "navigat" in msg)
    )


def _pause(seconds):
    time.sleep(seconds)


def _debug_enabled():
    return os.environ.get(ENV_DEBUG, "0").strip().lower() in ("1", "true", "yes")


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


def ShowCallout(
    text,
    bgColor="yellow",
    textColor="black",
    fade=2000,
    hold=1500,
    wait=False,
    page=None,
):
    page = page or _CURRENT_PAGE
    if page is None:
        return
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
            loc = page.locator(selector + " >> visible=true").first
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
    raise RuntimeError(
        "Failed to obtain element's coordinates %s: %s" % (selector, last_err)
    )


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
        if "/home-error/" in page.url:
            return False
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
        print("[DEBUG] Failed to process page: %s" % e)
    if not _debug_enabled():
        return
    try:
        folder = os.path.abspath(PATH_TO_VIDEO_FOLDER)
        shot = os.path.join(folder, _safe_name(company_name) + "_debug.png")
        page.screenshot(path=shot)
        print("[DEBUG] Screenshot: %s" % shot)
    except Exception as e:
        print("[DEBUG] Failed to save screenshot: %s" % e)


def _launch_kwargs():
    headless = os.environ.get(ENV_HEADLESS, "0").strip().lower() in ("1", "true", "yes")
    kwargs = {"headless": headless}
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
    global _CURRENT_PAGE
    _CURRENT_PAGE = page
    cur = {"x": 100.0, "y": 100.0}

    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_selector(SEL_SEARCH_BOX, state="visible", timeout=30000)
    _pause(1.0)

    page.mouse.move(cur["x"], cur["y"])
    _restore_cursor(page, cur)
    ShowCallout("Company: %s" % company_name)
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
        bx, by = _element_center(page, SEL_SEARCH_BUTTON, timeout=3000)
        _move_smooth(page, cur, bx, by)
        _pause(0.3)
        _click_here(page)
    except PlaywrightError:
        page.keyboard.press("Enter")

    _wait_load(page)
    ok = _wait_for_results(page, timeout_s=15)
    retries = 0
    while not ok and "/home-error/" not in page.url and retries < 2:
        retries += 1
        print("[WARN] %s: search page error, retry %d/2" % (company_name, retries))
        ShowCallout(
            "Search page error, reloading (%d/2)" % retries, fade=500, hold=1500
        )
        _pause(2.5)
        page.reload(wait_until="domcontentloaded", timeout=30000)
        _wait_load(page)
        ok = _wait_for_results(page, timeout_s=15)
    if not ok:
        _debug_dump(page, company_name)
        if "/home-error/" in page.url:
            raise RuntimeError("DuckDuckGo blocked an automated request (error page)")
        raise RuntimeError("No results obtained")
    _restore_cursor(page, cur)
    try:
        ShowCallout("Waiting for page to load", hold=1000)
        page.wait_for_load_state("networkidle", timeout=10000)
    except PlaywrightError:
        pass
    _pause(2.0)
    ShowCallout("Opening the first result", hold=1000)

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
                if _debug_enabled():
                    failed = os.path.join(
                        folder, _safe_name(company_name) + "_FAILED.webm"
                    )
                    if os.path.exists(failed):
                        os.remove(failed)
                    shutil.move(src, failed)
                    print("[DEBUG] Failed video: %s" % failed)
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
        print("[ERROR] %s: failed to save video: %s" % (company_name, e))
        return None
