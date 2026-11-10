#!/usr/bin/env python3
"""sw_video_test.py — the garden_video room test WITH the service worker active.

Identical to videoroom_test.py except the browser context ALLOWS sw.js to
register (the deployed site's real behavior): every asset request — including
the video element's vid/ch5_intro_en.mp4 Range requests — goes through the
worker's cache-first handler. This is the one environment difference between
the harness and a player's browser that the video path has never seen.

Usage: python3 tests/sw_video_test.py --watch 360
"""
from __future__ import annotations
import argparse
import base64
import json
import sys
import time
from pathlib import Path

E2E = Path(__file__).resolve().parent
sys.path.insert(0, str(E2E))
from playwright.sync_api import sync_playwright         # noqa: E402
from boot_harness import (ensure_server, probe_webgl, BootLog, hook_page,   # noqa: E402
                          frames_now, FRAME_PROBE_INIT)
from flower_test import SHADER_PROBE_JS, IDB_INJECT_TMPL  # noqa: E402
import saveforge  # noqa: E402

BASE_URL = "http://127.0.0.1:8901"

ROOMLIST_BYTE_OFF = 11148144      # pushi84 112 inside the {50114, 112} struct
ROOMLIST_NEW_VAL = 119            # -> room index 119 = room_dw_garden_video
SAVE_ROOM_ID = 50114


def make_sw_context(browser, hard_mode=True, extra_init_js=""):
    """Like boot_harness.make_game_context but with service workers ALLOWED."""
    context = browser.new_context(
        service_workers="allow",            # <-- the difference
        viewport={"width": 1280, "height": 720},
    )
    init = FRAME_PROBE_INIT
    if hard_mode is not None:
        init += f"\ntry {{ localStorage.setItem('dr_webport_hardmode', '{'1' if hard_mode else '0'}'); }} catch (e) {{}}"
    if extra_init_js:
        init += "\n" + extra_init_js
    context.add_init_script(init)
    return context


def run(watch_s: float, tag: str, boot_timeout: float = 240.0) -> dict:
    print(f"[sw_video] watch={watch_s}s (service workers ALLOWED)")
    payload = saveforge.craft(5, SAVE_ROOM_ID, hp=90, maxhp=90,
                              base=saveforge.SAVES / "filech4_0")
    files = [("filech5_0", base64.b64encode(payload).decode())]
    inject = IDB_INJECT_TMPL.replace("%FILES%", json.dumps(files))

    if not ensure_server():
        return {"verdict": "FAIL", "reason": "cannot serve repo"}

    blog = BootLog()
    result = {"tag": tag}
    shot_dir = E2E / "shots"
    shot_dir.mkdir(exist_ok=True)

    with sync_playwright() as pw:
        browser, gl_label, gl_args, gl_probe = probe_webgl(pw, headless=True)
        ctx = make_sw_context(browser, True, SHADER_PROBE_JS + "\n" + inject)
        page = ctx.new_page()

        sw_msgs = []
        page.on("console", lambda m: sw_msgs.append(m.text) if "sw]" in m.text or "Service Worker" in m.text else None)

        def _patch_part1(route):
            resp = route.fetch()
            body = bytearray(resp.body())
            if len(body) > ROOMLIST_BYTE_OFF:
                print(f"[sw_video] interceptor: roomlist byte {body[ROOMLIST_BYTE_OFF]} -> {ROOMLIST_NEW_VAL}")
                body[ROOMLIST_BYTE_OFF] = ROOMLIST_NEW_VAL
            route.fulfill(status=resp.status, headers=dict(resp.headers),
                          body=bytes(body))
        # NOTE: with the SW active, the SW's own fetches bypass page routes;
        # the first page load has no cache, so the page fetches part001
        # directly (through this route) and the SW caches the patched copy.
        page.route("**/chapter5.unx.part001*", _patch_part1)

        tref = [time.time()]
        hook_page(page, blog, tref, verbose=False,
                  extra_milestones=[("save_injected", "[idb-inject] wrote"),
                                    ("file_select", "mus/menu.ogg"),
                                    ("gml_error", "ERROR in"),
                                    ("video_player", "Video playing event called"),
                                    ("video_muted", "video_open failed"),
                                    ("no_video_ctx", "Not rendering video")])
        url = f"{BASE_URL}/index.html?c=5&hm=full"
        print(f"[sw_video] goto {url}")
        page.goto(url, timeout=90_000, wait_until="domcontentloaded")
        tref[0] = time.time()

        deadline = time.time() + boot_timeout
        while time.time() < deadline:
            if blog.milestones.get("MAIN_LOOP") is not None or blog.crashed:
                break
            page.wait_for_timeout(500)
        t_sel = time.time() + 200
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
            for i in range(6):
                if blog.crashed:
                    break
                page.screenshot(path=str(shot_dir / f"swv_{tag}_step{i}.png"))
                page.keyboard.press("z")
                page.wait_for_timeout(1600)
            page.wait_for_timeout(4000)
        page.screenshot(path=str(shot_dir / f"swv_{tag}_loaded.png"))

        t_watch0 = time.time()
        frame_samples = []
        last = [time.time()]
        while time.time() - t_watch0 < watch_s and not blog.crashed:
            page.keyboard.press("z")
            page.wait_for_timeout(1200)
            frame_samples.append({"t": round(time.time() - tref[0], 1),
                                  "frames": frames_now(page)})
            if time.time() - last[0] >= 8:
                p = shot_dir / f"swv_{tag}_w{int(time.time()-t_watch0):03d}s.png"
                page.screenshot(path=str(p))
                last[0] = time.time()

        fps = None
        if len(frame_samples) >= 2:
            a, b = frame_samples[-2], frame_samples[-1]
            if a["frames"] is not None and b["frames"] is not None and b["t"] > a["t"]:
                fps = round((b["frames"] - a["frames"]) / (b["t"] - a["t"]), 1)

        def bucket(sub):
            return [t for (_, _, t) in blog.events if sub in t]
        result.update({
            "fps_last": fps, "crashed": blog.crashed, "crash_t": blog.crash_t,
            "pageerrors": blog.pageerrors[:20],
            "shader_compile_fails": bucket("[shaderprobe]"),
            "video_events": [t for (_, _, t) in blog.events if "video" in t.lower()][:30],
            "gml_errors": [t for (_, k, t) in blog.events if k == "error" or "ERROR" in t][:40],
            "reqfails": [t for (_, k, t) in blog.events if k == "REQFAIL"][:20],
            "sw_msgs": sw_msgs[:10],
            "milestones_s": {k: round(v, 1) for k, v in sorted(blog.milestones.items(), key=lambda kv: kv[1])},
        })
        verdict = ("CRASH" if blog.crashed else
                   "SHADER_FAIL" if result["shader_compile_fails"] else
                   "PAGEERROR" if blog.pageerrors else
                   "FROZEN" if (fps is not None and fps < 1.0) else
                   "ALIVE" if (fps and fps >= 1.0) else "UNKNOWN")
        result["verdict"] = verdict

        logp = E2E / "logs" / f"sw_video_{tag}.log"
        logp.parent.mkdir(exist_ok=True)
        blog.dump(logp, [f"sw_video_test tag={tag} watch={watch_s} SW=allowed"], result)
        print(f"\n[sw_video] VERDICT: {verdict}  fps={fps}  crashed={blog.crashed}")
        print(f"[sw_video] milestones: {result['milestones_s']}")
        for s in result["video_events"][:10]:
            print("   [video]", s[:180])
        for s in result["gml_errors"][:10]:
            print("   [err]", s[:220])
        for s in result["reqfails"][:8]:
            print("   [reqfail]", s[:160])
        for s in result["pageerrors"][:5]:
            print("   [pageerror]", str(s)[:250])
        try:
            ctx.close(); browser.close()
        except Exception:
            pass
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--watch", type=float, default=360.0)
    ap.add_argument("--tag", default="run")
    a = ap.parse_args()
    res = run(a.watch, a.tag)
    return 0 if res.get("verdict") in ("ALIVE",) else 1


if __name__ == "__main__":
    sys.exit(main())
