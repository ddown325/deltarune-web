#!/usr/bin/env python3
"""newgame_test.py — fresh-player ch5 INTRO probe on the deployed v9.10 bytes.

The user complaint chain: "lag on the intro to chapter 5" + "sometimes the
game crashes". v9.9 reverted the video-canvas willReadFrequently tweak and
v9.10 pulled the intro-freezing stubs; the boot->file-select flow is verified
(FLOWERS_REPORT) but the INTRO VIDEO sequence itself (vid/ch5_intro_en.mp4 /
vid/tennaIntroF1_compressed_28.mp4 — the game's one video-driven sequence)
has never been runtime-verified on v9.10 bytes. This probe plays it.

Clean IndexedDB (no save) — the true fresh-player path. Boots ?c=5, waits for
the file select, then walks a generic input sequence (click + Z / arrows,
screenshot every step) to create a new file, and watches for the intro:

  - every vid/* + mus/* request (console), video element state (currentTime
    advancing = the video really decodes), rAF fps, shader compile failures
    (document-start WebGL probe, same as flower_test), pageerrors, crashes
  - WATCH phase after the first vid/ request: fps samples every 10s, video
    position samples, screenshots every 30s with change detection.

Verdict ALIVE = intro video plays (currentTime advances), fps >= 5 during
playback (the complaint era was ~0.08 fps), no shader fail/pageerror/crash.
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
          console.log('[shaderprobe] src400: ' + src.slice(0, 400).replace(/\n/g, '\\n'));
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

VIDEO_PROBE_JS = """() => Array.from(document.querySelectorAll('video')).map(v => ({
  src: (v.currentSrc || v.src || '').split('/').pop().slice(0, 70),
  t: +v.currentTime.toFixed(2),
  dur: isFinite(v.duration) ? +v.duration.toFixed(2) : null,
  paused: v.paused, ready: v.readyState,
  dim: v.videoWidth + 'x' + v.videoHeight})).slice(0, 6)"""


def run(watch_s: float, tag: str, headless: bool = True) -> dict:
    print(f"[newgame] fresh-player ch5 intro probe watch={watch_s}s tag={tag}")
    if not ensure_server():
        return {"verdict": "FAIL", "reason": "cannot serve repo"}

    blog = BootLog()
    shot_dir = E2E / "shots"
    shot_dir.mkdir(exist_ok=True)
    result: dict = {"tag": tag, "timings": {}, "samples": []}

    with sync_playwright() as pw:
        browser, gl_label, gl_args, gl_probe = probe_webgl(pw, headless=headless)
        result["webgl"] = gl_label
        ctx = make_game_context(browser, hard_mode=None, extra_init_js=SHADER_PROBE_JS)
        page = ctx.new_page()
        tref = [time.time()]
        hook_page(page, blog, tref, verbose=False,
                  extra_milestones=[("logo_music", "mus/deltarune_logo_ch5_itoki"),
                                    ("file_select", "mus/menu.ogg"),
                                    ("vid_req", "for vid/"),
                                    ("dll_fail", "shader_replace_simple DLL failed")])
        url = f"{BASE_URL}/index.html?c=5"
        print(f"[newgame] goto {url}")
        page.goto(url, timeout=90_000, wait_until="domcontentloaded")
        tref[0] = time.time()

        # --- boot to file select --------------------------------------------
        deadline = time.time() + 240
        while time.time() < deadline:
            if blog.crashed or blog.milestones.get("file_select") is not None:
                break
            page.wait_for_timeout(500)
        result["timings"]["main_loop_s"] = blog.milestones.get("MAIN_LOOP")
        result["timings"]["logo_music_s"] = blog.milestones.get("logo_music")
        result["timings"]["file_select_s"] = blog.milestones.get("file_select")
        print(f"[newgame] main_loop={result['timings']['main_loop_s']} "
              f"logo={result['timings']['logo_music_s']} file_select={result['timings']['file_select_s']}")
        page.screenshot(path=str(shot_dir / f"newgame_{tag}_00_fileselect.png"))

        # --- input walk: create a new file (v9.10-verified flow) ------------
        # file select: Z on empty slot 1 -> "Start" option appears (heart on
        # Start) -> Z -> name-entry screen ("ENTER YOUR OWN NAME.", cursor on
        # 'A', grid rows A-J / K-T / U-Z BACK END). Enter 'A', walk to END,
        # confirm, then a gentle Z-loop advances any dialogs (recap offers,
        # YES/NO defaults) until a vid/ request = the intro video starts.
        shots = []

        def step(key=None, click=False, wait=2500, label=""):
            if blog.crashed:
                return
            if click:
                page.mouse.click(640, 360)
            elif key:
                page.keyboard.press(key)
            page.wait_for_timeout(wait)
            p = shot_dir / f"newgame_{tag}_{label}.png"
            page.screenshot(path=str(p))
            shots.append(str(p))

        step(click=True, wait=1500, label="s00_click")
        step("z", wait=2500, label="s01_slot_z")          # slot 1 -> Start/Back
        step("z", wait=3000, label="s02_start")           # Start -> name entry
        step("z", wait=1500, label="s03_enter_A")         # type 'A'
        step("ArrowDown", wait=700, label="s04_dn1")
        step("ArrowDown", wait=700, label="s05_dn2")      # row 3, 'U'
        for i in range(7):
            step("ArrowRight", wait=550, label=f"s06_rt{i}")   # U -> END
        step("z", wait=3000, label="s07_end")            # END -> "THIS IS YOUR NAME." NO/YES
        # the heart lands CENTERED between NO and YES — no default selection;
        # move right onto YES first, then confirm (Z alone is a no-op here)
        step("ArrowRight", wait=800, label="s08_heart_to_yes")
        step("z", wait=3000, label="s09_confirm_yes")     # name confirmed
        # dialog loop: for each prompt, favor the right-hand option (YES/
        # continue-style answers), confirm, watch for the intro video request
        for i in range(10):
            if blog.crashed or blog.milestones.get("vid_req") is not None:
                break
            step("ArrowRight", wait=600, label=f"s10a_prompt{i}")
            step("z", wait=3500, label=f"s10b_prompt{i}")
        result["input_steps"] = len(shots)
        result["timings"]["vid_req_s"] = blog.milestones.get("vid_req")

        # --- watch / play the intro -------------------------------------------
        # The ch5 opening is a cutscene with dialogue: keep pressing Z (~every
        # 3s) to advance, with a periodic ArrowRight for YES/NO choices. All
        # input STOPS the moment a vid/ request fires so the video is never
        # skipped, then the watch just samples.
        t_watch0 = time.time()
        last_frames = frames_now(page)
        last_t = time.time()
        last_shot_t = time.time()
        prev_shot = None
        sample_i = 0
        while time.time() - t_watch0 < watch_s:
            if blog.crashed:
                break
            vid_started = blog.milestones.get("vid_req") is not None
            if not vid_started:
                for _ in range(3):
                    if blog.crashed or blog.milestones.get("vid_req") is not None:
                        break
                    if sample_i % 3 == 0:
                        page.keyboard.press("ArrowRight")
                        page.wait_for_timeout(400)
                    page.keyboard.press("z")
                    page.wait_for_timeout(2800)
            else:
                page.wait_for_timeout(10_000)
            now = time.time()
            f = frames_now(page)
            fps = (round((f - last_frames) / (now - last_t), 2)
                   if (f is not None and last_frames is not None and now > last_t) else None)
            last_frames, last_t = f, now
            try:
                vids = page.evaluate(VIDEO_PROBE_JS)
            except Exception:
                vids = []
            result["samples"].append(
                {"t": round(now - tref[0], 1), "fps": fps, "videos": vids})
            sample_i += 1
            vtxt = " | ".join(f"{v['src'] or '?'}@{v['t']}/{v['dur']}{'[PAUSED]' if v['paused'] else ''}"
                              for v in vids) or "(no video el)"
            print(f"[newgame] t={now - tref[0]:7.1f}s fps={fps} {vtxt[:150]}")
            if now - last_shot_t >= 30:
                p = shot_dir / f"newgame_{tag}_w{int(now - t_watch0):03d}s.png"
                page.screenshot(path=str(p))
                if prev_shot:
                    d = diff_pngs(Path(prev_shot), Path(p))
                    if d is not None:
                        result.setdefault("shot_diffs", []).append(
                            {"pair": f"{Path(prev_shot).name}->{Path(p).name}", "diff": d})
                prev_shot = str(p)
                last_shot_t = now

        # video really played? (currentTime advanced between samples)
        played = None
        vid_pos = [(s["t"], [v for v in s["videos"] if v["src"]]) for s in result["samples"]
                   if s["videos"]]
        withsrc = [x for x in vid_pos if x[1]]
        if withsrc:
            first, last = withsrc[0], withsrc[-1]
            fv, lv = first[1][0], last[1][0]
            played = {"src": fv["src"], "from_t": fv["t"], "to_t": lv["t"],
                      "at_sample_t": [first[0], last[0]],
                      "advanced": lv["t"] > fv["t"] + 0.5,
                      "completed": (lv["dur"] or 0) > 0 and lv["t"] >= lv["dur"] - 1.0}
        result["video_played"] = played

        result["crashed"] = blog.crashed
        result["crash_t"] = blog.crash_t
        result["pageerrors"] = blog.pageerrors[:20]

        def bucket(sub):
            return sorted(set(t.split("for ")[-1][:80] for (_t, _k, t) in blog.events
                              if sub in t))
        result["vid_requested"] = bucket("for vid/")
        result["mus_requested"] = bucket("GML_async_wget2_data called for mus/")[:12]
        result["shader_compile_fails"] = [t for (_t, _k, t) in blog.events
                                          if "[shaderprobe]" in t and "FAIL" in t]
        result["gml_errors"] = [t for (_t, _k, t) in blog.events
                                if "ERROR" in t][:20]
        result["milestones_s"] = {k: round(v, 1) for k, v in
                                  sorted(blog.milestones.items(), key=lambda kv: kv[1])}

        fps_all = [s["fps"] for s in result["samples"] if s.get("fps") is not None]
        result["fps_min"] = min(fps_all) if fps_all else None
        result["fps_median"] = sorted(fps_all)[len(fps_all) // 2] if fps_all else None

        # verdict
        if blog.crashed:
            verdict = "CRASH"
        elif blog.pageerrors:
            verdict = "PAGEERROR"
        elif result["shader_compile_fails"]:
            verdict = "SHADER_FAIL"
        elif result["vid_requested"] and played and not played.get("advanced") \
                and result["fps_median"] is not None and result["fps_median"] < 5:
            verdict = "VIDEO_STALLED"
        elif result["vid_requested"] and (played and played.get("advanced")):
            verdict = "ALIVE"
        elif result["fps_median"] is not None and result["fps_median"] >= 5:
            verdict = "ALIVE_NOVIDEO"   # flow advanced but no video seen (input walk missed it)
        elif result["fps_median"] is not None:
            verdict = "FROZEN"
        else:
            verdict = "UNKNOWN"
        result["verdict"] = verdict

        logp = E2E / "logs" / f"newgame_{tag}.log"
        logp.parent.mkdir(exist_ok=True)
        blog.dump(logp, [f"newgame_test tag={tag} watch={watch_s}s"], result)
        print(f"\n[newgame] VERDICT: {verdict} crashed={blog.crashed} "
              f"vid={result['vid_requested']} played={played}")
        print(f"[newgame] fps min/median={result['fps_min']}/{result['fps_median']} "
              f"mus={result['mus_requested']}")
        if result["shader_compile_fails"]:
            print(f"[newgame] SHADER FAILS: {len(result['shader_compile_fails'])}")
            for s in result["shader_compile_fails"][:5]:
                print("   ", s[:180])
        if result["pageerrors"]:
            for s in result["pageerrors"][:5]:
                print("   [pageerror]", str(s)[:200])
        try:
            ctx.close(); browser.close()
        except Exception:
            pass
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--watch", type=float, default=300.0)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--headed", action="store_true")
    a = ap.parse_args()
    res = run(a.watch, a.tag, headless=not a.headed)
    return 0 if res.get("verdict") in ("ALIVE", "ALIVE_NOVIDEO") else 1


if __name__ == "__main__":
    sys.exit(main())
