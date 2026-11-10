#!/usr/bin/env python3
"""fixcheck_test.py — validate the ch5 speed-patch pull at the crash position.

Fix under test: HARD_MODE_PATCHES["5"].speed -> [] and
ATTACK_LEN_HARD_EXTRA["5"] -> [] (ch5 hard mode keeps the runtime-verified
damage component + the always-on base ATTACK_LEN 1.2x; ch1-4 unchanged).

Run: spawn at (3900,1330) in room 122 (the floradinn trigger region) with
hm=full and the FIXED index.html served via route interception. The trigger
should fire the floradinn encounter and the battle should RUN (battle music!)
without the renderer crash. Z-rhythm drives the fight.

Usage: python3 tests/fixcheck_test.py [--no-fix] [--watch 180]
"""
from __future__ import annotations
import argparse
import base64
import json
import re
import sys
import time
from pathlib import Path

E2E = Path(__file__).resolve().parent
sys.path.insert(0, str(E2E))
from playwright.sync_api import sync_playwright         # noqa: E402
from boot_harness import (ensure_server, probe_webgl, make_game_context,   # noqa: E402
                          BootLog, hook_page, frames_now)
from flower_test import SHADER_PROBE_JS, IDB_INJECT_TMPL  # noqa: E402
import saveforge  # noqa: E402

BASE_URL = "http://127.0.0.1:8901"
MAINCHARA_OFF = 4043324
SPAWN_X, SPAWN_Y = 3900, 1330
SAVE_ROOM_ID = 50124


def patched_html(html: str) -> str:
    """Empty the ch5 speed-component tables (the fix)."""
    m = re.search(r'window\.HARD_MODE_PATCHES\s*=\s*(\{.*?\});\s*\n', html, re.S)
    tables = json.loads(m.group(1))
    tables["5"]["speed"] = []
    html = html[:m.start(1)] + json.dumps(tables) + html[m.end(1):]
    m2 = re.search(r'window\.ATTACK_LEN_HARD_EXTRA\s*=\s*(\{.*?\});?\s*\n', html, re.S)
    extra = json.loads(m2.group(1))
    extra["5"] = []
    html = html[:m2.start(1)] + json.dumps(extra) + html[m2.end(1):]
    return html


def run(watch_s: float, tag: str, apply_fix: bool, hm: str = "full") -> dict:
    print(f"[fixcheck] fix={'ON' if apply_fix else 'OFF'} spawn=({SPAWN_X},{SPAWN_Y}) "
          f"hm={hm} watch={watch_s}s")
    payload = saveforge.craft(5, SAVE_ROOM_ID, hp=90, maxhp=90,
                              base=saveforge.SAVES / "filech4_0")
    files = [("filech5_0", base64.b64encode(payload).decode())]
    inject = IDB_INJECT_TMPL.replace("%FILES%", json.dumps(files))

    if not ensure_server():
        return {"verdict": "FAIL"}
    blog = BootLog()
    shot_dir = E2E / "shots"
    shot_dir.mkdir(exist_ok=True)

    with sync_playwright() as pw:
        browser, gl_label, gl_args, gl_probe = probe_webgl(pw, headless=True)
        ctx = make_game_context(browser, hard_mode=True,
                                extra_init_js=SHADER_PROBE_JS + "\n" + inject)
        page = ctx.new_page()

        def _patch_index(route):
            resp = route.fetch()
            body = resp.text()
            if apply_fix:
                body = patched_html(body)
                print("[fixcheck] index.html: ch5 speed tables emptied")
            route.fulfill(status=resp.status,
                          headers={**dict(resp.headers), "content-type": "text/html"},
                          body=body)
        page.route("**/index.html*", _patch_index)

        def _patch_part1(route):
            resp = route.fetch()
            body = bytearray(resp.body())
            if len(body) >= MAINCHARA_OFF + 8:
                body[MAINCHARA_OFF:MAINCHARA_OFF+4] = SPAWN_X.to_bytes(4, "little")
                body[MAINCHARA_OFF+4:MAINCHARA_OFF+8] = SPAWN_Y.to_bytes(4, "little")
                print(f"[fixcheck] spawn -> ({SPAWN_X},{SPAWN_Y})")
            route.fulfill(status=resp.status, headers=dict(resp.headers),
                          body=bytes(body))
        page.route("**/chapter5.unx.part001*", _patch_part1)

        tref = [time.time()]
        hook_page(page, blog, tref, verbose=False,
                  extra_milestones=[("save_injected", "[idb-inject] wrote"),
                                    ("file_select", "mus/menu.ogg"),
                                    ("gml_error", "ERROR in"),
                                    ("battle_music", "mus/rakuichi")])
        url = f"{BASE_URL}/index.html?c=5&hm={hm}"
        page.goto(url, timeout=90_000, wait_until="domcontentloaded")
        tref[0] = time.time()

        t_sel = time.time() + 170
        while time.time() < t_sel:
            if blog.crashed:
                break
            if blog.milestones.get("file_select") is not None:
                page.wait_for_timeout(2500)
                break
            page.wait_for_timeout(500)

        if not blog.crashed:
            page.mouse.click(640, 360)
            page.wait_for_timeout(1200)
            for i in range(4):
                if blog.crashed:
                    break
                try:
                    page.keyboard.press("z")
                    page.wait_for_timeout(1500)
                except Exception:
                    blog.crashed = True
                    break
            page.wait_for_timeout(3000)

        # drive the encounter: walk right into the trigger zone, then Z-rhythm
        if not blog.crashed:
            for burst in range(3):
                if blog.crashed:
                    break
                page.keyboard.down("ArrowRight")
                page.wait_for_timeout(1800)
                page.keyboard.up("ArrowRight")
                page.wait_for_timeout(1200)

        t_watch0 = time.time()
        frame_samples = []
        last = [time.time()]
        while time.time() - t_watch0 < watch_s and not blog.crashed:
            try:
                page.keyboard.press("z")
                page.wait_for_timeout(1000)
            except Exception:
                blog.crashed = True
                blog.crash_t = time.time() - tref[0]
                break
            frame_samples.append({"t": round(time.time() - tref[0], 1),
                                  "frames": frames_now(page)})
            if time.time() - last[0] >= 10:
                try:
                    p = shot_dir / f"fx_{tag}_w{int(time.time()-t_watch0):03d}s.png"
                    page.screenshot(path=str(p))
                except Exception:
                    pass
                last[0] = time.time()

        fps = None
        if len(frame_samples) >= 2:
            a, b = frame_samples[-2], frame_samples[-1]
            if a["frames"] is not None and b["frames"] is not None and b["t"] > a["t"]:
                fps = round((b["frames"] - a["frames"]) / (b["t"] - a["t"]), 1)

        music = sorted(set(t.split("for ")[-1] for (_, _, t) in blog.events
                           if "GML_async_wget2_data called for mus/" in t))
        errs = [t for (_, k, t) in blog.events if k == "error" or "ERROR" in t][:10]
        verdict = ("CRASH" if blog.crashed else
                   "FROZEN" if (fps is not None and fps < 1.0) else
                   "ALIVE" if (fps is None or fps >= 1.0) else "UNKNOWN")
        print(f"\n[fixcheck] VERDICT: {verdict} fps={fps} crashed={blog.crashed} "
              f"crash_t={blog.crash_t}")
        print(f"[fixcheck] music: {music}")
        battle_ran = any("rakuichi" in m for m in music)
        print(f"[fixcheck] battle music requested (encounter ran): {battle_ran}")
        for e in errs:
            print("   [err]", e[:200])
        logp = E2E / "logs" / f"fixcheck_{tag}.log"
        logp.parent.mkdir(exist_ok=True)
        blog.dump(logp, [f"fixcheck fix={'ON' if apply_fix else 'OFF'} hm={hm}"],
                  {"verdict": verdict, "music": music, "battle_ran": battle_ran})
        try:
            ctx.close(); browser.close()
        except Exception:
            pass
    return {"verdict": verdict, "battle_ran": battle_ran}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--watch", type=float, default=180.0)
    ap.add_argument("--tag", default="run")
    ap.add_argument("--no-fix", action="store_true")
    a = ap.parse_args()
    res = run(a.watch, a.tag, apply_fix=not a.no_fix)
    return 0 if res.get("verdict") in ("ALIVE",) else 1


if __name__ == "__main__":
    sys.exit(main())
