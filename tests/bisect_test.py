#!/usr/bin/env python3
"""bisect_test.py — find which room-122 object class causes the load crash.

The crash: spawning the party at (4100,1330) in room_dw_garden_floradinnencounter
kills the Chromium renderer within ~2s of room load (native death, no GML error,
no OOM, hm=off too). This script moves ONE object class (all its instance
records) to a far corner via the part001 route patch, spawns at (4100,1330),
and reports CRASH / ALIVE. Iterate over classes to find the culprit.

Usage: python3 tests/bisect_test.py --remove class1,class2 --tag name
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
                          BootLog, hook_page, frames_now)
from flower_test import SHADER_PROBE_JS, IDB_INJECT_TMPL  # noqa: E402
import saveforge  # noqa: E402

BASE_URL = "http://127.0.0.1:8901"
MAINCHARA_OFF = 4043324
import os as _os
SPAWN_X, SPAWN_Y = (int(_os.environ.get("BSX", 4100)), int(_os.environ.get("BSY", 1330)))
SAVE_ROOM_ID = 50124

# instance offsets by class (room 122), from scripts/room122_instances.pkl
import pickle
INSTS = pickle.load(open(Path(__file__).resolve().parent.parent.parent /
                          "scripts/room122_instances.pkl", "rb"))
BY_CLASS = {}
for p, x, y, nm in INSTS:
    BY_CLASS.setdefault(nm, []).append(p)


def run(remove_classes, tag, hm="off", watch=45.0):
    remove_offs = []
    for c in remove_classes:
        remove_offs += BY_CLASS.get(c, [])
    print(f"[bisect] removing {remove_classes} ({len(remove_offs)} instances), "
          f"spawn=({SPAWN_X},{SPAWN_Y}) hm={hm} watch={watch}s")

    payload = saveforge.craft(5, SAVE_ROOM_ID, hp=90, maxhp=90,
                              base=saveforge.SAVES / "filech4_0")
    files = [("filech5_0", base64.b64encode(payload).decode())]
    inject = IDB_INJECT_TMPL.replace("%FILES%", json.dumps(files))

    if not ensure_server():
        return {"verdict": "FAIL"}
    blog = BootLog()
    with sync_playwright() as pw:
        browser, gl_label, gl_args, gl_probe = probe_webgl(pw, headless=True)
        ctx = make_game_context(browser, hard_mode=None,
                                extra_init_js=SHADER_PROBE_JS + "\n" + inject)
        page = ctx.new_page()

        def _patch(route):
            resp = route.fetch()
            body = bytearray(resp.body())
            if len(body) >= MAINCHARA_OFF + 8:
                body[MAINCHARA_OFF:MAINCHARA_OFF+4] = SPAWN_X.to_bytes(4, "little")
                body[MAINCHARA_OFF+4:MAINCHARA_OFF+8] = SPAWN_Y.to_bytes(4, "little")
            for toff in remove_offs:
                if len(body) >= toff + 8:
                    body[toff:toff+4] = (-2000).to_bytes(4, "little", signed=True)
                    body[toff+4:toff+8] = (-2000).to_bytes(4, "little", signed=True)
            route.fulfill(status=resp.status, headers=dict(resp.headers),
                          body=bytes(body))
        page.route("**/chapter5.unx.part001*", _patch)

        tref = [time.time()]
        hook_page(page, blog, tref, verbose=False,
                  extra_milestones=[("save_injected", "[idb-inject] wrote"),
                                    ("file_select", "mus/menu.ogg"),
                                    ("gml_error", "ERROR in")])
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
            page.wait_for_timeout(5000)
        # extra settle window: the crash hit ~2s after load in prior runs
        t0 = time.time()
        while time.time() - t0 < watch and not blog.crashed:
            try:
                page.wait_for_timeout(1000)
            except Exception:
                blog.crashed = True
                blog.crash_t = time.time() - tref[0]
                break

        verdict = "CRASH" if blog.crashed else "ALIVE"
        print(f"[bisect] {tag}: {verdict}" + (f" at {blog.crash_t:.1f}s" if blog.crash_t else ""))
        errs = [t for (_, k, t) in blog.events if k == "error" or "ERROR" in t][:6]
        for e in errs:
            print("   [err]", e[:200])
        try:
            ctx.close(); browser.close()
        except Exception:
            pass
    return {"verdict": verdict, "tag": tag}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--remove", default="", help="comma list of object classes to move away")
    ap.add_argument("--tag", required=True)
    ap.add_argument("--hm", default="off")
    a = ap.parse_args()
    classes = [c for c in a.remove.split(",") if c]
    res = run(classes, a.tag, a.hm)
    return 1 if res["verdict"] == "CRASH" else 0


if __name__ == "__main__":
    sys.exit(main())
