#!/usr/bin/env python3
"""flower_test.py — headless ch5 Field-of-Hopes render/crash gate.

Boots chapter 5 from the local drweb/ copy (byte-identical to the deployed
build), injects a forged filech5_0 save (real ch4 base, room overridden —
the V2 save format is byte-compatible across ch2-5) into the runner's
IndexedDB, loads it through the REAL file-select flow, and watches the
target room for: renderer crash, pageerror, shader-compile failures (a
WebGL probe injected at document-start watches every compileShader/
linkProgram), GML errors, rAF fps, and canvas output (non-blankness +
change detection across shots).

Usage (serve the FULL repo — without mus/ the title flow stalls in a
music-retry storm; see FLOWERS_REPORT.md):
  python3 tests/flower_test.py --room 50124 --tag field --watch 120

PASS = verdict ALIVE: room renders, animates, responds to input, no
shader failure, no pageerror, no crash for the whole watch window.
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
import time
from pathlib import Path

E2E = Path(__file__).resolve().parent          # .../tests
sys.path.insert(0, str(E2E))
from playwright.sync_api import sync_playwright         # noqa: E402
from boot_harness import (ensure_server, probe_webgl, make_game_context,   # noqa: E402
                          BootLog, hook_page, frames_now, analyze_png)

import saveforge  # noqa: E402

DRWEB = E2E.parent / "drweb"
BASE_URL = "http://127.0.0.1:8901"

# SAVE ROOM-ID MAP (this build) — see tests/FLOWERS_REPORT.md.
# WARNING: the ch5 save room ids are a SPARSE list that does NOT match the
# community rooms.ts beyond the low light-world rooms. Verified by probing
# (tests/probe_parallel.py): 50013 valid, 50118/50119/50120/50121 INVALID
# ("could not find room with id"), 50122/50124/50125/50131/50135 valid.
# 50124 = the Field of Hopes flower field (field_of_hopes_insaneintherain
# theme); 50131 = another field segment (same theme); 50122 = outdoor LW
# (mus/birds_ch5). Invalid ids fall back to the animated Annoying-Dog error
# screen with mus/inukuma_wip.ogg.
ROOM_NAMES = {
    50013: "light-world cutscene house room (valid id; 'living room + Susie')",
    50024: "valid (probe pivot)",
    50054: "valid (probe pivot)",
    50075: "valid (probe pivot)",
    50114: "valid (probe pivot)",
    50122: "outdoor light world (mus/birds_ch5) - VALID",
    50124: "FIELD OF HOPES flower field (insaneintherain theme) - VALID",
    50125: "field-adjacent room - VALID",
    50131: "field segment (insaneintherain theme) - VALID",
    50135: "valid (probe pivot)",
}

# document-start probe: log every WebGL shader compile/link failure with a
# source excerpt. Runs before page scripts; purely observational.
SHADER_PROBE_JS = r"""
(() => {
  const wrap = (proto, glname) => {
    if (!proto) return;
    const oc = proto.compileShader, og = proto.getShaderParameter;
    if (!oc || oc.__drProbe) return;
    proto.compileShader = function (sh) {
      oc.call(this, sh);
      try {
        if (!og.call(this, sh, this.COMPILE_STATUS)) {
          const info = this.getShaderInfoLog(sh) || '';
          const src = (this.getShaderSource(sh) || '').replace(/\r/g, '');
          console.log('[shaderprobe] ' + glname + ' COMPILE FAIL: ' + info.trim().split('\n')[0]);
          const head = src.slice(0, 400).replace(/\n/g, '\\n');
          console.log('[shaderprobe] src400: ' + head);
        }
      } catch (e) {}
    };
    const ol = proto.linkProgram, op = proto.getProgramParameter;
    if (ol && !ol.__drProbe) {
      proto.linkProgram = function (pr) {
        ol.call(this, pr);
        try {
          if (!op.call(this, pr, this.LINK_STATUS)) {
            console.log('[shaderprobe] ' + glname + ' LINK FAIL: ' + (this.getProgramInfoLog(pr) || '').trim().slice(0, 200));
          }
        } catch (e) {}
      };
      ol.__drProbe = true;
    }
    oc.__drProbe = true;
  };
  wrap(window.WebGLRenderingContext, 'gl1');
  wrap(window.WebGL2RenderingContext, 'gl2');
})();
"""

IDB_INJECT_TMPL = """
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


def run(room: int, watch_s: float, tag: str, boot_timeout: float = 210.0,
        headless: bool = True, hard: bool = False, hm: str = "full",
        walk: bool = False, walk_plan: str = None) -> dict:
    room_name = ROOM_NAMES.get(room, f"room_{room}")
    print(f"[flower_test] room={room} ({room_name}) watch={watch_s}s tag={tag} "
          f"hard={hard} hm={hm} walk={walk}")

    # ch5 base: none exists in the community repo; the V2 save format is
    # byte-compatible across ch2-5 (3055 lines, 2509 flags), so a real ch4
    # save with the room overridden is the carrier.
    payload = saveforge.craft(5, room, hp=90, maxhp=90,
                              base=saveforge.SAVES / "filech4_0")
    files = [("filech5_0", base64.b64encode(payload).decode())]
    inject = IDB_INJECT_TMPL.replace("%FILES%", json.dumps(files))

    if not ensure_server():
        return {"verdict": "FAIL", "reason": "cannot serve repo"}

    blog = BootLog()
    result = {"room": room, "room_name": room_name, "tag": tag, "timings": {}}
    shot_dir = E2E / "shots"
    shot_dir.mkdir(exist_ok=True)

    with sync_playwright() as pw:
        browser, gl_label, gl_args, gl_probe = probe_webgl(pw, headless=headless)
        result["webgl"] = gl_label
        ctx = make_game_context(browser, hard_mode=(True if hard else None),
                                extra_init_js=SHADER_PROBE_JS + "\n" + inject)
        page = ctx.new_page()
        tref = [time.time()]
        hook_page(page, blog, tref, verbose=False,
                  extra_milestones=[("save_injected", "[idb-inject] wrote"),
                                    ("file_select", "mus/menu.ogg"),
                                    ("room_music", "GML_async_wget2_data called for mus/"),
                                    ("dll_fail", "shader_replace_simple DLL failed"),
                                    ("gml_error", "ERROR in")])
        url = f"{BASE_URL}/index.html?c=5&hm={hm}"
        print(f"[flower_test] goto {url}")
        page.goto(url, timeout=90_000, wait_until="domcontentloaded")
        tref[0] = time.time()

        # --- boot to main loop ----------------------------------------------
        deadline = time.time() + boot_timeout
        while time.time() < deadline:
            if blog.milestones.get("MAIN_LOOP") is not None or blog.crashed:
                break
            page.wait_for_timeout(500)
        result["timings"]["main_loop_s"] = blog.milestones.get("MAIN_LOOP")

        # --- wait for file select (menu music) --------------------------------
        # ch5's logo screen runs ~80s before the title; wait properly.
        t_sel = time.time() + 160
        while time.time() < t_sel:
            if blog.crashed:
                break
            if blog.milestones.get("file_select") is not None:
                page.wait_for_timeout(2500)
                break
            page.wait_for_timeout(500)
        result["timings"]["file_select_s"] = blog.milestones.get("file_select")
        page.screenshot(path=str(shot_dir / f"flower_{tag}_fileselect.png"))

        # --- load the save (step-by-step, screenshot after every input) -------
        step_shots = []
        if not blog.crashed:
            page.mouse.click(640, 360)
            page.wait_for_timeout(1200)
            for i in range(6):
                if blog.crashed:
                    break
                p = shot_dir / f"flower_{tag}_step{i}.png"
                page.screenshot(path=str(p))
                step_shots.append(str(p))
                page.keyboard.press("z")
                page.wait_for_timeout(1800)
            p = shot_dir / f"flower_{tag}_step6.png"
            page.screenshot(path=str(p))
            step_shots.append(str(p))
            page.wait_for_timeout(4000)
        result["timings"]["after_load_s"] = round(time.time() - tref[0], 1)

        # --- watch the room: periodic input + change detection ----------------
        # walk=True: hold each direction ~2s (real walking sweeps through the
        # field — petals, grass, borders) instead of single key taps.
        t_watch0 = time.time()
        frame_samples = []
        shots = []
        input_cycle = ["z", "ArrowRight", "ArrowDown", "z", "ArrowLeft", "ArrowUp"]
        walk_cycle = ["ArrowRight", "ArrowDown", "ArrowLeft", "ArrowUp"]
        walk_hold_ms = 4000
        plan = None
        if walk_plan:
            DIRK = {"L": "ArrowLeft", "R": "ArrowRight", "U": "ArrowUp", "D": "ArrowDown", "Z": "Z"}
            plan = [(DIRK[p[0]], float(p[1:])) for p in walk_plan.split(",") if p]
        i_in = 0
        last = [time.time()]
        while time.time() - t_watch0 < watch_s:
            if blog.crashed:
                break
            if plan:
                k, hold = plan[i_in % len(plan)]
                if k == "Z":   # Z-rhythm phase: press Z every ~800ms for `hold` seconds
                    t_end = time.time() + min(hold, max(0.1, watch_s - (time.time() - t_watch0)))
                    while time.time() < t_end and not blog.crashed:
                        page.keyboard.press("z")
                        page.wait_for_timeout(800)
                else:
                    page.keyboard.down(k)
                    page.wait_for_timeout(min(hold, max(0.1, watch_s - (time.time() - t_watch0))) * 1000)
                    page.keyboard.up(k)
                    page.wait_for_timeout(300)
            elif walk:
                k = walk_cycle[i_in % len(walk_cycle)]
                page.keyboard.down(k)
                page.wait_for_timeout(walk_hold_ms)
                page.keyboard.up(k)
                if i_in % 4 == 3:          # occasional interact while circling
                    page.keyboard.press("z")
            else:
                page.wait_for_timeout(2500)
                k = input_cycle[i_in % len(input_cycle)]
                page.keyboard.press(k)
            i_in += 1
            frame_samples.append({"t": round(time.time() - tref[0], 1),
                                  "frames": frames_now(page)})
            if time.time() - last[0] >= 8:
                p = shot_dir / f"flower_{tag}_w{int(time.time()-t_watch0):03d}s.png"
                page.screenshot(path=str(p))
                shots.append(analyze_png(p))
                last[0] = time.time()

        result["frame_samples"] = frame_samples[-8:]
        fps = None
        if len(frame_samples) >= 2:
            a, b = frame_samples[-2], frame_samples[-1]
            if a["frames"] is not None and b["frames"] is not None and b["t"] > a["t"]:
                fps = round((b["frames"] - a["frames"]) / (b["t"] - a["t"]), 1)
        result["fps_last"] = fps
        result["shots"] = shots
        result["crashed"] = blog.crashed
        result["crash_t"] = blog.crash_t
        result["pageerrors"] = blog.pageerrors[:20]

        # console evidence buckets
        def bucket(sub):
            return [t for (_, _, t) in blog.events if sub in t]
        result["shader_compile_fails"] = bucket("[shaderprobe]")
        result["dll_fail_msgs"] = bucket("shader_replace_simple")
        result["gml_errors"] = [t for (_, k, t) in blog.events
                                if k == "error" or "ERROR" in t][:30]
        result["music_requested"] = sorted(set(
            t.split("for ")[-1] for (_, _, t) in blog.events
            if "GML_async_wget2_data called for mus/" in t))[:10]
        result["milestones_s"] = {k: round(v, 1) for k, v in
                                  sorted(blog.milestones.items(), key=lambda kv: kv[1])}

        # verdict
        if blog.crashed:
            verdict = "CRASH"
        elif result["shader_compile_fails"]:
            verdict = "SHADER_FAIL"
        elif fps is not None and fps < 1.0:
            verdict = "FROZEN"
        elif blog.pageerrors:
            verdict = "PAGEERROR"
        elif fps and fps >= 1.0:
            verdict = "ALIVE"
        else:
            verdict = "UNKNOWN"
        result["verdict"] = verdict

        # dump full log
        logp = E2E / "logs" / f"flower_{tag}.log"
        logp.parent.mkdir(exist_ok=True)
        blog.dump(logp, [f"flower_test room={room} ({room_name}) tag={tag}"], result)
        print(f"\n[flower_test] VERDICT: {verdict}  fps={fps}  crashed={blog.crashed}")
        print(f"[flower_test] milestones: {result['milestones_s']}")
        print(f"[flower_test] music: {result['music_requested']}")
        if result["shader_compile_fails"]:
            print(f"[flower_test] SHADER FAILURES: {len(result['shader_compile_fails'])}")
            for s in result["shader_compile_fails"][:6]:
                print("   ", s[:220])
        if result["dll_fail_msgs"]:
            for s in result["dll_fail_msgs"][:4]:
                print("   [dll]", s[:160])
        if result["gml_errors"]:
            print(f"[flower_test] error lines: {len(result['gml_errors'])}")
            for s in result["gml_errors"][:8]:
                print("   [err]", s[:200])
        if result["pageerrors"]:
            for s in result["pageerrors"][:5]:
                print("   [pageerror]", s[:200])
        try:
            ctx.close(); browser.close()
        except Exception:
            pass
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--room", type=int, required=True)
    ap.add_argument("--watch", type=float, default=100.0)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--hard", action="store_true",
                    help="hard mode ON (localStorage set before page scripts)")
    ap.add_argument("--hm", default="full", choices=["full", "damage", "speed", "off"],
                    help="hard-mode patch component (?hm= URL param)")
    ap.add_argument("--walk", action="store_true",
                    help="hold-key walking sweeps instead of key taps")
    ap.add_argument("--walk-plan", default=None,
                    help="directional hold plan, e.g. 'L20,R20,U10,D10' (overrides --walk cycle)")
    a = ap.parse_args()
    res = run(a.room, a.watch, a.tag, headless=not a.headed,
              hard=a.hard, hm=a.hm, walk=a.walk, walk_plan=a.walk_plan)
    return 0 if res.get("verdict") in ("ALIVE",) else 1


if __name__ == "__main__":
    sys.exit(main())
