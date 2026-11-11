#!/usr/bin/env python3
"""sw_poison_test.py — the v9.13 poisoned-cache runtime gate.

Player report: "on some devices the game in general fails to load
[RETRYING DOWNLOAD] 5/6 ... then [you have to] sadly delete all data".

Root cause (v9 worker): a big download that the CDN closes early leaves a
"clean but short" body. The worker cached it anyway - permanently - under the
immutable key. Every later boot served the truncated copy; the page
downloader's Range resume then asked for bytes past the cached body's end
(slice206 -> 416), all six retry rounds hit the same poisoned entry, and the
game died at "RETRYING DOWNLOAD 5/6 -> DOWNLOAD FAILED" until the player
deleted ALL site data - which also deleted the saves (/_savedata in
IndexedDB), while NOT even clearing the Cache Storage entry that was actually
broken.

This gate reproduces exactly that state in a real Chromium with the real
sw.js controlling, and verifies the v9.13 fixes:

  A. POISON + SELF-HEAL: plant a truncated chapter1.unx.part001 in the
     dr-assets cache (exactly what the old worker stored), then issue the
     same Range-resume fetch the page downloader uses on retry. The old
     worker answered 416 forever; v9.13 must answer 206 with the correct
     Content-Range, delete the poisoned entry, re-fetch + verify + re-cache
     the complete file, and serve subsequent loads the full body.
  B. SAVE-SAFE RESET: with a save present in /_savedata (IndexedDB),
     window.DRClearAssetCache() must wipe the dr-assets caches while the
     save survives byte-for-byte (the in-page escape hatch that used to
     require deleting all site data).
  C. STATIC GATES: the completeness check, storage-blocked fallbacks and
     runner-script hardening must be present in the served sources (future
     edits must not silently regress them).

No game boot / WebGL needed: the loader page in chapter-select mode only
pulls game.unx.part001 (2.9 MB) and never touches chapter1.unx.part001, so
the poisoned entry is the only one that matters.

Usage: python3 tests/sw_poison_test.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

E2E = Path(__file__).resolve().parent
sys.path.insert(0, str(E2E))
from playwright.sync_api import sync_playwright         # noqa: E402
from boot_harness import ensure_server, BootLog, hook_page, probe_webgl  # noqa: E402

BASE_URL = "http://127.0.0.1:8901"
CHAPTER1_PART1 = "chapter1.unx.part001"
FULL_SIZE = 13_072_876          # repo ground truth (ARCHIVE_SIZES)
TRUNCATED = FULL_SIZE * 40 // 100

# --------------------------------------------------------------------------- #
# browser-side helpers (run inside the page)
# --------------------------------------------------------------------------- #

WAIT_FOR_CONTROLLER = """
async (timeoutMs) => {
  const t0 = Date.now();
  while (Date.now() - t0 < timeoutMs) {
    if (navigator.serviceWorker.controller) return navigator.serviceWorker.controller.scriptURL;
    await new Promise(r => setTimeout(r, 200));
  }
  return null;
}
"""

# Warm the SW cache first (a small immutable file through the worker) so
# the dr-assets cache exists, then plant the exact state the old worker left
# behind: a SHORT body under the immutable key (Content-Length header says
# the full size, body is 40%). The pre-probe read-back proves the poison is
# really in place (otherwise the heal probe would be a vacuous cache miss).
POISON_JS = """
async ([name, warmup, truncLen, fullLen]) => {
  const w = await fetch(warmup + '?v=poisonwarm');
  if (!w.ok) return { error: 'warmup fetch failed: ' + w.status };
  const keys = await caches.keys();
  const cname = keys.find(k => /^dr-assets-/.test(k));
  if (!cname) return { error: 'no dr-assets cache after warmup', keys };
  const cache = await caches.open(cname);
  const key = location.origin + '/' + name;
  await cache.delete(key);
  const body = new Uint8Array(truncLen);
  for (let i = 0; i < truncLen; i++) body[i] = (i * 7) & 0xff;
  await cache.put(key, new Response(body, {
    status: 200,
    headers: {
      'Content-Type': 'application/octet-stream',
      'Content-Length': String(fullLen),
    },
  }));
  const hit = await cache.match(key);
  const plantedLen = hit ? (await hit.arrayBuffer()).byteLength : -1;
  return { cache: cname, planted: plantedLen, expected: truncLen, key: key,
           poisoned: plantedLen === truncLen };
}
"""

# What the page downloader does on retry: ranged fetch past the truncated
# body (the loader fetches <chunk>?v=7&r=N with a Range header).
RANGE_FETCH_JS = """
async ([name, start]) => {
  const r = await fetch(name + '?v=7&r=99', {
    headers: { 'Range': 'bytes=' + start + '-' },
  });
  const ab = await r.arrayBuffer();
  return {
    status: r.status,
    contentRange: r.headers.get('content-range'),
    acceptRanges: r.headers.get('accept-ranges'),
    byteLength: ab.byteLength,
  };
}
"""

FULL_FETCH_JS = """
async (name) => {
  const r = await fetch(name + '?v=7&r=100', {});
  const ab = await r.arrayBuffer();
  return { status: r.status, byteLength: ab.byteLength };
}
"""

CACHE_ENTRY_SIZE_JS = """
async (name) => {
  const keys = await caches.keys();
  const cname = keys.find(k => /^dr-assets-/.test(k));
  if (!cname) return { error: 'no dr-assets cache' };
  const cache = await caches.open(cname);
  const hit = await cache.match(location.origin + '/' + name);
  if (!hit) return { present: false };
  return { present: true, byteLength: (await hit.arrayBuffer()).byteLength };
}
"""

INJECT_SAVE_JS = """
async ([name, b64]) => {
  const bin = atob(b64);
  const u8 = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) u8[i] = bin.charCodeAt(i);
  const db = await new Promise((res, rej) => {
    const r = indexedDB.open('/_savedata', 21);
    r.onupgradeneeded = () => { if (!r.result.objectStoreNames.contains('FILE_DATA')) r.result.createObjectStore('FILE_DATA'); };
    r.onsuccess = () => res(r.result);
    r.onerror = () => rej(r.error);
  });
  await new Promise((res, rej) => {
    const tx = db.transaction(['FILE_DATA'], 'readwrite');
    tx.objectStore('FILE_DATA').put({timestamp: new Date(), mode: 33206, contents: u8}, '/_savedata/' + name);
    tx.oncomplete = res;
    tx.onerror = () => rej(tx.error);
  });
  db.close();
  return { wrote: u8.byteLength };
}
"""

READ_SAVE_JS = """
async (name) => {
  const db = await new Promise((res, rej) => {
    const r = indexedDB.open('/_savedata', 21);
    r.onsuccess = () => res(r.result);
    r.onerror = () => rej(r.error);
  });
  const rec = await new Promise((res, rej) => {
    const tx = db.transaction(['FILE_DATA'], 'readonly');
    const rq = tx.objectStore('FILE_DATA').get('/_savedata/' + name);
    rq.onsuccess = () => res(rq.result);
    rq.onerror = () => rej(rq.error);
  });
  db.close();
  if (!rec) return { present: false };
  const u8 = new Uint8Array(rec.contents || []);
  let hex = '';
  for (let i = 0; i < u8.length; i++) hex += u8[i].toString(16).padStart(2, '0');
  return { present: true, byteLength: u8.length, hex };
}
"""

DRCLEAR_JS = """
async () => { await window.DRClearAssetCache(); return true; }
"""

CACHE_KEYS_JS = """
async () => (await caches.keys()).filter(k => /^dr-assets-/.test(k))
"""

# --------------------------------------------------------------------------- #

def static_gates() -> list[str]:
    """Phase C: the fixes must exist in the served sources."""
    fails = []
    sw = (E2E.parent / "sw.js").read_text(encoding="utf-8")
    idx = (E2E.parent / "index.html").read_text(encoding="utf-8")
    one = (E2E.parent / "onefile.html").read_text(encoding="utf-8")

    def need(src: str, needle: str, label: str):
        if needle not in src:
            fails.append(label)

    need(sw, "byteLength === expected", "sw.js: completeness check before cache.put")
    need(sw, "await cache.delete(key)", "sw.js: poisoned-entry self-heal")
    need(sw, "try { cache = await caches.open(CACHE); } catch", "sw.js: storage-blocked fallback")
    need(sw, "CACHE_VERSION = 'v9.13'", "sw.js: CACHE_VERSION bump")

    for name, src in (("index.html", idx), ("onefile.html", one)):
        need(src, 'id="drReset"', f"{name}: reset button in the fail overlay")
        need(src, "DRClearAssetCache", f"{name}: save-safe cache purge helper")
        need(src, "DRPurgeCachedAsset", f"{name}: single-entry purge helper")
        need(src, "runnerScriptReady", f"{name}: robust runner-script prefetch")
        need(src, '"runner0.js": 157078', f"{name}: runner script sizes verified")
        need(src, "toggleclear_asset_cache", f"{name}: save-safe save-manager button")
        need(src, "factory_reset_all_data", f"{name}: labeled factory reset")
        assert "toggleclear_site_cache" not in src, f"{name}: old save-wipe button still wired"
    return fails


def run() -> dict:
    print("[sw_poison] phase C: static gates on the served sources")
    static_fails = static_gates()
    for f in static_fails:
        print(f"   [static-FAIL] {f}")
    if static_fails:
        return {"verdict": "FAIL", "reason": "static gates failed", "fails": static_fails}
    print("   [static-ok] all v9.13 markers present in sw.js / index.html / onefile.html")

    if not ensure_server():
        return {"verdict": "FAIL", "reason": "cannot serve repo"}

    blog = BootLog()
    result = {"phases": {}}
    save_name = "filech1_0"
    save_payload = bytes(range(256)) * 17 + b"~SW-POISON-SURVIVAL~"   # 4380 bytes, non-trivial

    with sync_playwright() as pw:
        browser, gl_label, _, _ = probe_webgl(pw, headless=True)
        ctx = browser.new_context(service_workers="allow",
                                  viewport={"width": 1280, "height": 720})
        page = ctx.new_page()
        hook_page(page, blog, tref := [time.time()], verbose=False)
        t0 = time.time()

        # ---- load the chapter-select page (light: only game.unx.part001) ----
        print("[sw_poison] phase A: loading page (chapter select) and waiting for the SW to control it")
        page.goto(f"{BASE_URL}/index.html", timeout=60_000, wait_until="domcontentloaded")
        tref[0] = time.time()
        controller = page.evaluate(WAIT_FOR_CONTROLLER, 20_000)
        if not controller:
            result["verdict"] = "FAIL"
            result["reason"] = "service worker never controlled the page"
            ctx.close(); browser.close()
            return result
        print(f"   [sw-ok] controller active: {controller}")
        result["sw_controller"] = controller

        # ---- A1: warm the cache, then plant the poison ----
        planted = page.evaluate(POISON_JS, [CHAPTER1_PART1, "favicon.ico", TRUNCATED, FULL_SIZE])
        print(f"   [poison] planted: {planted}")
        result["phases"]["poison_planted"] = planted
        if not planted.get("poisoned"):
            result["verdict"] = "FAIL"
            result["reason"] = "could not plant a poisoned cache entry"
            ctx.close(); browser.close()
            return result

        # ---- A2: the failing request from the bug report ----
        # old worker: 416 forever -> "RETRYING DOWNLOAD 5/6" -> DOWNLOAD FAILED
        ranged = page.evaluate(RANGE_FETCH_JS, [CHAPTER1_PART1, TRUNCATED])
        print(f"   [range] {ranged}")
        result["phases"]["range_resume"] = ranged
        a2_ok = (ranged.get("status") == 206
                 and ranged.get("byteLength") == FULL_SIZE - TRUNCATED
                 and (ranged.get("contentRange") or "").endswith(f"/{FULL_SIZE}"))

        # ---- A3: the entry in the cache must now be complete ----
        entry = page.evaluate(CACHE_ENTRY_SIZE_JS, CHAPTER1_PART1)
        print(f"   [cache] {entry}")
        result["phases"]["cache_entry_after_heal"] = entry
        a3_ok = entry.get("present") and entry.get("byteLength") == FULL_SIZE

        # ---- A4: a fresh full load gets the complete file ----
        full = page.evaluate(FULL_FETCH_JS, CHAPTER1_PART1)
        print(f"   [full] {full}")
        result["phases"]["full_fetch"] = full
        a4_ok = full.get("status") == 200 and full.get("byteLength") == FULL_SIZE

        result["phases"]["A_self_heal"] = bool(a2_ok and a3_ok and a4_ok)

        # ---- phase B: the save-safe reset ----
        print("[sw_poison] phase B: save-safe asset-cache reset")
        inj = page.evaluate(INJECT_SAVE_JS, [save_name, __import__("base64").b64encode(save_payload).decode()])
        print(f"   [save] injected: {inj}")
        cleared = page.evaluate(DRCLEAR_JS)
        keys_after = page.evaluate(CACHE_KEYS_JS)
        back = page.evaluate(READ_SAVE_JS, save_name)
        print(f"   [reset] cleared={cleared} dr-assets caches left={keys_after}")
        print(f"   [save] read-back: present={back.get('present')} bytes={back.get('byteLength')}")
        result["phases"]["B_save_safe_reset"] = {
            "caches_left": keys_after,
            "save_present": back.get("present"),
            "save_bytes": back.get("byteLength"),
            "save_intact": (back.get("present") and back.get("byteLength") == len(save_payload)
                            and bytes.fromhex(back["hex"]) == save_payload),
        }

        # ---- phase D: full chapter-select boot WITH the SW active, with the
        # cache wiped mid-boot (phase B) - the loader must still reach the
        # main loop through the new verified-cache path + runner-script
        # prefetch. This is the end-to-end "the game still loads" proof.
        print("[sw_poison] phase D: SW-allowed full boot (chapter select) after the mid-boot cache wipe")
        deadline = time.time() + 150
        while time.time() < deadline:
            if blog.milestones.get("MAIN_LOOP") is not None or blog.crashed:
                break
            page.wait_for_timeout(500)
        boot_ok = blog.milestones.get("MAIN_LOOP") is not None and not blog.crashed
        result["phases"]["D_full_boot"] = {
            "boot_ok": bool(boot_ok),
            "crashed": blog.crashed,
            "pageerrors": [str(e) for (_, e) in blog.pageerrors[:10]],
            "milestones_s": {k: round(v, 1) for k, v in sorted(blog.milestones.items(), key=lambda kv: kv[1])},
        }
        print(f"   [boot] ok={boot_ok} crashed={blog.crashed}")
        print(f"   [boot] milestones: {result['phases']['D_full_boot']['milestones_s']}")
        for (_, e) in blog.pageerrors[:5]:
            print(f"   [pageerror] {str(e)[:200]}")

        page.screenshot(path=str(E2E / "shots" / "swpoison_final.png"))
        ctx.close()
        browser.close()
        result["elapsed_s"] = round(time.time() - t0, 1)

    a_ok = result["phases"].get("A_self_heal") is True
    b_ok = (result["phases"]["B_save_safe_reset"]["caches_left"] == []
            and result["phases"]["B_save_safe_reset"]["save_intact"] is True)
    d_ok = result["phases"]["D_full_boot"]["boot_ok"] is True
    result["verdict"] = "PASS" if (a_ok and b_ok and d_ok) else "FAIL"
    result["reason"] = ("self-heal=" + str(a_ok) + " save-safe-reset=" + str(b_ok)
                         + " sw-boot=" + str(d_ok))

    logp = E2E / "logs" / "sw_poison_test.log"
    logp.parent.mkdir(exist_ok=True)
    blog.dump(logp, ["sw_poison_test — v9.13 poisoned-cache runtime gate"], result)
    print(f"\n[sw_poison] VERDICT: {result['verdict']}  ({result['reason']})  elapsed={result['elapsed_s']}s")
    return result


def main() -> int:
    res = run()
    return 0 if res.get("verdict") == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
