#!/usr/bin/env python3
"""
hazard_test.py — REAL-BROWSER overworld-hazard damage test for the DELTARUNE
web port (drweb, READ-ONLY, served at 127.0.0.1:8901).

WHAT IT DOES
------------
1. Serves the repo (via boot_harness.ensure_server), boots chapter 4 headless
   with the proven software-WebGL launch args, service_workers="block".
2. Injects the chosen OVERWORLD_DMG_PATCHES table into the served index.html
   via context.route() response rewriting (repo itself is never touched):
      --table v96        the v9.6 table (git show 95522d8:index.html) - REPRO
      --table none       v9.7 ships an empty table           - NEGATIVE CONTROL
      --table <path.json> e.g. analysis/final_patch.json (v9.8 candidate)
3. HARD MODE: localStorage 'dr_webport_hardmode'='1' is set by an init script
   BEFORE any page script; the URL uses ?hm=damage so ONLY the damage patches
   (battle 2x + overworld table) apply - the 1.2x speed patch is excluded.
4. Spawns the party next to the hazard via a crafted IndexedDB save:
   a real save file (XtronXI/deltarune-saves) edited byte-exactly (saveforge)
   to room 40260 = room_dw_churchb_extinguisher, written to the runner's
   /_savedata IDBFS store in the exact format the port's own upload feature
   uses ({timestamp, mode:33206, contents}).
   Hazard: obj_dw_churchb_extinguisher (Step_0 calls scr_damage_all_overworld)
   sprays the party; engagement is scripted arrow-walking with Z to dismiss
   the room dialog.
5. Watches for: renderer crash, pageerror (wasm abort), console tail,
   rAF frame life, and the party HP HUD (yellow HP bars in the bottom-left
   box) as the damage observable.

VERDICT
-------
  --table v96   PASS == crash/abort observed (the v9.6 defect reproduced)
  --table none  PASS == damage taken, no crash, engine alive >= 30s after
  --table <v98> PASS == damage taken, no crash, engine alive >= 30s after
                       (+ best-effort 2x-damage check via HP-bar pixels)

Exit code 0 = PASS, 1 = FAIL. Artifacts: logs/hazard_<tag>.log/.json,
shots/hazard_<tag>_*.png.
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from playwright.sync_api import sync_playwright
from boot_harness import (ensure_server, probe_webgl, make_game_context, BootLog,
                          hook_page, frames_now)

E2E = Path(__file__).resolve().parent
DRWEB = E2E.parent / "drweb"
BASE_URL = "http://127.0.0.1:8901"

# hazard rooms (canonical ids; community rooms.ts + verified against the
# port's chapter data): each room's controller object calls
# scr_damage_all_overworld. ch3 = green room (obj_room_green_room_Step_0),
# ch4 = church-B extinguisher (obj_dw_churchb_extinguisher_Step_0).
HAZARD_ROOMS = {
    1: {"chapter": 1, "room_id": 10357, "name": "room_forest_area2"},
    2: {"chapter": 2, "room_id": 20229, "name": "room_dw_mansion_sparks"},
    3: {"chapter": 3, "room_id": 30134, "name": "room_dw_green_room"},
    4: {"chapter": 4, "room_id": 40260, "name": "room_dw_churchb_extinguisher"},
}

PLACEHOLDER = "window.OVERWORLD_DMG_PATCHES = {};"

IDB_INJECT_JS = """
(async () => {
  const files = %FILES%;
  try {
    const db = await new Promise((res, rej) => { const r = indexedDB.open('/_savedata', 21);
      r.onupgradeneeded = () => { if (!r.result.objectStoreNames.contains('FILE_DATA')) r.result.createObjectStore('FILE_DATA'); };
      r.onsuccess = () => res(r.result); r.onerror = () => rej(r.error); });
    for (const [name, b64] of files) {
      const bin = atob(b64); const u8 = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i++) u8[i] = bin.charCodeAt(i);
      await new Promise((res, rej) => { const tx = db.transaction(['FILE_DATA'], 'readwrite');
        tx.objectStore('FILE_DATA').put({timestamp: new Date(), mode: 33206, contents: u8}, '/_savedata/' + name);
        tx.oncomplete = res; tx.onerror = () => rej(tx.error); });
    }
    console.log('[idb-inject] wrote ' + files.length + ' file(s) OK');
    db.close();
  } catch (e) { console.log('[idb-inject] FAILED: ' + e); }
})();
"""

# engagement walk (validated probe): reaches the spray zone, dismisses the
# room dialog with Z, sweeps the slope. ~55s, produced 3 damage HUD hits.
ENGAGE_ROOM = {
    1: [  # forest_area2: walk right into darkponman orbit bullets, retreat, repeat
        ("ArrowRight", 2800), ("hold", 2500),
        ("ArrowRight", 1500), ("hold", 2500),
        ("ArrowLeft", 1800), ("hold", 1000),
        ("ArrowRight", 2500), ("hold", 2500),
        ("ArrowLeft", 1800), ("hold", 1000),
        ("ArrowRight", 2500), ("hold", 2500),
        ("ArrowLeft", 1800), ("hold", 1000),
        ("ArrowRight", 2500), ("hold", 3000),
        ("menu", 0), ("hold", 2000),
    ],
    2: [  # mansion_sparks: sweep the corridor slowly ALONG THE TOP WALL
        # (the sparking wall plugs) pausing at each plug to catch the flicker
        ("diag_ul", 1200), ("hold", 1800),
        ("diag_ul", 1200), ("hold", 1800),
        ("diag_ul", 1200), ("hold", 1800),
        ("diag_ul", 1200), ("hold", 1800),
        ("diag_ul", 1200), ("hold", 1800),
        ("diag_ul", 1200), ("hold", 1800),
        ("diag_ul", 1200), ("hold", 1800),
        ("diag_ul", 1200), ("hold", 2500),
        ("ArrowLeft", 1200), ("hold", 2000),
        ("diag_dl", 1000), ("hold", 1500),
        ("diag_ul", 1000), ("hold", 2000),
        ("menu", 0), ("hold", 2000),
    ],
    3: [  # green room: reach the top-left couch, start the racing, crash it
        ("ArrowLeft", 2200), ("ArrowUp", 2000), ("ArrowLeft", 1500), ("ArrowUp", 1500),
        ("z", 0), ("hold", 2000), ("z", 0), ("z", 0), ("hold", 2500),
        ("ArrowRight", 900), ("ArrowLeft", 900), ("ArrowRight", 900), ("ArrowLeft", 900),
        ("z", 0), ("hold", 2000), ("ArrowUp", 800), ("ArrowDown", 800),
        ("ArrowRight", 900), ("ArrowLeft", 900), ("z", 0), ("hold", 2500),
        ("ArrowDown", 1800), ("ArrowLeft", 1800), ("z", 0), ("hold", 2000),
        ("ArrowRight", 2500), ("z", 0), ("z", 0), ("hold", 3000),
        ("ArrowUp", 1500), ("ArrowDown", 1500), ("ArrowUp", 1500), ("ArrowDown", 1500),
        ("z", 0), ("z", 0), ("hold", 4000),
    ],
    4: [
        ("ArrowRight", 2500), ("z", 0), ("z", 0),
        ("ArrowRight", 2500), ("ArrowUp", 2200),
        ("z", 0), ("ArrowUp", 2000), ("ArrowRight", 2500),
        ("z", 0), ("ArrowRight", 2200), ("ArrowUp", 2500),
        ("z", 0), ("ArrowUp", 2000), ("ArrowRight", 3000),
        ("z", 0), ("z", 0), ("ArrowDown", 2500),
        ("ArrowRight", 3000), ("z", 0),
        ("ArrowRight", 2500), ("ArrowUp", 2500),
        ("z", 0), ("ArrowUp", 2000),
    ],
}
ENGAGE = ENGAGE_ROOM[4]  # default; run_test selects per chapter


# --------------------------------------------------------------------------- #
# patch tables
# --------------------------------------------------------------------------- #
def v96_table_js() -> str:
    """Extract the v9.6 OVERWORLD_DMG_PATCHES object literal from git."""
    out = subprocess.run(
        ["git", "-C", str(DRWEB), "show", "95522d8:index.html"],
        capture_output=True, text=True, check=True).stdout
    m = re.search(r"window\.OVERWORLD_DMG_PATCHES\s*=\s*(\{.*?\});", out, re.S)
    if not m:
        raise RuntimeError("v9.6 table not found in git 95522d8:index.html")
    return m.group(1)


def json_table_js(path: Path) -> str:
    data = json.loads(path.read_text())
    return json.dumps(data, separators=(",", ":"))


# --------------------------------------------------------------------------- #
# HP HUD measurement (party HP bars are yellow, bottom-left box)
# --------------------------------------------------------------------------- #
def hud_measure(png: Path):
    try:
        from PIL import Image
    except ImportError:
        return None
    im = Image.open(png).convert("RGB")
    box = im.crop((0, 520, 640, 720))
    px = list(box.getdata())
    yellow = [(i % 640, 520 + i // 640) for i, (r, g, b) in enumerate(px)
              if r > 150 and 130 < g and b < 110]
    n = len(yellow)
    width = 0
    if yellow:
        xs = [x for x, _ in yellow]
        width = max(xs) - min(xs) + 1
    return {"yellow_px": n, "yellow_span_px": width}


# --------------------------------------------------------------------------- #
# main test
# --------------------------------------------------------------------------- #
def run_test(table_arg: str, chapter: int, hp: int, maxhp: int,
             tag: str | None, watch_s: float = 30.0,
             boot_timeout: float = 150.0, headless: bool = True) -> dict:
    import saveforge

    if table_arg == "v96":
        table_js, table_desc = v96_table_js(), "v9.6 (git 95522d8)"
        expect = "crash"
    elif table_arg == "none":
        table_js, table_desc = "{}", "none (v9.7 empty table)"
        expect = "alive"
    else:
        p = Path(table_arg)
        table_js, table_desc = json_table_js(p), f"file:{p.name}"
        expect = "alive"

    tag = tag or f"{table_arg if table_arg in ('v96', 'none') else Path(table_arg).stem}_ch{chapter}"
    hazard_room = HAZARD_ROOMS[chapter]
    print(f"[hazard_test] table={table_desc} chapter={chapter} "
          f"room={hazard_room['room_id']} ({hazard_room['name']}) hp={hp}/{maxhp}")

    # --- craft save ---------------------------------------------------------
    payload = saveforge.craft(chapter, hazard_room["room_id"], hp=hp, maxhp=maxhp)
    files = [(f"filech{chapter}_0", base64.b64encode(payload).decode())]
    inject = IDB_INJECT_JS.replace("%FILES%", json.dumps(files))

    if not ensure_server():
        return {"verdict": "FAIL", "reason": "cannot serve repo"}

    blog = BootLog()
    result = {"table": table_desc, "chapter": chapter, "room": hazard_room,
              "hp_setup": [hp, maxhp], "url_query": "?c=%d&hm=damage" % chapter,
              "expect": expect, "timings": {}, "hud_hits": [], "tag": tag}

    with sync_playwright() as pw:
        browser, gl_label, gl_args, gl_probe = probe_webgl(pw, headless=headless)
        result["webgl"] = gl_label
        ctx = make_game_context(browser, hard_mode=True, extra_init_js=inject)

        # --- route interception: patch table injection ----------------------
        replaced = [False]

        def handler(route):
            resp = route.fetch()
            body = resp.text()
            if PLACEHOLDER in body:
                body = body.replace(
                    PLACEHOLDER,
                    f"window.OVERWORLD_DMG_PATCHES = {table_js};")
                replaced[0] = True
            route.fulfill(status=resp.status, headers=dict(resp.headers), body=body)

        ctx.route(f"{BASE_URL}/index.html*", handler)
        ctx.route(f"{BASE_URL}/", handler)

        page = ctx.new_page()
        tref = [time.time()]
        hook_page(page, blog, tref, verbose=False)
        url = f"{BASE_URL}/index.html?c={chapter}&hm=damage"
        print(f"[hazard_test] goto {url}")
        page.goto(url, timeout=90_000, wait_until="domcontentloaded")
        tref[0] = time.time()
        t_goto = time.time()

        # --- boot to file select --------------------------------------------
        deadline = time.time() + boot_timeout
        while time.time() < deadline:
            if blog.milestones.get("MAIN_LOOP") is not None:
                break
            if blog.crashed:
                break
            page.wait_for_timeout(500)
        result["timings"]["main_loop_s"] = blog.milestones.get("MAIN_LOOP")
        # file select: menu.ogg fetch (or fixed wait after main loop)
        t_sel = time.time() + 45
        while time.time() < t_sel:
            if blog.crashed:
                break
            if any("mus/menu.ogg" in t for _, _, t in blog.events):
                # menu music means the file select is up; give it 2s to settle
                page.wait_for_timeout(2000)
                break
            page.wait_for_timeout(500)
        result["timings"]["file_select_s"] = round(time.time() - tref[0], 1)
        print(f"[hazard_test] file select at {result['timings']['file_select_s']}s "
              f"(main_loop={result['timings']['main_loop_s']}s, "
              f"table_injected={replaced[0]})")
        if not replaced[0]:
            print("[hazard_test] WARN: placeholder not replaced - table NOT injected!")

        if blog.crashed:
            result["crashed_early"] = True

        # --- load the save ---------------------------------------------------
        page.mouse.click(640, 360)
        page.wait_for_timeout(400)
        for _ in range(3):
            page.keyboard.press("z")
            page.wait_for_timeout(900)
        page.wait_for_timeout(4500)
        result["timings"]["room_loaded_s"] = round(time.time() - tref[0], 1)
        # evidence the room loaded: hazard-room music fetch
        result["room_music"] = next(
            (t.split("for ")[-1] for _, _, t in blog.events
             if "GML_async_wget2_data called for mus/" in t), None)
        print(f"[hazard_test] save loaded at {result['timings']['room_loaded_s']}s, "
              f"music={result['room_music']}")

        # --- engagement -------------------------------------------------------
        shot_dir = E2E / "shots"
        hud_hits = 0
        t_engage0 = time.time()
        for i, (k, ms) in enumerate(ENGAGE_ROOM[chapter]):
            if blog.crashed:
                break
            try:
                if k == "z":
                    page.keyboard.press("z")
                    page.wait_for_timeout(350)
                elif k in ("diag_ul", "diag_dl"):
                    up = "ArrowUp" if k == "diag_ul" else "ArrowDown"
                    page.keyboard.down("ArrowLeft"); page.keyboard.down(up)
                    page.wait_for_timeout(ms)
                    page.keyboard.up(up); page.keyboard.up("ArrowLeft")
                elif k == "menu":
                    page.keyboard.press("c")
                    page.wait_for_timeout(max(ms, 1200))
                elif k == "hold":
                    # rapid screenshots during holds: catch the damage flash
                    n_shots = max(1, int(ms // 500))
                    for s_i in range(n_shots):
                        if blog.crashed:
                            break
                        page.wait_for_timeout(500)
                        p = shot_dir / f"hazard_{tag}_r{i+1:02d}_{s_i}.png"
                        try:
                            page.screenshot(path=str(p))
                            m = hud_measure(p)
                            if m and m["yellow_px"] > 20:
                                hud_hits += 1
                                result["hud_hits"].append(
                                    {"step": i + 1, "shot": s_i,
                                     "t": round(time.time() - tref[0], 1), **m})
                        except Exception:
                            pass
                else:
                    page.keyboard.down(k)
                    page.wait_for_timeout(ms)
                    page.keyboard.up(k)
                page.wait_for_timeout(250)
                p = shot_dir / f"hazard_{tag}_{i+1:02d}_{k}.png"
                page.screenshot(path=str(p))
                m = hud_measure(p)
                if m and m["yellow_px"] > 20:
                    hud_hits += 1
                    result["hud_hits"].append(
                        {"step": i + 1, "t": round(time.time() - tref[0], 1), **m})
            except Exception as ex:
                print(f"[hazard_test] engagement step {i+1} failed: {ex}")
                break
        result["timings"]["engage_s"] = round(time.time() - t_engage0, 1)
        result["hud_hit_count"] = hud_hits
        print(f"[hazard_test] engagement done in {result['timings']['engage_s']}s, "
              f"HUD damage hits={hud_hits}")

        # --- post-watch -------------------------------------------------------
        f0 = frames_now(page)
        page.wait_for_timeout(2000)
        f1 = frames_now(page)
        t_watch_end = time.time() + watch_s
        z_tick = time.time()
        while time.time() < t_watch_end and not blog.crashed:
            page.wait_for_timeout(1000)
            if time.time() - z_tick > 6:
                try:
                    page.keyboard.press("z")
                except Exception:
                    pass
                z_tick = time.time()
        f2 = frames_now(page)
        result["frames_alive_post"] = bool(
            f1 is not None and f2 is not None and f2 > f1)
        # canvas render check: two shots 2.5s apart must differ (live engine)
        try:
            pa = shot_dir / f"hazard_{tag}_alive_a.png"
            pb = shot_dir / f"hazard_{tag}_alive_b.png"
            page.screenshot(path=str(pa))
            page.wait_for_timeout(2500)
            page.screenshot(path=str(pb))
            from boot_harness import diff_pngs
            d = diff_pngs(pa, pb)
            result["canvas_diff_post"] = d
            result["canvas_frozen_post"] = bool(d is not None and d < 0.0005)
        except Exception:
            result["canvas_frozen_post"] = None
        result["post_watch_s"] = watch_s
        result["crashed"] = blog.crashed
        result["pageerrors"] = [t for _, t in blog.pageerrors]
        result["gameover_music"] = any("gameover" in t for _, _, t in blog.events)
        result["snd_hurt_fetched"] = any("snd_hurt" in t for _, _, t in blog.events)
        try:
            p = shot_dir / f"hazard_{tag}_final.png"
            page.screenshot(path=str(p))
            result["final_shot"] = str(p)
            result["final_hud"] = hud_measure(p)
        except Exception:
            pass
        ctx.close()
        browser.close()

    # --- verdict -------------------------------------------------------------
    fatal_errs = [e for e in result["pageerrors"]
                  if any(s in e.lower() for s in ("abort", "runtimeerror", "unreachable",
                                                  "memory access", "wasm", "exception"))]
    result["fatal_pageerrors"] = fatal_errs
    if expect == "crash":
        frozen = result.get("canvas_frozen_post")
        ok = bool(result["crashed"] or fatal_errs
                  or (result["pageerrors"] and frozen is True))
        result["verdict"] = "PASS" if ok else "FAIL"
        if result["crashed"]:
            result["verdict_detail"] = "renderer crash reproduced"
        elif fatal_errs:
            result["verdict_detail"] = (f"wasm/pageerror abort: "
                                        f"{fatal_errs[0][:120]}")
        elif result["pageerrors"] and frozen is True:
            result["verdict_detail"] = (f"pageerror + canvas frozen: "
                                        f"{result['pageerrors'][0][:120]}")
        else:
            result["verdict_detail"] = "NO CRASH - v9.6 defect NOT reproduced"
    else:
        got_damage = (result.get("hud_hit_count", 0) > 0
                      or result.get("gameover_music"))
        # a static GAME OVER screen is a healthy engine state, not a freeze
        frozen = (result.get("canvas_frozen_post") is True
                  and not got_damage)
        ok = (not result["crashed"] and not result["pageerrors"]
              and result.get("frames_alive_post") and not frozen
              and got_damage)
        result["verdict"] = "PASS" if ok else "FAIL"
        bits = []
        if result["crashed"]: bits.append("CRASHED")
        if fatal_errs: bits.append(f"{len(fatal_errs)} fatal pageerrors")
        if not result.get("frames_alive_post"): bits.append("rAF stopped")
        if frozen: bits.append("canvas frozen without damage/gameover")
        if not result.get("hud_hit_count"): bits.append("no damage HUD seen")
        result["verdict_detail"] = "no crash, engine alive, damage taken" if ok \
            else ("; ".join(bits) or "unknown failure")

    # --- artifacts ------------------------------------------------------------
    logf = E2E / "logs" / f"hazard_{tag}.log"
    blog.dump(logf, [
        f"hazard_test table={table_desc} chapter={chapter} room={hazard_room['room_id']}",
        f"hard mode ON, ?hm=damage, hp={hp}/{maxhp}, engaged {len(ENGAGE)} steps",
    ], result)
    (E2E / "logs" / f"hazard_{tag}.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False))
    print(f"\n[hazard_test] VERDICT: {result['verdict']} — {result['verdict_detail']}")
    print(f"[hazard_test] log -> {logf}")
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description="overworld hazard damage test")
    ap.add_argument("--table", required=True,
                    help="v96 | none | <path.json> (v9.8 candidate)")
    ap.add_argument("--chapter", type=int, default=4)
    ap.add_argument("--hp", type=int, default=90)
    ap.add_argument("--maxhp", type=int, default=90)
    ap.add_argument("--watch", type=float, default=30.0)
    ap.add_argument("--tag", default=None)
    ap.add_argument("--headed", action="store_true")
    args = ap.parse_args()
    res = run_test(args.table, args.chapter, args.hp, args.maxhp,
                   args.tag, args.watch, headless=not args.headed)
    return 0 if res.get("verdict") == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
