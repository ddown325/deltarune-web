#!/usr/bin/env python3
"""videoroom_test.py — reach room_dw_garden_video (ch5) in the headless harness
and watch the GML video path (video_open/video_draw) that no test has ever run.

Path: forged save (room 50122 = garden_intro, hard mode ON) + a pure-DATA patch
that moves garden_intro's obj_mainchara spawn onto the room's right-edge door
(instance record x/y only — no bytecode, no chains). Walking right then flows:
  garden_intro --door--> meetflowery --trigger@760--> obj_ch5_DW01 cutscene
  --> room_goto(119) garden_video --> video_open('vid/ch5_intro_en.mp4')
  --> video_draw()/surface path  (the crash suspect)

Usage: python3 tests/videoroom_test.py --watch 420
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
from boot_harness import (ensure_server, probe_webgl, make_game_context,   # noqa: E402
                          BootLog, hook_page, frames_now, analyze_png)
from flower_test import SHADER_PROBE_JS, IDB_INJECT_TMPL, ROOM_NAMES  # noqa: E402
import saveforge  # noqa: E402

BASE_URL = "http://127.0.0.1:8901"

# --- the data patch: make save id 50114 load room 119 (garden_video) ---------
# scr_get_room_list's base (always-built) array has {id: 50114, room: 112}.
# We patch the pushi84 immediate 112 -> 119 so the forged save (room=50114)
# loads room_dw_garden_video directly — the only room whose controller calls
# video_open()/video_draw() (a code path never exercised on web before).
# Pure immediate change: no branch targets, no chains, same instruction size.
ROOMLIST_BYTE_OFF = 11148144      # pushi84 112 inside the {50114, 112} struct
ROOMLIST_NEW_VAL = 119            # -> room index 119 = room_dw_garden_video
SAVE_ROOM_ID = 50114


def run(watch_s: float, tag: str, boot_timeout: float = 210.0,
        headless: bool = True, hard: bool = True, hm: str = "full") -> dict:
    print(f"[videoroom] watch={watch_s}s hard={hard} hm={hm}")
    payload = saveforge.craft(5, SAVE_ROOM_ID, hp=90, maxhp=90,
                              base=saveforge.SAVES / "filech4_0")
    files = [("filech5_0", base64.b64encode(payload).decode())]
    inject = IDB_INJECT_TMPL.replace("%FILES%", json.dumps(files))

    if not ensure_server():
        return {"verdict": "FAIL", "reason": "cannot serve repo"}

    blog = BootLog()
    result = {"tag": tag, "timings": {}}
    shot_dir = E2E / "shots"
    shot_dir.mkdir(exist_ok=True)

    with sync_playwright() as pw:
        browser, gl_label, gl_args, gl_probe = probe_webgl(pw, headless=headless)
        ctx = make_game_context(browser, hard_mode=(True if hard else None),
                                extra_init_js=SHADER_PROBE_JS + "\n" + inject)
        page = ctx.new_page()

        # ---- route interception: patch the save-room list in part001 --------
        def _patch_part1(route):
            resp = route.fetch()
            body = bytearray(resp.body())
            if len(body) > ROOMLIST_BYTE_OFF:
                orig = body[ROOMLIST_BYTE_OFF]
                print(f"[videoroom] interceptor: roomlist byte @{ROOMLIST_BYTE_OFF} "
                      f"{orig} -> {ROOMLIST_NEW_VAL} (50114 now loads garden_video)")
                body[ROOMLIST_BYTE_OFF] = ROOMLIST_NEW_VAL
            route.fulfill(status=resp.status, headers=dict(resp.headers),
                          body=bytes(body))
        page.route("**/chapter5.unx.part001*", _patch_part1)

        tref = [time.time()]
        hook_page(page, blog, tref, verbose=False,
                  extra_milestones=[("save_injected", "[idb-inject] wrote"),
                                    ("file_select", "mus/menu.ogg"),
                                    ("room_music", "GML_async_wget2_data called for mus/"),
                                    ("gml_error", "ERROR in"),
                                    ("video_player", "Video playing event called"),
                                    ("video_muted", "video_open failed"),
                                    ("no_video_ctx", "Not rendering video")])
        url = f"{BASE_URL}/index.html?c=5&hm={hm}"
        print(f"[videoroom] goto {url}")
        page.goto(url, timeout=90_000, wait_until="domcontentloaded")
        tref[0] = time.time()

        # boot to file select
        deadline = time.time() + boot_timeout
        while time.time() < deadline:
            if blog.milestones.get("MAIN_LOOP") is not None or blog.crashed:
                break
            page.wait_for_timeout(500)
        t_sel = time.time() + 160
        while time.time() < t_sel:
            if blog.crashed:
                break
            if blog.milestones.get("file_select") is not None:
                page.wait_for_timeout(2500)
                break
            page.wait_for_timeout(500)
        result["timings"]["file_select_s"] = blog.milestones.get("file_select")

        # load the save
        if not blog.crashed:
            page.mouse.click(640, 360)
            page.wait_for_timeout(1200)
            for i in range(6):
                if blog.crashed:
                    break
                page.screenshot(path=str(shot_dir / f"vr_{tag}_step{i}.png"))
                page.keyboard.press("z")
                page.wait_for_timeout(1600)
            page.wait_for_timeout(4000)
        page.screenshot(path=str(shot_dir / f"vr_{tag}_loaded.png"))

        # ---- the walk: right into the door, then right to the trigger ------
        t_watch0 = time.time()
        frame_samples = []
        last = [time.time()]
        phase = [0]
        if not blog.crashed:
            # phase 0: walk right (garden_intro door is right there)
            page.keyboard.down("ArrowRight")
            page.wait_for_timeout(4000)
            page.keyboard.up("ArrowRight")
            # phase 1: keep walking right toward the trigger in meetflowery
            page.wait_for_timeout(1500)
            page.keyboard.down("ArrowRight")
            page.wait_for_timeout(9000)
            page.keyboard.up("ArrowRight")
        # phase 2: cutscene support — Z to advance dialogue, small nudges
        while time.time() - t_watch0 < watch_s and not blog.crashed:
            page.keyboard.press("z")
            page.wait_for_timeout(1400)
            if (time.time() - t_watch0) % 16 < 2:
                page.keyboard.down("ArrowRight")
                page.wait_for_timeout(900)
                page.keyboard.up("ArrowRight")
            frame_samples.append({"t": round(time.time() - tref[0], 1),
                                  "frames": frames_now(page)})
            if time.time() - last[0] >= 8:
                p = shot_dir / f"vr_{tag}_w{int(time.time()-t_watch0):03d}s.png"
                page.screenshot(path=str(p))
                last[0] = time.time()

        result["frame_samples"] = frame_samples[-10:]
        fps = None
        if len(frame_samples) >= 2:
            a, b = frame_samples[-2], frame_samples[-1]
            if a["frames"] is not None and b["frames"] is not None and b["t"] > a["t"]:
                fps = round((b["frames"] - a["frames"]) / (b["t"] - a["t"]), 1)
        result["fps_last"] = fps
        result["crashed"] = blog.crashed
        result["crash_t"] = blog.crash_t
        result["pageerrors"] = blog.pageerrors[:20]

        def bucket(sub):
            return [t for (_, _, t) in blog.events if sub in t]
        result["shader_compile_fails"] = bucket("[shaderprobe]")
        result["video_events"] = [t for (_, _, t) in blog.events
                                  if "video" in t.lower()][:30]
        result["gml_errors"] = [t for (_, k, t) in blog.events
                                if k == "error" or "ERROR" in t][:40]
        result["reqfails"] = [t for (_, k, t) in blog.events if k == "REQFAIL"][:20]
        result["music_requested"] = sorted(set(
            t.split("for ")[-1] for (_, _, t) in blog.events
            if "GML_async_wget2_data called for mus/" in t))[:12]
        result["milestones_s"] = {k: round(v, 1) for k, v in
                                  sorted(blog.milestones.items(), key=lambda kv: kv[1])}

        if blog.crashed:
            verdict = "CRASH"
        elif result["shader_compile_fails"]:
            verdict = "SHADER_FAIL"
        elif blog.pageerrors:
            verdict = "PAGEERROR"
        elif fps is not None and fps < 1.0:
            verdict = "FROZEN"
        elif fps and fps >= 1.0:
            verdict = "ALIVE"
        else:
            verdict = "UNKNOWN"
        result["verdict"] = verdict

        logp = E2E / "logs" / f"videoroom_{tag}.log"
        logp.parent.mkdir(exist_ok=True)
        blog.dump(logp, [f"videoroom_test tag={tag} watch={watch_s}"], result)
        print(f"\n[videoroom] VERDICT: {verdict}  fps={fps}  crashed={blog.crashed}")
        print(f"[videoroom] milestones: {result['milestones_s']}")
        print(f"[videoroom] music: {result['music_requested']}")
        if result["video_events"]:
            print(f"[videoroom] video events ({len(result['video_events'])}):")
            for s in result["video_events"][:10]:
                print("   ", s[:200])
        if result["gml_errors"]:
            print(f"[videoroom] GML errors ({len(result['gml_errors'])}):")
            for s in result["gml_errors"][:12]:
                print("   ", s[:220])
        if result["reqfails"]:
            print(f"[videoroom] request failures ({len(result['reqfails'])}):")
            for s in result["reqfails"][:10]:
                print("   ", s[:180])
        if result["pageerrors"]:
            for s in result["pageerrors"][:6]:
                print("   [pageerror]", str(s)[:250])
        try:
            ctx.close(); browser.close()
        except Exception:
            pass
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--watch", type=float, default=420.0)
    ap.add_argument("--tag", default="run")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--no-hard", action="store_true")
    ap.add_argument("--hm", default="full", choices=["full", "damage", "speed", "off"])
    a = ap.parse_args()
    res = run(a.watch, a.tag, headless=not a.headed,
              hard=not a.no_hard, hm=a.hm)
    return 0 if res.get("verdict") in ("ALIVE",) else 1


if __name__ == "__main__":
    sys.exit(main())
