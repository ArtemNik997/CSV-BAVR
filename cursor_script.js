(() => {
  if (window.top !== window) return;
  if (window.__qsInstalled) return;
  window.__qsInstalled = true;

  const SIZE = 32;
  const R = 32;
  const TIP_X = 3,
    TIP_Y = 2;
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
  `;

  function place(x, y) {
    state.x = x;
    state.y = y;
    const hl = document.getElementById('__qs_hl');
    const cur = document.getElementById('__qs_cur');
    if (hl) hl.style.transform = `translate(${x - R}px, ${y - R}px)`;
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
      [
        { width: '0px', height: '0px', opacity: 0.9 },
        { width: R * 2 + 'px', height: R * 2 + 'px', opacity: 0 },
      ],
      { duration: 600, easing: 'ease-out', fill: 'forwards' },
    );
    anim.onfinish = () => el.remove();
  }

  function hideCallout(fadeMs) {
    const el = document.getElementById('__qs_callout');
    if (!el) return;
    const f = fadeMs === undefined || fadeMs === null ? 2000 : fadeMs;
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
    const f = fadeMs === undefined || fadeMs === null ? 2000 : fadeMs;
    el.textContent = text;
    el.style.background = bg || 'yellow';
    el.style.color = color || 'black';
    el.style.transition = 'none';
    el.style.opacity = '0';
    void el.offsetWidth; // reflow
    el.style.transition = `opacity ${f}ms ease`;
    el.style.opacity = '1'; // fade-in
    clearTimeout(hideTimer);
    if (holdMs !== undefined && holdMs !== null && holdMs >= 0) {
      hideTimer = setTimeout(() => hideCallout(f), f + holdMs); // fade-out
    }
  }

  window.addEventListener(
    'mousemove',
    (e) => place(e.clientX, e.clientY),
    true,
  );
  window.addEventListener(
    'mousedown',
    (e) => ripple(e.clientX, e.clientY),
    true,
  );

  window.__qs = {
    moveTo: place,
    showCallout: showCallout,
    hideCallout: hideCallout,
    get x() {
      return state.x;
    },
    get y() {
      return state.y;
    },
  };

  build();
  document.addEventListener('DOMContentLoaded', build);
  new MutationObserver(build).observe(document, {
    childList: true,
    subtree: true,
  });
})();
