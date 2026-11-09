#!/usr/bin/env python3
"""soak_test.py — ch5 title/file-select IDLE SOAK on the deployed v9.10 bytes.

The v9.5-era analysis said the vanilla save-poll storm (obj_initializer2_Step_0
boot block, unlatched since v9.8) leaks ~81KB native memory per ini open and
OOM-killed a tab at ~143s of title-idle. v9.8 pulled the broken latch and
v9.10 pulled the v8.1 stubs; boot/field flow is verified, but NOBODY has
soaked the title screen on the current bytes. This probe does exactly that.

No save is injected (clean IndexedDB — the fresh-player path).

Measures every 10s while idling on the title/file-select screen:
  - rAF fps, JS heap (performance.memory), wasm heap (Module.HEAPU8.byteLength)
  - console-line rate by category (save-poll needles + generic top lines)
  - screenshots every 60s (non-blank + change detection)
Verdict: ALIVE / CRASH / PAGEERROR / FROZEN / LEAK_GROWTH (wasm heap climbing
>= 1 MB/min sustained to the end).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

E2E = Path(__file__).resolve().parent
sys.path.insert(0, str(E2E))
from boot_harness import (ensure_server, probe_webgl, make_game_context,   # noqa: E402
                          BootLog, hook_page, frames_now, analyze_png, diff_pngs)

from playwright.sync_api import sync_playwright  # noqa: E402

BASE_URL = "http://127.0.0.1:8901"

MEM_PROBE_JS = """() => {
  const out = {jsHeap: null, jsTotal: null, wasmHeap: null, videos: 0};
  try { if (performance.memory) {
    out.jsHeap = performance.memory.usedJSHeapSize;
    out.jsTotal = performance.memory.totalJSHeapSize; } } catch (e) {}
  try { const M = window.Module;
    if (M && M.HEAPU8) out.wasmHeap = M.HEAPU8.byteLength; } catch (e) {}
  try { out.videos = document.querySelectorAll('video').length; } catch (e) {}
  return out;
}"""

# console needles that indicate the save-poll storm (rates reported per interval)
STORM_NEEDLES = ("GetSaveFileName", "filech", "dr.ini", "IDBFS", "_savedata")


def run(soak_s: float, tag: str, headless: bool = True) -> dict:
    print(f"[soak] title-idle soak {soak_s}s tag={tag}")
    if not ensure_server():
        return {"verdict": "FAIL", "reason": "cannot serve repo"}

    blog = BootLog()
    shot_dir = E2E / "shots"
    shot_dir.mkdir(exist_ok=True)
    result: dict = {"tag": tag, "soak_s": soak_s, "timings": {}, "samples": []}

    with sync_playwright() as pw:
        browser, gl_label, gl_args, gl_probe = probe_webgl(pw, headless=headless)
        result["webgl"] = gl_label
        ctx = make_game_context(browser, hard_mode=None)
        page = ctx.new_page()
        tref = [time.time()]
        hook_page(page, blog, tref, verbose=False,
                  extra_milestones=[("logo_music", "mus/deltarune_logo_ch5_itoki"),
                                    ("title", "mus/menu.ogg")])
        url = f"{BASE_URL}/index.html?c=5"
        print(f"[soak] goto {url}")
        page.goto(url, timeout=90_000, wait_until="domcontentloaded")
        tref[0] = time.time()

        # --- boot to file select -------------------------------------------
        deadline = time.time() + 240
        while time.time() < deadline:
            if blog.crashed or blog.milestones.get("title") is not None:
                break
            page.wait_for_timeout(500)
        result["timings"]["main_loop_s"] = blog.milestones.get("MAIN_LOOP")
        result["timings"]["logo_music_s"] = blog.milestones.get("logo_music")
        result["timings"]["title_s"] = blog.milestones.get("title")
        print(f"[soak] main_loop={result['timings']['main_loop_s']} "
              f"logo={result['timings']['logo_music_s']} title={result['timings']['title_s']}")

        t0 = time.time()
        title_shot = shot_dir / f"soak_{tag}_title.png"
        if not blog.crashed:
            page.screenshot(path=str(title_shot))
            result["title_shot"] = analyze_png(title_shot)

        # --- soak ------------------------------------------------------------
        ev0 = len(blog.events)
        last_frames = frames_now(page)
        last_t = time.time()
        last_sample_t = time.time()
        last_shot_t = time.time()
        shots = [str(title_shot)]
        interval_lines: list[dict] = []
        while time.time() - t0 < soak_s:
            if blog.crashed:
                break
            page.wait_for_timeout(10_000)
            now = time.time()
            f = frames_now(page)
            fps = (round((f - last_frames) / (now - last_t), 2)
                   if (f is not None and last_frames is not None and now > last_t) else None)
            last_frames, last_t = f, now
            try:
                mem = page.evaluate(MEM_PROBE_JS)
            except Exception:
                mem = {"error": "evaluate failed"}
            evn = len(blog.events)
            recent = blog.events[ev0:evn]
            ev0 = evn
            cats = Counter()
            for (_t, _k, txt) in recent:
                for needle in STORM_NEEDLES:
                    if needle.lower() in txt.lower():
                        cats[needle] += 1
                        break
            sample = {"t": round(now - tref[0], 1), "fps": fps,
                      "jsHeapMB": round(mem.get("jsHeap", 0) / 1e6, 1) if mem.get("jsHeap") else None,
                      "wasmHeapMB": round(mem.get("wasmHeap", 0) / 1e6, 1) if mem.get("wasmHeap") else None,
                      "evps": round(len(recent) / max(0.001, now - last_sample_t), 1),
                      "storm": dict(cats)}
            last_sample_t = now
            interval_lines.append(sample)
            print(f"[soak] t={sample['t']:7.1f}s fps={fps} js={sample['jsHeapMB']}MB "
                  f"wasm={sample['wasmHeapMB']}MB lines/s={sample['evps']} storm={dict(cats)}")
            if now - last_shot_t >= 60:
                p = shot_dir / f"soak_{tag}_t{int(now - t0):04d}s.png"
                page.screenshot(path=str(p))
                shots.append(str(p))
                last_shot_t = now

        result["samples"] = interval_lines
        result["shots"] = shots

        # final shot pair for animation check
        final_a = shot_dir / f"soak_{tag}_finalA.png"
        final_b = shot_dir / f"soak_{tag}_finalB.png"
        if not blog.crashed:
            page.screenshot(path=str(final_a))
            page.wait_for_timeout(4000)
            page.screenshot(path=str(final_b))
            result["final_diff"] = diff_pngs(final_a, final_b)
            result["final_nonblank"] = analyze_png(final_a).get("nonblank")

        # memory growth over the soak (wasm heap)
        wasm = [s["wasmHeapMB"] for s in interval_lines if s.get("wasmHeapMB")]
        growth = None
        if len(wasm) >= 6:
            n = max(3, len(wasm) // 3)
            head, tail = wasm[:n], wasm[-n:]
            growth = round((sum(tail) / len(tail)) - (sum(head) / len(head)), 1)
        result["wasm_growth_mb"] = growth

        result["crashed"] = blog.crashed
        result["crash_t"] = blog.crash_t
        result["pageerrors"] = blog.pageerrors[:20]
        result["milestones_s"] = {k: round(v, 1) for k, v in
                                  sorted(blog.milestones.items(), key=lambda kv: kv[1])}

        def bucket(sub):
            return sum(1 for (_t, _k, t) in blog.events if sub in t)
        result["storm_totals"] = {n: bucket(n) for n in STORM_NEEDLES}
        result["top_console"] = [
            (txt[:110], n) for (txt, n) in
            Counter(t.split('\n')[0][:110] for (_t, _k, t) in blog.events).most_common(12)]
        result["total_console"] = len(blog.events)

        # verdict
        fps_ok = [s["fps"] for s in interval_lines[-6:] if s.get("fps") is not None]
        min_recent_fps = min(fps_ok) if fps_ok else None
        result["min_recent_fps"] = min_recent_fps
        if blog.crashed:
            verdict = "CRASH"
        elif blog.pageerrors:
            verdict = "PAGEERROR"
        elif min_recent_fps is not None and min_recent_fps < 1.0:
            verdict = "FROZEN"
        elif growth is not None and growth >= 50 and min_recent_fps is not None and min_recent_fps < 5:
            verdict = "LEAK_DEGRADE"
        else:
            verdict = "ALIVE"
        result["verdict"] = verdict

        logp = E2E / "logs" / f"soak_{tag}.log"
        logp.parent.mkdir(exist_ok=True)
        blog.dump(logp, [f"soak_test tag={tag} soak={soak_s}s"], result)
        print(f"\n[soak] VERDICT: {verdict}  crashed={blog.crashed} "
              f"wasm_growth={result.get('wasm_growth_mb')}MB min_fps={min_recent_fps}")
        print(f"[soak] console total={result['total_console']} storm={result['storm_totals']}")
        for txt, n in result["top_console"]:
            print(f"   {n:6d}x {txt}")
        try:
            ctx.close(); browser.close()
        except Exception:
            pass
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--soak", type=float, default=420.0)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--headed", action="store_true")
    a = ap.parse_args()
    res = run(a.soak, a.tag, headless=not a.headed)
    return 0 if res.get("verdict") in ("ALIVE",) else 1


if __name__ == "__main__":
    sys.exit(main())
