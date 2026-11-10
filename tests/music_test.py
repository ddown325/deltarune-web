"""music_test.py - audio-probe harness for the v9.12 music-handoff fix.

Boots ch5 (or any chapter) with a forged save and a document-start WebAudio
probe (AudioBufferSourceNode.start hook + per-buffer-duration histograms,
dumped every 15s). The GM web runner plays every sound as ~10-45ms chunk
nodes (~25 nodes/s per audible stream), so node-creation rate is a direct
hearable-music-activity meter. Used to root-cause the v9.12 stacked-music
bug (see MUSIC_FIX_REPORT.md) and to gate the fix.

Usage:
  python3 tests/music_test.py --room 50124 --watch 150 --tag mytag [--hm full] [--sw]
"""
from __future__ import annotations
import base64, json, sys, time
from pathlib import Path

E2E = Path(__file__).resolve().parent
sys.path.insert(0, str(E2E))
from playwright.sync_api import sync_playwright
from boot_harness import (ensure_server, probe_webgl, make_game_context,
                          BootLog, hook_page, frames_now, analyze_png)
import saveforge

# scr_get_room_list {save_id, room_ordinal} struct byte layout (validated:
# the previous session's 50114->119 patch used exactly this): 
#   02 c0 <id32> 00 00 | 00 00 | 52 07 | <room_lo> 00
# room ordinal low byte sits at push.i_site + 10. Two clusters hold the
# struct for 50124 (both patched for safety). Active only with --redirect.
ROOMLIST_REDIRECT_SITES = {
    11148064: 121,   # {50124, 122} site A room byte -> 121 (ralseicupboard)
    11153296: 121,   # {50124, 122} site B room byte -> 121
}

BASE_URL = "http://127.0.0.1:8901"

AUDIO_PROBE_JS = r"""
(() => {
  const t0 = performance.now();
  const log = (ev, info) => { try { console.log('[audioprobe] ' + ev + ' | ' + info + ' | t=' + ((performance.now()-t0)/1000).toFixed(2)); } catch(e){} };
  window.__drAud = { nodes: 0, byDur: {} };
  try {
    const os = AudioBufferSourceNode.prototype.start;
    if (os && !os.__drAud) {
      AudioBufferSourceNode.prototype.start = function (when, off, dur) {
        window.__drAud.nodes++;
        let bd = -1, bl = -1;
        try { if (this.buffer) { bd = +this.buffer.duration.toFixed(3); bl = this.buffer.length; } } catch (e) {}
        const key = bd + '/' + bl;
        window.__drAud.byDur[key] = (window.__drAud.byDur[key] || 0) + 1;
        if (bd > 0.5 || !window.__drAud.byDur.__bigLogged) {
          // log long buffers individually (whole-track buffers identify tracks);
          // short chunk buffers get logged once as a summary cadence sample
          if (bd > 0.5) { log('webaudio.start', 'BUF dur=' + bd + ' len=' + bl + ' off=' + (off||0) + ' dur=' + (dur===undefined?'inf':dur) + ' rate=' + (this.playbackRate?this.playbackRate.value:'?')); }
        }
        return os.call(this, when, off, dur);
      };
      os.__drAud = true;
    }
  } catch (e) {}
  try {
    // dump the per-duration histogram every 15s
    setInterval(() => {
      const h = window.__drAud.byDur;
      const parts = Object.keys(h).filter(k => k !== '__bigLogged').map(k => k + 'x' + h[k]);
      log('hist', 'nodes=' + window.__drAud.nodes + ' [' + parts.slice(0, 12).join(', ') + ']');
    }, 15000);
  } catch (e) {}
  try {
    const op = HTMLMediaElement.prototype.play;
    if (op && !op.__drAud) {
      HTMLMediaElement.prototype.play = function () {
        let src = '';
        try { src = (this.currentSrc || this.src || '').split('?')[0].split('/').pop(); } catch (e) {}
        log('media.play', src + (this.loop ? ' [loop]' : ''));
        return op.call(this);
      };
      op.__drAud = true;
    }
  } catch (e) {}
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


def run(room: int, watch_s: float, tag: str, hm: str = "full", hard: bool = True,
        walk_plan: str | None = "R30,U10,D5,L10,Z5,R25", boot_timeout: float = 240.0,
        sw: bool = False, plot: int | None = None, redirect: bool = False):
    print(f"[musicrepro] room={room} watch={watch_s}s hm={hm} hard={hard} plan={walk_plan} sw={sw} plot={plot}")
    payload = saveforge.craft(5, room, hp=90, maxhp=90,
                              base=saveforge.SAVES / "filech4_0")
    if plot is not None:
        lines = saveforge.load_save(saveforge.SAVES / "filech4_0")
        lines = saveforge.set_room(lines, room)
        lines[-3] = saveforge.num(plot)
        payload = saveforge.save_bytes(lines)
    files = [("filech5_0", base64.b64encode(payload).decode())]
    inject = IDB_INJECT_TMPL.replace("%FILES%", json.dumps(files))

    if not ensure_server():
        return {"verdict": "FAIL", "reason": "cannot serve repo"}

    blog = BootLog()
    result = {"room": room, "tag": tag}
    shot_dir = E2E / "shots"
    shot_dir.mkdir(exist_ok=True)

    with sync_playwright() as pw:
        browser, gl_label, gl_args, _ = probe_webgl(pw, headless=True)
        result["webgl"] = gl_label
        if sw:
            # service workers ALLOWED (the real user path: sw.js caches assets
            # incl. mus/*.ogg and serves them — including Range/206)
            context = browser.new_context(
                viewport={"width": 1280, "height": 720},
            )
            init = ""
            if hard:
                init += "\ntry { localStorage.setItem('dr_webport_hardmode', '1'); } catch (e) {}"
            init += "\n" + AUDIO_PROBE_JS + "\n" + inject
            context.add_init_script(init)
            ctx = context
        else:
            ctx = make_game_context(browser, hard_mode=(True if hard else None),
                                     extra_init_js=AUDIO_PROBE_JS + "\n" + inject)
        page = ctx.new_page()

        if redirect:
            def _patch_part1(route):
                resp = route.fetch()
                body = bytearray(resp.body())
                for off, val in ROOMLIST_REDIRECT_SITES.items():
                    if len(body) > off:
                        print(f"[musicrepro] interceptor: roomlist byte @{off} "
                              f"{body[off]} -> {val} (50124 now loads room 121)")
                        body[off] = val
                route.fulfill(status=resp.status, headers=dict(resp.headers),
                              body=bytes(body))
            page.route("**/chapter5.unx.part001*", _patch_part1)
        tref = [time.time()]
        hook_page(page, blog, tref, verbose=False,
                  extra_milestones=[("save_injected", "[idb-inject] wrote"),
                                    ("file_select", "mus/menu.ogg"),
                                    ("room_music", "GML_async_wget2_data called for mus/")])
        url = f"{BASE_URL}/index.html?c=5&hm={hm}"
        print(f"[musicrepro] goto {url}")
        page.goto(url, timeout=90_000, wait_until="domcontentloaded")
        tref[0] = time.time()

        # boot to main loop
        deadline = time.time() + boot_timeout
        while time.time() < deadline:
            if blog.milestones.get("MAIN_LOOP") is not None or blog.crashed:
                break
            page.wait_for_timeout(500)
        result["main_loop_s"] = blog.milestones.get("MAIN_LOOP")

        # wait for file select
        t_sel = time.time() + 200
        while time.time() < t_sel:
            if blog.crashed:
                break
            if blog.milestones.get("file_select") is not None:
                page.wait_for_timeout(2500)
                break
            page.wait_for_timeout(500)
        result["file_select_s"] = blog.milestones.get("file_select")

        # load the save
        if not blog.crashed:
            page.mouse.click(640, 360)
            page.wait_for_timeout(1200)
            for _ in range(6):
                if blog.crashed:
                    break
                page.keyboard.press("z")
                page.wait_for_timeout(1800)
            page.wait_for_timeout(4000)

        # walk plan + watch
        DIRK = {"L": "ArrowLeft", "R": "ArrowRight", "U": "ArrowUp", "D": "ArrowDown", "Z": "Z"}
        plan = [(DIRK[p[0]], float(p[1:])) for p in (walk_plan or "").split(",") if p] or None
        t_watch0 = time.time()
        i_in = 0
        last_shot = [time.time()]
        while time.time() - t_watch0 < watch_s:
            if blog.crashed:
                break
            if plan:
                k, hold = plan[i_in % len(plan)]
                if k == "Z":
                    t_end = time.time() + min(hold, max(0.1, watch_s - (time.time() - t_watch0)))
                    while time.time() < t_end and not blog.crashed:
                        page.keyboard.press("z")
                        page.wait_for_timeout(800)
                else:
                    page.keyboard.down(k)
                    page.wait_for_timeout(min(hold, max(0.1, watch_s - (time.time() - t_watch0))) * 1000)
                    page.keyboard.up(k)
                    page.wait_for_timeout(300)
            else:
                page.wait_for_timeout(2500)
            i_in += 1
            if time.time() - last_shot[0] >= 10:
                p = shot_dir / f"mrep_{tag}_{int(time.time()-t_watch0):03d}s.png"
                page.screenshot(path=str(p))
                last_shot[0] = time.time()

        result["crashed"] = blog.crashed
        result["crash_t"] = blog.crash_t
        result["pageerrors"] = blog.pageerrors[:20]
        result["fps_last"] = None
        f = frames_now(page)
        page.wait_for_timeout(2000)
        f2 = frames_now(page)
        if f is not None and f2 is not None and f2 > f:
            result["fps_last"] = round((f2 - f) / 2.0, 1)

        # music + audio events, ordered
        mus = [(t, txt.split("for ")[-1]) for (t, _, txt) in blog.events
               if "GML_async_wget2_data called for mus/" in txt]
        auds = [(t, txt) for (t, _, txt) in blog.events if "[audioprobe]" in txt]
        result["mus_requests"] = [(round(t, 2), m) for t, m in mus]
        result["audio_events"] = [(round(t, 2), txt) for t, txt in auds]
        result["gml_errors"] = [t for (_, _, t) in blog.events if "ERROR" in t][:20]
        result["milestones_s"] = {k: round(v, 1) for k, v in
                                  sorted(blog.milestones.items(), key=lambda kv: kv[1])}
        verdict = "CRASH" if blog.crashed else ("PAGEERROR" if blog.pageerrors else "ALIVE")
        result["verdict"] = verdict

        logp = E2E / "logs" / f"musicrepro_{tag}.log"
        logp.parent.mkdir(exist_ok=True)
        blog.dump(logp, [f"musicrepro room={room} tag={tag} hm={hm}"], result)
        print(f"\n[musicrepro] VERDICT: {verdict} crashed={blog.crashed} fps={result['fps_last']}")
        print(f"[musicrepro] milestones: {result['milestones_s']}")
        print(f"[musicrepro] mus requests ({len(mus)}):")
        for t, m in result["mus_requests"]:
            print(f"    {t:8.2f}s  {m}")
        print(f"[musicrepro] audio probe events ({len(auds)}):")
        for t, txt in result["audio_events"][:40]:
            print(f"    {t:8.2f}s  {txt[:130]}")
        if result["gml_errors"]:
            print("[musicrepro] GML errors:")
            for s in result["gml_errors"][:8]:
                print("    ", s[:200])
        if result["pageerrors"]:
            print("[musicrepro] pageerrors:", result["pageerrors"][:5])
        try:
            ctx.close(); browser.close()
        except Exception:
            pass
    return result


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--room", type=int, default=50124)
    ap.add_argument("--watch", type=float, default=150.0)
    ap.add_argument("--tag", default="repro1")
    ap.add_argument("--hm", default="full")
    ap.add_argument("--no-hard", action="store_true")
    ap.add_argument("--plan", default="R30,U10,D5,L10,Z5,R25")
    ap.add_argument("--sw", action="store_true",
                    help="allow the service worker (real user path)")
    ap.add_argument("--plot", type=int, default=None,
                    help="override the save's plot line ([-3])")
    ap.add_argument("--redirect", action="store_true",
                    help="redirect save 50124 to room 121 (ralseicupboard)")
    a = ap.parse_args()
    r = run(a.room, a.watch, a.tag, hm=a.hm, hard=not a.no_hard, walk_plan=a.plan,
            sw=a.sw, plot=a.plot, redirect=a.redirect)
    sys.exit(0 if r.get("verdict") == "ALIVE" else 1)
