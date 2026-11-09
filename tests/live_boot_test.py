#!/usr/bin/env python3
"""live_boot_test.py — LIVE-site ch5 boot probe (https://ddown325.github.io).

Final end-to-end check of the deployed build: boots index.html?c=5 from the
REAL site with the REAL service worker allowed (service_workers default) —
the exact path a user's browser takes, including the v9.10 CACHE_VERSION
purge + re-download of the immutable-cached chapter data.

Watches: SW registration/purge lines, boot milestones (interception ->
chapter 100% -> game loaded -> main loop -> logo music -> title music),
rAF fps, wasm heap, pageerrors, renderer crash. Idles on the title screen
for --idle seconds at the end.

Verdict ALIVE = main loop + title reached, fps >= 5, no pageerror/crash.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

E2E = Path(__file__).resolve().parent
sys.path.insert(0, str(E2E))
from boot_harness import (probe_webgl, BootLog, hook_page, frames_now,   # noqa: E402
                          analyze_png)

from playwright.sync_api import sync_playwright  # noqa: E402

LIVE = "https://ddown325.github.io/deltarune-web"

MEM_PROBE_JS = """() => {
  const out = {jsHeap: null, wasmHeap: null, sw: null, caches: null};
  try { out.jsHeap = performance.memory.usedJSHeapSize; } catch (e) {}
  try { const M = window.Module; if (M && M.HEAPU8) out.wasmHeap = M.HEAPU8.byteLength; } catch (e) {}
  try { out.sw = (navigator.serviceWorker && navigator.serviceWorker.controller) ? 'controlled' : 'uncontrolled'; } catch (e) {}
  try { if (window.caches) caches.keys().then(k => {}); } catch (e) {}
  return out;
}"""


def run(idle_s: float, tag: str, headless: bool = True) -> dict:
    print(f"[live] booting {LIVE}/index.html?c=5 (service workers ALLOWED) idle={idle_s}s")
    blog = BootLog()
    shot_dir = E2E / "shots"
    shot_dir.mkdir(exist_ok=True)
    result: dict = {"tag": tag, "url": f"{LIVE}/index.html?c=5", "timings": {}, "samples": []}

    with sync_playwright() as pw:
        browser, gl_label, gl_args, gl_probe = probe_webgl(pw, headless=headless)
        result["webgl"] = gl_label
        # NOTE: no service_workers='block' — we WANT the real sw.js path
        ctx = browser.new_context(viewport={"width": 1280, "height": 720})
        from boot_harness import FRAME_PROBE_INIT
        ctx.add_init_script(FRAME_PROBE_INIT)
        page = ctx.new_page()
        tref = [time.time()]
        hook_page(page, blog, tref, verbose=True,
                  extra_milestones=[("sw_reg", "Service Worker"),
                                    ("sw_purge", "purging"),
                                    ("cache_ver", "CACHE_VERSION"),
                                    ("logo_music", "mus/deltarune_logo_ch5_itoki"),
                                    ("title", "mus/menu.ogg")])

        print(f"[live] goto")
        page.goto(result["url"], timeout=120_000, wait_until="domcontentloaded")
        tref[0] = time.time()

        # --- boot to title ---------------------------------------------------
        deadline = time.time() + 300
        while time.time() < deadline:
            if blog.crashed or blog.milestones.get("title") is not None:
                break
            page.wait_for_timeout(1000)
        for k in ("MAIN_LOOP", "logo_music", "title"):
            result["timings"][k] = blog.milestones.get(k)
        print(f"[live] main_loop={result['timings']['MAIN_LOOP']} "
              f"logo={result['timings']['logo_music']} title={result['timings']['title']}")

        page.screenshot(path=str(shot_dir / f"live_{tag}_title.png"))

        # --- idle on title -----------------------------------------------------
        t0 = time.time()
        last_frames, last_t = frames_now(page), time.time()
        while time.time() - t0 < idle_s:
            if blog.crashed:
                break
            page.wait_for_timeout(10_000)
            now = time.time()
            f = frames_now(page)
            fps = (round((f - last_frames) / (now - last_t), 2)
                   if (f is not None and last_frames is not None) else None)
            last_frames, last_t = f, now
            try:
                mem = page.evaluate(MEM_PROBE_JS)
            except Exception:
                mem = {}
            result["samples"].append(
                {"t": round(now - tref[0], 1), "fps": fps,
                 "wasmMB": round(mem.get("wasmHeap", 0) / 1e6, 1) if mem.get("wasmHeap") else None,
                 "sw": mem.get("sw")})
            print(f"[live] t={now - tref[0]:7.1f}s fps={fps} wasm={result['samples'][-1]['wasmMB']}MB sw={mem.get('sw')}")

        final = shot_dir / f"live_{tag}_final.png"
        if not blog.crashed:
            page.screenshot(path=str(final))
        result["final_nonblank"] = analyze_png(final).get("nonblank") if final.exists() else None
        result["crashed"] = blog.crashed
        result["crash_t"] = blog.crash_t
        result["pageerrors"] = blog.pageerrors[:20]
        result["milestones_s"] = {k: round(v, 1) for k, v in
                                  sorted(blog.milestones.items(), key=lambda kv: kv[1])}

        def bucket(sub):
            return [t[:160] for (_t, _k, t) in blog.events if sub.lower() in t.lower()][:10]
        result["sw_lines"] = bucket("service worker") + bucket("cache")[:5]
        result["gml_errors"] = [t for (_t, _k, t) in blog.events if "ERROR" in t][:15]

        fps_all = [s["fps"] for s in result["samples"] if s.get("fps") is not None]
        result["fps_min"] = min(fps_all) if fps_all else None

        if blog.crashed:
            verdict = "CRASH"
        elif blog.pageerrors:
            verdict = "PAGEERROR"
        elif result["timings"].get("title") is None:
            verdict = "NO_TITLE"
        elif result["fps_min"] is not None and result["fps_min"] < 5:
            verdict = "SLOW"
        else:
            verdict = "ALIVE"
        result["verdict"] = verdict

        logp = E2E / "logs" / f"live_{tag}.log"
        logp.parent.mkdir(exist_ok=True)
        blog.dump(logp, [f"live_boot_test tag={tag} url={result['url']}"], result)
        print(f"\n[live] VERDICT: {verdict} crashed={blog.crashed} title_at={result['timings'].get('title')}s")
        print(f"[live] sw lines: {result['sw_lines'][:5]}")
        try:
            ctx.close(); browser.close()
        except Exception:
            pass
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--idle", type=float, default=120.0)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--headed", action="store_true")
    a = ap.parse_args()
    res = run(a.idle, a.tag, headless=not a.headed)
    return 0 if res.get("verdict") == "ALIVE" else 1


if __name__ == "__main__":
    sys.exit(main())
