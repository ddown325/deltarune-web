#!/usr/bin/env python3
"""
boot_harness.py — reusable headless boot harness for the DELTARUNE web port
(the ../drweb repo is READ-ONLY: we only serve it, never edit it).

Step-1 proven recipe (recreated after sandbox reset):
1. Ensures the repo is served on http://127.0.0.1:8901 (starts a detached
   `python3 -m http.server` if nothing is listening; server is left running).
2. Launches Playwright chromium headless and PROBES software-WebGL launch
   args until a WebGL context can actually be created, in this order:
      A) --enable-unsafe-swiftshader       (winner on this box)
      B) --use-gl=angle --use-angle=swiftshader
      C) A + B combined
   (every attempt also carries env flags: --no-sandbox, --disable-dev-shm-usage,
   --mute-audio, --autoplay-policy=no-user-gesture-required)
3. Boots index.html?c=<chapter>&hm=... inside browser.new_context(
   service_workers="block") — stops sw.js asset caching — with a rAF
   frame-counter injected at document-start.
4. Captures every console message (game prints via Module.print -> console.log),
   pageerror and renderer-crash event, timestamped relative to page.goto().
5. Waits for the "Entering main loop." milestone (timeboxed, default 180 s).
6. Holds the page, samples the frame counter, takes screenshots and verifies
   the canvas is really rendering (non-blank + pixel delta between shots).

Importable for later steps:
    from boot_harness import ensure_server, probe_webgl, make_game_context, BootLog
CLI:  python3 boot_harness.py --chapter 1 --log boot1.log --shot boot1.png
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import time
import urllib.request
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

try:
    from PIL import Image, ImageChops
    HAVE_PIL = True
except ImportError:  # pragma: no cover
    HAVE_PIL = False

E2E_DIR = Path(__file__).resolve().parent
LOG_DIR = E2E_DIR / "logs"
SHOT_DIR = E2E_DIR / "shots"
DRWEB_DIR = E2E_DIR.parent / "drweb"          # READ-ONLY repo, served as-is
BASE_URL = "http://127.0.0.1:8901"
PORT = 8901

# --------------------------------------------------------------------------- #
# launch-arg candidates, probed in order until a WebGL context works
# --------------------------------------------------------------------------- #
BASE_ARGS = [  # environment flags, always present (sandbox/shm/audio policy)
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--mute-audio",
    "--autoplay-policy=no-user-gesture-required",
]
WEBGL_ARG_CANDIDATES = [
    ("unsafe-swiftshader",       ["--enable-unsafe-swiftshader"]),
    ("angle+swiftshader",        ["--use-gl=angle", "--use-angle=swiftshader"]),
    ("unsafe-swiftshader+angle", ["--enable-unsafe-swiftshader",
                                  "--use-gl=angle", "--use-angle=swiftshader"]),
]

# console lines that mark boot milestones: (label, substring-to-match)
MILESTONES = [
    ("banner",       "[deltarune-web]"),            # port banner (first line)
    ("sw_blocked",   "Service Worker registration blocked"),
    ("interception", "File interception is active"),  # loader starts, chapter fetch begins
    ("chapter_100",  "loaded 100%"),                # chapter archive fully downloaded
    ("game_loaded",  "Success Loaded game file"),   # assembled+patched data handed to runner
    ("runner_init",  "WGS_RunnerInit called"),      # GM runner bootstrapped
    ("webgl_ctx",    "OpenGL: version string"),     # game created its WebGL context
    ("audio_loaded", "Audio group 1 -> Loaded"),
    ("MAIN_LOOP",    "Entering main loop"),         # <- boot complete
]

WEBGL_PROBE_JS = """
() => {
  const c = document.createElement('canvas');
  const g = c.getContext('webgl') || c.getContext('experimental-webgl');
  if (!g) return null;
  const dbg = g.getExtension('WEBGL_debug_renderer_info');
  return { version: String(g.getParameter(g.VERSION)),
           renderer: dbg ? String(g.getParameter(dbg.UNMASKED_RENDERER_WEBGL)) : '(no debug info)' };
}
"""

# injected at document-start on every navigation: counts rAF callbacks
FRAME_PROBE_INIT = """
(() => {
  const p = (window.__drProbe = { frames: 0, lastTs: 0, t0: performance.now() });
  const tick = (ts) => { p.frames++; p.lastTs = ts; requestAnimationFrame(tick); };
  requestAnimationFrame(tick);
})();
"""


# --------------------------------------------------------------------------- #
# static server (serve-only; repo is never modified)
# --------------------------------------------------------------------------- #
def http_ok(url: str, timeout: float = 3.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def ensure_server(start_if_needed: bool = True) -> bool:
    """True if BASE_URL serves the repo; optionally starts a detached server."""
    if http_ok(f"{BASE_URL}/index.html"):
        return True
    if not start_if_needed:
        return False
    assert DRWEB_DIR.is_dir(), f"repo missing: {DRWEB_DIR}"
    LOG_DIR.mkdir(exist_ok=True)
    with open(LOG_DIR / "server.log", "ab") as logf:
        subprocess.Popen(
            [sys.executable, "-m", "http.server", str(PORT),
             "--bind", "127.0.0.1", "-d", str(DRWEB_DIR)],
            stdout=logf, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, start_new_session=True,
        )
    for _ in range(50):
        if http_ok(f"{BASE_URL}/index.html"):
            return True
        time.sleep(0.2)
    return False


# --------------------------------------------------------------------------- #
# event log
# --------------------------------------------------------------------------- #
@dataclass
class BootLog:
    """Captured browser-side events; t relative to page.goto() start."""
    events: list = field(default_factory=list)      # (t_rel_s, kind, text)
    milestones: dict = field(default_factory=dict)  # label -> t_rel_s (first hit)
    pageerrors: list = field(default_factory=list)
    crashed: bool = False
    crash_t: float | None = None

    def note(self, t: float, kind: str, text: str) -> None:
        self.events.append((t, kind, text))
        for label, needle in MILESTONES:
            if label not in self.milestones and needle in text:
                self.milestones[label] = t

    def dump(self, path: Path, header_lines: list[str], summary: dict) -> None:
        with open(path, "w", encoding="utf-8") as f:
            for line in header_lines:
                f.write(f"# {line}\n")
            f.write("# t = seconds since page.goto() started\n")
            for t, kind, text in self.events:
                f.write(f"[{t:8.2f}s][{kind:<8}] {text}\n")
            f.write("\n===== MILESTONES (first occurrence) =====\n")
            for label, _ in MILESTONES:
                if label in self.milestones:
                    f.write(f"  {label:<13} {self.milestones[label]:8.2f}s\n")
                else:
                    f.write(f"  {label:<13} (not seen)\n")
            f.write("\n===== SUMMARY =====\n")
            f.write(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")

    def tail(self, n: int = 25) -> list[tuple[float, str, str]]:
        return self.events[-n:]


# --------------------------------------------------------------------------- #
# screenshot analysis (PIL)
# --------------------------------------------------------------------------- #
def analyze_png(path: Path) -> dict:
    """Non-blankness metrics for a screenshot (text-on-black passes easily)."""
    if not HAVE_PIL:
        return {"path": str(path), "error": "PIL unavailable"}
    im = Image.open(path).convert("RGB")
    w, h = im.size
    px = list(im.getdata())
    n = len(px)
    (top, topn) = Counter(px).most_common(1)[0]
    lum = [0.299 * r + 0.587 * g + 0.114 * b for (r, g, b) in px]
    mean = sum(lum) / n
    std = math.sqrt(sum((v - mean) ** 2 for v in lum) / n)
    uniq = len(Counter(px))
    frac_changed = 1.0 - topn / n
    return {
        "path": str(path), "size": f"{w}x{h}",
        "unique_colors": uniq, "dominant_color": top,
        "frac_changed": round(frac_changed, 5),
        "lum_mean": round(mean, 2), "lum_std": round(std, 2),
        "nonblank": bool(uniq >= 8 and frac_changed >= 0.001 and std >= 2.0),
    }


def diff_pngs(a: Path, b: Path) -> float | None:
    """Fraction of pixels that changed (>2/255 luminance) between two shots."""
    if not HAVE_PIL:
        return None
    ia, ib = Image.open(a).convert("RGB"), Image.open(b).convert("RGB")
    if ia.size != ib.size:
        return None
    hist = ImageChops.difference(ia, ib).convert("L").histogram()
    total = sum(hist)
    changed = sum(hist[3:])
    return round(changed / total, 5)


# --------------------------------------------------------------------------- #
# WebGL launch-arg probe
# --------------------------------------------------------------------------- #
def probe_webgl(pw, headless: bool = True):
    """Try candidates until about:blank can create a WebGL context.
    Returns (browser, label, full_args, probe_result_dict) — browser stays open."""
    last_err = None
    for label, extra in WEBGL_ARG_CANDIDATES:
        args = BASE_ARGS + extra
        browser = None
        try:
            browser = pw.chromium.launch(headless=headless, args=args)
            ctx = browser.new_context()
            page = ctx.new_page()
            page.goto("about:blank")
            res = page.evaluate(WEBGL_PROBE_JS)
            ctx.close()
            if res:
                return browser, label, args, res
            browser.close()
        except Exception as e:  # noqa: BLE001
            last_err = e
            if browser:
                try:
                    browser.close()
                except Exception:
                    pass
    raise RuntimeError(f"no launch-arg set produced a WebGL context (last error: {last_err})")


# --------------------------------------------------------------------------- #
# page/context factory used by every test (boot + hazard tests)
# --------------------------------------------------------------------------- #
def make_game_context(browser, hard_mode: bool | None = None, extra_init_js: str = ""):
    """Context with sw.js blocked, rAF frame counter, optional hard-mode flag.
    hard_mode=True sets localStorage 'dr_webport_hardmode'='1' BEFORE any page
    script runs (the selector reads it at parse time)."""
    context = browser.new_context(
        service_workers="block",              # stops sw.js caching
        viewport={"width": 1280, "height": 720},
    )
    init = FRAME_PROBE_INIT
    if hard_mode is not None:
        init += f"\ntry {{ localStorage.setItem('dr_webport_hardmode', '{'1' if hard_mode else '0'}'); }} catch (e) {{}}"
    if extra_init_js:
        init += "\n" + extra_init_js
    context.add_init_script(init)
    return context


def hook_page(page, blog: BootLog, t_load_ref: list, verbose: bool = True,
              extra_milestones: list | None = None):
    """Attach console/pageerror/crash/requestfailed handlers to a page."""
    milestones = list(MILESTONES)
    if extra_milestones:
        milestones += extra_milestones

    def _note(t, kind, text):
        blog.events.append((t, kind, text))
        for label, needle in milestones:
            if needle and label not in blog.milestones and needle in text:
                blog.milestones[label] = t

    def _on_console(msg) -> None:
        try:
            text = msg.text
        except Exception:
            text = "<unreadable>"
        t = time.time() - t_load_ref[0]
        _note(t, msg.type, text)
        if verbose and msg.type in ("log", "error", "warning"):
            print(f"  [{t:6.2f}s][{msg.type[:4]}] {text[:150]}")

    def _on_pageerror(exc) -> None:
        t = time.time() - t_load_ref[0]
        blog.pageerrors.append((t, str(exc)))
        _note(t, "PAGEERR", str(exc))
        print(f"  [{t:6.2f}s][PAGEERROR] {str(exc)[:400]}", file=sys.stderr)

    def _on_crash() -> None:
        t = time.time() - t_load_ref[0]
        blog.crashed, blog.crash_t = True, t
        _note(t, "CRASH", "!!! renderer process crashed !!!")
        print(f"  [{t:6.2f}s][!!RENDERER CRASH!!]", file=sys.stderr)

    def _on_requestfailed(req) -> None:
        t = time.time() - t_load_ref[0]
        blog.events.append((t, "REQFAIL", f"{req.method} {req.url} -> {req.failure}"))

    page.on("console", _on_console)
    page.on("pageerror", _on_pageerror)
    page.on("crash", _on_crash)
    page.on("requestfailed", _on_requestfailed)


def frames_now(page) -> int | None:
    try:
        return page.evaluate("() => window.__drProbe ? window.__drProbe.frames : -1")
    except Exception:
        return None


def wait_main_loop(page, blog: BootLog, timeout: float, poll_ms: int = 250,
                   on_poll=None) -> float | None:
    """Wait until 'Entering main loop' seen (blog.milestones['MAIN_LOOP']),
    a crash, or timeout. Returns milestone time or None."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if blog.milestones.get("MAIN_LOOP") is not None or blog.crashed:
            break
        page.wait_for_timeout(poll_ms)
        if on_poll:
            on_poll()
    return blog.milestones.get("MAIN_LOOP")


# --------------------------------------------------------------------------- #
# main boot routine (step-1 verification entry point)
# --------------------------------------------------------------------------- #
def boot_chapter(
    chapter: int = 1,
    timeout: float = 180.0,        # timebox for 'Entering main loop'
    shot_at: float = 60.0,         # primary screenshot time (s since goto)
    delta_after: float = 4.0,      # second screenshot this many s later
    log_path: str | Path = "boot1.log",
    shot_path: str | Path = "boot1.png",
    query: str = "",
    headless: bool = True,
    verbose: bool = True,
) -> dict:
    log_path = Path(log_path)
    shot_path = Path(shot_path)
    if not log_path.is_absolute():
        log_path = E2E_DIR / log_path
    if not shot_path.is_absolute():
        shot_path = E2E_DIR / shot_path
    shot_path.parent.mkdir(parents=True, exist_ok=True)
    delta_shot = shot_path.with_name(shot_path.stem + "_delta.png")

    result: dict = {"chapter": chapter, "headless": headless}

    if not ensure_server():
        result["error"] = f"cannot serve {DRWEB_DIR} on {BASE_URL}"
        print(f"[harness] FATAL: {result['error']}", file=sys.stderr)
        return result
    url = f"{BASE_URL}/index.html?c={chapter}{query}"
    result["url"] = url

    blog = BootLog()
    frame_samples: list[dict] = []

    with sync_playwright() as pw:
        browser, gl_label, gl_args, gl_probe = probe_webgl(pw, headless=headless)
        result["webgl_args_label"] = gl_label
        result["webgl_launch_args"] = gl_args
        result["webgl_probe"] = gl_probe
        print(f"[harness] WebGL OK with '{gl_label}': {gl_probe}")

        try:
            context = make_game_context(browser)
            page = context.new_page()
            t_load_ref = [time.time()]
            hook_page(page, blog, t_load_ref, verbose=verbose)

            print(f"[harness] goto {url}")
            page.goto(url, timeout=60_000, wait_until="domcontentloaded")
            t_load_ref[0] = time.time()  # re-zero at navigation commit

            last_sample = [time.time()]

            def _poll():
                if time.time() - last_sample[0] >= 5.0:
                    frame_samples.append(
                        {"t": round(time.time() - t_load_ref[0], 2), "frames": frames_now(page)})
                    last_sample[0] = time.time()

            boot_t = wait_main_loop(page, blog, timeout, on_poll=_poll)
            result["boot_ok"] = boot_t is not None
            result["time_to_main_loop_s"] = round(boot_t, 2) if boot_t is not None else None
            print(f"[harness] {'BOOTED' if boot_t is not None else 'BOOT TIMEOUT'}"
                  + (f" — 'Entering main loop' at {boot_t:.2f}s" if boot_t is not None else
                     f" after {timeout:.0f}s"))

            target = t_load_ref[0] + shot_at
            while time.time() < target and not blog.crashed:
                page.wait_for_timeout(min(5000, max(100, (target - time.time()) * 1000)))
                frame_samples.append(
                    {"t": round(time.time() - t_load_ref[0], 2), "frames": frames_now(page)})
            result["frame_samples"] = frame_samples

            fps = None
            if len(frame_samples) >= 2:
                a, b = frame_samples[-2], frame_samples[-1]
                if a["frames"] is not None and b["frames"] is not None and b["t"] > a["t"]:
                    fps = round((b["frames"] - a["frames"]) / (b["t"] - a["t"]), 1)
            result["fps_last_interval"] = fps
            result["frames_alive"] = bool(fps and fps > 1.0)

            page.screenshot(path=str(shot_path))
            page.wait_for_timeout(delta_after * 1000)
            page.screenshot(path=str(delta_shot))

            a1, a2 = analyze_png(shot_path), analyze_png(delta_shot)
            d = diff_pngs(shot_path, delta_shot)
            result["shot_primary"] = a1
            result["shot_delta"] = a2
            result["shot_diff_frac"] = d
            result["render_ok"] = bool(
                result["frames_alive"] and (a1.get("nonblank") or (d or 0) > 0.001)
            )
            result["crashed"] = blog.crashed
            result["pageerror_count"] = len(blog.pageerrors)
            result["console_event_count"] = len(blog.events)
        finally:
            try:
                context.close()
            except Exception:
                pass
            try:
                browser.close()
            except Exception:
                pass

    result["milestones_s"] = {
        k: round(v, 2) for k, v in sorted(blog.milestones.items(), key=lambda kv: kv[1])
    }
    result["success"] = bool(
        result.get("boot_ok") and result.get("render_ok") and not result.get("crashed")
    )

    header = [
        f"boot_harness.py run — {datetime.now().isoformat(timespec='seconds')}",
        f"url: {url}   (headless={headless}, service_workers=blocked)",
        f"webgl launch args [{result['webgl_args_label']}]: {' '.join(result['webgl_launch_args'])}",
        f"webgl probe: {result['webgl_probe']}",
    ]
    blog.dump(log_path, header, result)
    Path(str(log_path) + ".json").write_text(json.dumps(result, indent=2, ensure_ascii=False))
    print(f"[harness] log -> {log_path} (+ {log_path.name}.json)")

    print("\n===== BOOT RESULT =====")
    print(f"  boot_ok               : {result['boot_ok']}"
          + (f"  ({result['time_to_main_loop_s']}s to 'Entering main loop')" if result['boot_ok'] else ""))
    print(f"  fps last interval     : {result['fps_last_interval']}  (frames_alive={result['frames_alive']})")
    print(f"  shot non-blank        : {result['shot_primary'].get('nonblank')}"
          f" ({result['shot_primary'].get('unique_colors')} colors, diff={result['shot_diff_frac']})")
    print(f"  crashed / pageerrors  : {result['crashed']} / {result['pageerror_count']}")
    print(f"  SUCCESS               : {result['success']}")
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description="headless boot harness for drweb")
    ap.add_argument("--chapter", type=int, default=1)
    ap.add_argument("--timeout", type=float, default=180.0)
    ap.add_argument("--shot-at", type=float, default=60.0)
    ap.add_argument("--delta-after", type=float, default=4.0)
    ap.add_argument("--log", default="boot1.log")
    ap.add_argument("--shot", default="boot1.png")
    ap.add_argument("--query", default="", help='extra query string, e.g. "&hm=damage"')
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    res = boot_chapter(
        chapter=args.chapter, timeout=args.timeout, shot_at=args.shot_at,
        delta_after=args.delta_after, log_path=args.log, shot_path=args.shot,
        query=args.query, headless=not args.headed, verbose=not args.quiet,
    )
    return 0 if res.get("success") else 1


if __name__ == "__main__":
    sys.exit(main())
