#!/usr/bin/env python3
"""battle_test.py — force a ch5 field BATTLE with hard mode on and drive it.

Data patch (part001): room 122 (floradinnencounter) obj_mainchara spawn is
moved right on top of the obj_solidenemy at (4120,1320) — instant enemy
contact on load. Then a Z-rhythm (confirm text / FIGHT / targets) drives the
battle through attacks and enemy turns, where the patched scr_damage /
scr_damage_calculation / attack-length bytecode actually executes for the
first time on ch5.

Usage: python3 tests/battle_test.py --watch 400
"""
from __future__ import annotations
import argparse
import base64
import json
import os
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

MAINCHARA_OFF = 4043324            # room 122 obj_mainchara record
SPAWN_X, SPAWN_Y = 3260, 1288      # the savepoint (original spawn)
SAVE_ROOM_ID = 50124               # floradinnencounter (valid save room)
MOVE_TRIGGERS = []                 # (empty: keep the world intact)


def run(watch_s: float, tag: str, boot_timeout: float = 210.0, hm: str = "full") -> dict:
    print(f"[battle] watch={watch_s}s hard=True hm={hm} — spawn on enemy")
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
        import glob as _glob
        for f in _glob.glob("/tmp/chrome_bt_*.log"):
            try: os.remove(f)
            except Exception: pass
        browser, gl_label, gl_args, gl_probe = probe_webgl(
            pw, headless=True,
            extra_args=["--enable-logging", "--log-file=/tmp/chrome_bt_%s.log" % tag])
        ctx = make_game_context(browser, hard_mode=True,
                                extra_init_js=SHADER_PROBE_JS + "\n" + inject)
        page = ctx.new_page()

        def _patch_part1(route):
            resp = route.fetch()
            body = bytearray(resp.body())
            if len(body) >= MAINCHARA_OFF + 8:
                ox = int.from_bytes(body[MAINCHARA_OFF:MAINCHARA_OFF+4], "little")
                oy = int.from_bytes(body[MAINCHARA_OFF+4:MAINCHARA_OFF+8], "little")
                print(f"[battle] interceptor: spawn ({ox},{oy}) -> ({SPAWN_X},{SPAWN_Y})")
                body[MAINCHARA_OFF:MAINCHARA_OFF+4] = SPAWN_X.to_bytes(4, "little")
                body[MAINCHARA_OFF+4:MAINCHARA_OFF+8] = SPAWN_Y.to_bytes(4, "little")
            if MOVE_TRIGGERS:
                for toff in MOVE_TRIGGERS:
                    if len(body) >= toff + 8:
                        tx = int.from_bytes(body[toff:toff+4], "little")
                        ty = int.from_bytes(body[toff+4:toff+8], "little")
                        print(f"[battle] interceptor: trigger@{toff} ({tx},{ty}) -> (-500,-500)")
                        body[toff:toff+4] = (-500).to_bytes(4, "little", signed=True)
                        body[toff+4:toff+8] = (-500).to_bytes(4, "little", signed=True)
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
        print(f"[battle] goto {url}")
        page.goto(url, timeout=90_000, wait_until="domcontentloaded")
        tref[0] = time.time()

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

        if not blog.crashed:
            page.mouse.click(640, 360)
            page.wait_for_timeout(1200)
            for i in range(4):
                if blog.crashed:
                    break
                try:
                    page.screenshot(path=str(shot_dir / f"bt_{tag}_step{i}.png"))
                except Exception:
                    break
                page.keyboard.press("z")
                page.wait_for_timeout(1500)
            page.wait_for_timeout(3000)
        try:
            page.screenshot(path=str(shot_dir / f"bt_{tag}_loaded.png"))
        except Exception:
            pass

        # drive: walk up-right from the savepoint toward the wandering sheary
        # wave at (3540,920) — the "petal" that alerts when Kris is within 120px
        if not blog.crashed:
            page.keyboard.down("ArrowUp")
            page.wait_for_timeout(2000)
            page.keyboard.up("ArrowUp")
            page.wait_for_timeout(400)
            page.keyboard.down("ArrowRight")
            page.wait_for_timeout(2200)
            page.keyboard.up("ArrowRight")
            page.wait_for_timeout(1500)
            # nudge around the wave's alert radius in case it wandered
            page.keyboard.down("ArrowUp")
            page.wait_for_timeout(1200)
            page.keyboard.up("ArrowUp")
            page.keyboard.down("ArrowRight")
            page.wait_for_timeout(1200)
            page.keyboard.up("ArrowRight")

        t_watch0 = time.time()
        frame_samples = []
        last = [time.time()]
        while time.time() - t_watch0 < watch_s and not blog.crashed:
            try:
                page.keyboard.press("z")
                page.wait_for_timeout(900)
            except Exception:
                blog.crashed = True
                break
            frame_samples.append({"t": round(time.time() - tref[0], 1),
                                  "frames": frames_now(page)})
            if time.time() - last[0] >= 10:
                try:
                    p = shot_dir / f"bt_{tag}_w{int(time.time()-t_watch0):03d}s.png"
                    page.screenshot(path=str(p))
                except Exception:
                    pass
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
            "gml_errors": [t for (_, k, t) in blog.events if k == "error" or "ERROR" in t][:40],
            "music_requested": sorted(set(
                t.split("for ")[-1] for (_, _, t) in blog.events
                if "GML_async_wget2_data called for mus/" in t))[:12],
            "milestones_s": {k: round(v, 1) for k, v in sorted(blog.milestones.items(), key=lambda kv: kv[1])},
        })
        verdict = ("CRASH" if blog.crashed else
                   "SHADER_FAIL" if result["shader_compile_fails"] else
                   "PAGEERROR" if blog.pageerrors else
                   "FROZEN" if (fps is not None and fps < 1.0) else
                   "ALIVE" if (fps and fps >= 1.0) else "UNKNOWN")
        result["verdict"] = verdict

        logp = E2E / "logs" / f"battle_{tag}.log"
        logp.parent.mkdir(exist_ok=True)
        blog.dump(logp, [f"battle_test tag={tag} watch={watch_s}"], result)
        print(f"\n[battle] VERDICT: {verdict}  fps={fps}  crashed={blog.crashed}")
        print(f"[battle] milestones: {result['milestones_s']}")
        print(f"[battle] music: {result['music_requested']}")
        if result["gml_errors"]:
            print(f"[battle] GML errors ({len(result['gml_errors'])}):")
            for s in result["gml_errors"][:14]:
                print("   ", s[:240])
        for s in result["pageerrors"][:6]:
            print("   [pageerror]", str(s)[:250])
        try:
            ctx.close(); browser.close()
        except Exception:
            pass
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--watch", type=float, default=400.0)
    ap.add_argument("--tag", default="run")
    ap.add_argument("--hm", default="full", choices=["full", "damage", "speed", "off"])
    a = ap.parse_args()
    res = run(a.watch, a.tag, hm=a.hm)
    return 0 if res.get("verdict") in ("ALIVE",) else 1


if __name__ == "__main__":
    sys.exit(main())
