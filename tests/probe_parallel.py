#!/usr/bin/env python3
"""probe_parallel.py — probe multiple save room ids in parallel contexts."""
from __future__ import annotations
import base64
import hashlib
import json
import sys
import time
from pathlib import Path

E2E = Path(__file__).resolve().parent
sys.path.insert(0, str(E2E))
from playwright.sync_api import sync_playwright          # noqa: E402
from boot_harness import (ensure_server, probe_webgl, make_game_context,  # noqa: E402
                          BootLog, hook_page)
import saveforge                                          # noqa: E402
from flower_test import IDB_INJECT_TMPL, SHADER_PROBE_JS  # noqa: E402
from PIL import Image                                      # noqa: E402

BASE_URL = "http://127.0.0.1:8901"
SHOT = E2E / "shots"
SHOT.mkdir(exist_ok=True)


def probe_one(browser, room: int) -> dict:
    tag = f"id{room}"
    payload = saveforge.craft(5, room, hp=90, maxhp=90,
                              base=saveforge.SAVES / "filech4_0")
    files = [("filech5_0", base64.b64encode(payload).decode())]
    inject = IDB_INJECT_TMPL.replace("%FILES%", json.dumps(files))
    blog = BootLog()
    res = {"room": room}
    ctx = make_game_context(browser, hard_mode=None,
                            extra_init_js=SHADER_PROBE_JS + "\n" + inject)
    page = ctx.new_page()
    tref = [time.time()]
    hook_page(page, blog, tref, verbose=False)
    try:
        page.goto(f"{BASE_URL}/index.html?c=5", timeout=90_000,
                  wait_until="domcontentloaded")
        tref[0] = time.time()
        deadline = time.time() + 260
        while time.time() < deadline:
            if blog.crashed or blog.milestones.get("file_select"):
                break
            page.wait_for_timeout(500)
        if blog.crashed:
            res["verdict"] = "CRASH_EARLY"
            return res
        page.wait_for_timeout(2500)
        page.mouse.click(640, 360)
        page.wait_for_timeout(600)
        for _ in range(4):
            page.keyboard.press("z")
            page.wait_for_timeout(900)
        page.wait_for_timeout(9000)
        s1 = SHOT / f"probe_{tag}_a.png"
        s2 = SHOT / f"probe_{tag}_b.png"
        page.screenshot(path=str(s1))
        page.wait_for_timeout(3500)
        page.screenshot(path=str(s2))
        h1 = hashlib.md5(Image.open(s1).tobytes()).hexdigest()[:10]
        h2 = hashlib.md5(Image.open(s2).tobytes()).hexdigest()[:10]
        im = Image.open(s1).convert("RGB")
        nonblk = sum(1 for p in im.getdata() if p != (0, 0, 0))
        errs = [t for (_, _, t) in blog.events if "could not find room" in t]
        dbg = [t for (_, _, t) in blog.events
               if t.strip() and len(t.strip()) < 40
               and not t.startswith(("[", "GML", "_Get", "__gx", "loaded",
                                     "in cache", "Success", "Attempting",
                                     "GM ", "Audio", "IndexedDB", "*", "Comm",
                                     "Memory", "YoYo"))][:8]
        res.update({"room_error": errs[0] if errs else None,
                    "animated": h1 != h2, "nonblk": nonblk,
                    "crashed": blog.crashed,
                    "pageerrors": [str(x)[:120] for x in blog.pageerrors[:3]],
                    "dbg": dbg,
                    "music": sorted({t.split("for ")[-1] for (_, _, t) in blog.events
                                     if "GML_async_wget2_data called for mus/" in t})[:4]})
        res["verdict"] = ("INVALID" if errs else "CRASH" if blog.crashed
                          else "PAGEERR" if blog.pageerrors else "OK")
    except Exception as ex:
        res["verdict"] = f"EXC:{str(ex)[:80]}"
    finally:
        try:
            ctx.close()
        except Exception:
            pass
    return res


def main():
    ids = [int(x) for x in sys.argv[1:]]
    if not ensure_server():
        print("server fail"); sys.exit(1)
    results = []
    with sync_playwright() as pw:
        browser, gl_label, gl_args, _ = probe_webgl(pw, headless=True)
        # run 3 at a time
        for i in range(0, len(ids), 3):
            batch = ids[i:i+3]
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=3) as ex:
                futs = [ex.submit(probe_one, browser, r) for r in batch]
                for f in futs:
                    r = f.result()
                    results.append(r)
                    print(f"room {r['room']}: {r.get('verdict'):9s} "
                          f"animated={r.get('animated')} nonblk={r.get('nonblk')} "
                          f"music={r.get('music')} dbg={r.get('dbg')}", flush=True)
        browser.close()
    (E2E / "logs" / "probe_parallel.json").write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
