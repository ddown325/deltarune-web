# tests/SW_POISON_REPORT.md — the "fails to load 5/6, delete all data" wedge, root-caused and fixed (2026-11-11, v9.13)

**Player report:** "on some devices the game in general fails to load 5/6
files bug and try to fix that then sadly delete all data"

**Answer: the v9 asset-cache worker cached truncated downloads forever.**
GitHub Pages/Fastly intermittently closes a big download early; the browser
then sees a *clean-but-short* body. The page downloader has defended against
exactly that since v7.1/v7.2 (Range resume + exact-size verification against
`ARCHIVE_SIZES`), but v9 inserted the service worker *between* the page and
the network, and the worker's `cacheFirst` did `cache.put(key, clone)` with
**no completeness check**. One short 30 MB chapter part got stored under the
immutable key — permanently. From then on, on that device:

1. every boot served the truncated copy out of Cache Storage;
2. the page downloader saw `have < total`, did what it was built to do —
   resumed with `Range: bytes=<have>-`;
3. the worker served that from the cached truncated body, whose end is
   *before* `<have>` → `slice206` returned **416 Range Not Satisfiable**;
4. the page threw, retried (stage: `RETRYING DOWNLOAD 1/6` … `5/6`), and
   every retry hit the same poisoned entry → **DOWNLOAD FAILED**;
5. reloads re-hit the cache forever; `CACHE_VERSION` bumps only happen on
   deploys. The only escape players found was deleting all site data —
   which deletes the saves (`/_savedata`, IndexedDB) — "sadly delete all
   data". Worse, the site's own repair button ("Wipe all of your saves?")
   deleted IndexedDB but **not** Cache Storage: it killed the saves and left
   the actual poison in place.

A second, device-class failure: on browsers where Cache Storage is blocked
or throws (Safari private mode, storage-policy-locked browsers, quota edge
states), the worker's unguarded `caches.open()` rejected inside
`respondWith` — killing every request the worker handled. On those devices
the game could not load at all, even though plain network worked fine.

The same poisoned-entry class silently degraded two more paths:

| path | symptom with a poisoned entry |
|---|---|
| engine manifest XHRs (`mus/*.ogg`, `chapterN_windows/`, ~370 files/chapter) | the v8.1 short-body check fires, but all five XHR retries hit the same cached truncated copy → garbled/missing audio |
| `runnerN.js` (plain `<script src>`, zero verification/retry) | a truncated copy = permanent silent boot failure |

## The runtime evidence (real Chromium, real sw.js controlling)

New gate: `tests/sw_poison_test.py`. It warms the worker cache, then plants
the exact state the old worker left behind — a 40%-truncated
`chapter1.unx.part001` (5,229,150 of 13,072,876 bytes) under the immutable
key, `Content-Length` header promising the full size — then issues the page
downloader's own Range-resume request (what `fetchChunkRobust` sends on
retry: `<chunk>?v=7&r=N` with `Range: bytes=5229150-`).

| observation | v9.12 worker (negative control) | v9.13 worker |
|---|---|---|
| poisoned entry planted | `planted: 5229150` ✓ (probe confirmed in-cache) | same |
| Range resume `bytes=5229150-` | **`status 416`, `Content-Range: bytes */5229150`, 0 bytes** — the wedge | **`status 206`, `Content-Range: bytes 5229150-13072875/13072876`, 7,843,726 bytes** |
| cache entry after the probe | still 5,229,150 bytes (poison persists) | **13,072,876 bytes (self-healed, complete)** |
| fresh full fetch afterwards | 200 with the truncated body | 200 with the complete body |
| save after `DRClearAssetCache()` | n/a (helper is new) | `dr-assets-*` caches gone, `/_savedata/filech1_0` survives byte-for-byte |
| chapter-select boot, SW active, cache wiped mid-boot | — | main loop 0.6 s, zero pageerrors, chapter-select screen rendering (red SOUL / yellow selector palette confirmed in the screenshot) |

## The fix (layered)

`sw.js`:

1. **Completeness check at put time** — every fetched asset is buffered and
   its length compared against its own `Content-Length` header; only
   complete bodies are cached. Short bodies are still *served* (the page
   resumes them) but never stored.
2. **Self-heal** — a Range request whose start byte is at/after the end of a
   cached body (only possible for a truncated entry: the page ranges inside
   the known file size) deletes the entry, re-fetches from the network,
   verifies, re-caches, and serves the proper 206 slice. Poisoned devices
   heal on the next affected download.
3. **Storage-blocked fallback** — every `caches` access is guarded; if Cache
   Storage is unavailable the worker serves plain network (also in
   `networkFirst` and `activate`).
4. **`CACHE_VERSION` → `v9.13`** — the standing deploy-ritual purge: a
   guaranteed one-time clean slate for already-poisoned devices.

`index.html` + `onefile.html` (identical changes):

5. **Runner script hardened** — `runnerN.js` is prefetched through
   `fetchChunkRobust` (Range resume + exact-size verification against the
   extended `ARCHIVE_SIZES`, which now includes `runner0-5.js`) and booted
   from a blob URL, exactly like `.data`/`.wasm` already were. The emscripten
   glue explicitly handles `blob:` script URLs (`currentScript.src` check).
   A cache-busted direct-URL retry remains as fallback.
6. **XHR short-body heal** — when the v8.1 length check fires, the page
   drops that single URL from the `dr-assets-*` caches (same origin+pathname
   keying as the worker) before retrying.
7. **Save-safe recovery UI** —
   - the fail overlay gains **RESET GAME FILES (KEEPS SAVES)** next to
     RETRY: purges only `dr-assets-*` from Cache Storage and reloads;
   - the Save Manager's old single "Wipe all of your saves?" button is split
     into **"Reset game files? (keeps saves)"** (the safe repair load
     failures actually need) and **"Factory reset? (DELETES saves)"** (last
     resort — which now *also* purges the asset cache, so it finally
     repairs loading too).

## Why the saves were never the problem

Saves live in IndexedDB (`/_savedata`, emscripten IDBFS `FILE_DATA` store,
keyed `/_savedata/filechN_x`). Game files live in Cache Storage
(`dr-assets-*`). The two never touch: purging the caches cannot lose a save,
and deleting IndexedDB never fixed a poisoned cache entry. v9.13 gives every
load-failure recovery path the cache-only purge, so nobody has to "sadly
delete all data" again.

## Repro / gate

```
python3 tests/sw_poison_test.py          # PASS = self-heal + save-safe reset + SW-allowed boot
```

Phases: (C) static gates on the fix markers in `sw.js` / `index.html` /
`onefile.html`; (A) poison + Range-resume probe + heal verification; (B)
save-safe reset; (D) full SW-allowed chapter-select boot after a mid-boot
cache wipe. Negative control: run with the v9.12 `sw.js` restored — the
Range probe answers 416 and the verdict goes FAIL (`self-heal=False`),
reproducing the reported wedge end-to-end.
