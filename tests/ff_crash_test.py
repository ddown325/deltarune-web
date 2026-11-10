#!/usr/bin/env python3
"""ff_crash_test.py — reproduce the field-encounter renderer crash in FIREFOX.

Same data patch as battle_test.py (spawn near the field enemies in room 122)
but on Firefox's WebGL stack. If Firefox also dies -> the game's encounter
draw call is at fault (engine/GPU-independent). If Firefox survives ->
Chromium software-GL (SwiftShader) specific.

Usage: python3 tests/ff_crash_test.py
"""
from __future__ import annotations
import base64
import json
import sys
import time
from pathlib import Path

E2E = Path(__file__).resolve().parent
sys.path.insert(0, str(E2E))
from playwright.sync_api import sync_playwright         # noqa: E402
from boot_harness import ensure_server, BootLog, hook_page, frames_now, FRAME_PROBE_INIT  # noqa: E402
from flower_test import SHADER_PROBE_JS, IDB_INJECT_TMPL  # noqa: E402
import saveforge  # noqa: E402

BASE_URL = "http://127.0.0.1:8901"
MAINCHARA_OFF = 4043324
SPAWN_X, SPAWN_Y = 4100, 1330
SAVE_ROOM_ID = 50124


def main() -> int:
    payload = saveforge.craft(5, SAVE_ROOM_ID, hp=90, maxhp=90,
                              base=saveforge.SAVES / "filech4_0")
    files = [("filech5_0", base64.b64encode(payload).decode())]
    inject = IDB_INJECT_TMPL.replace("%FILES%", json.dumps(files))

    if not ensure_server():
        print("cannot serve repo")
        return 1

    blog = BootLog()
    shot_dir = E2E / "shots"
    shot_dir.mkdir(exist_ok=True)

    with sync_playwright() as pw:
        browser = pw.firefox.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1280, "height": 720})
        ctx.add_init_script(FRAME_PROBE_INIT + "\n" + SHADER_PROBE_JS + "\n" + inject)
        page = ctx.new_page()

        def _patch(route):
            resp = route.fetch()
            body = bytearray(resp.body())
            if len(body) >= MAINCHARA_OFF + 8:
                print(f"[ff] patch spawn -> ({SPAWN_X},{SPAWN_Y})")
                body[MAINCHARA_OFF:MAINCHARA_OFF+4] = SPAWN_X.to_bytes(4, "little")
                body[MAINCHARA_OFF+4:MAINCHARA_OFF+8] = SPAWN_Y.to_bytes(4, "little")
            route.fulfill(status=resp.status, headers=dict(resp.headers),
                          body=bytes(body))
        page.route("**/chapter5.unx.part001*", _patch)

        tref = [time.time()]
        hook_page(page, blog, tref, verbose=False,
                  extra_milestones=[("save_injected", "[idb-inject] wrote"),
                                    ("file_select", "mus/menu.ogg"),
                                    ("gml_error", "ERROR in")])
        url = f"{BASE_URL}/index.html?c=5&hm=full"
        print(f"[ff] goto {url}")
        page.goto(url, timeout=120_000, wait_until="domcontentloaded")
        tref[0] = time.time()

        # wait for file select (firefox may be slower)
        t_sel = time.time() + 300
        while time.time() < t_sel:
            if blog.crashed:
                break
            if blog.milestones.get("file_select") is not None:
                page.wait_for_timeout(2500)
                break
            page.wait_for_timeout(1000)
        print(f"[ff] file_select at {blog.milestones.get('file_select')}")

        if not blog.crashed:
            page.mouse.click(640, 360)
            page.wait_for_timeout(1500)
            for i in range(4):
                if blog.crashed:
                    break
                try:
                    page.screenshot(path=str(shot_dir / f"ff_step{i}.png"))
                except Exception:
                    break
                page.keyboard.press("z")
                page.wait_for_timeout(1800)
            page.wait_for_timeout(4000)

        # watch
        t0 = time.time()
        while time.time() - t0 < 60 and not blog.crashed:
            try:
                page.keyboard.press("z")
                page.wait_for_timeout(1000)
            except Exception:
                blog.crashed = True
                blog.crash_t = time.time() - tref[0]
                break
        try:
            page.screenshot(path=str(shot_dir / "ff_end.png"))
        except Exception:
            pass

        print(f"\n[ff] crashed={blog.crashed} crash_t={blog.crash_t}")
        print(f"[ff] milestones: { {k: round(v,1) for k,v in blog.milestones.items()} }")
        errs = [t for (_, k, t) in blog.events if k == "error" or "ERROR" in t][:10]
        for e in errs:
            print("   [err]", e[:200])
        logp = E2E / "logs" / "ff_crash.log"
        logp.parent.mkdir(exist_ok=True)
        blog.dump(logp, ["ff_crash_test spawn=(4100,1330) hm=full"],
                  {"crashed": blog.crashed, "crash_t": blog.crash_t})
        try:
            ctx.close(); browser.close()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
