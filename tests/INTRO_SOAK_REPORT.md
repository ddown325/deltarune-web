# tests/INTRO_SOAK_REPORT.md — the v9.10 intro & title screen, verified end-to-end (2026-11-09)

**Verdict: on the deployed v9.10 bytes the ch5 intro is clean at every layer
that was ever broken — boot/logo, title idle, fresh-player file creation, the
opening cutscene, and the live github.io deployment with the real service
worker. No crash mechanism remains that this harness can provoke. Nothing
left to fix.**

This closes the loop on the user report that v9.8/v9.9 couldn't satisfy:
"some lag on the intro for the chapter and sometimes the game crashes".
v9.10 (stub pull) + v9.9 (runner revert) were the fixes; this session added
the three runtime gates that had never been run on the final bytes:

| gate (this session) | result |
|---|---|
| **420 s title/file-select idle soak** (`soak_test.py`, clean IndexedDB) | **PASS.** main loop 5.9 s, logo music 9.1 s, `mus/menu.ogg` 20.0 s; idle fps steady 24-28 (software GL); **wasm heap growth 0.0 MB** (244.4 MB flat for the whole soak), JS heap flat (435 MB); total console output for the entire run: 124 lines — **no save-poll storm** (all 33 `_GetSaveFileName` scans happen at boot, 2-3 legitimate passes over the 6 slots + `dr.ini` + `options.ini`); no pageerror, no crash. The v9.5-era "tab OOM-dies at ~143 s of title-idle" is confirmed dead — that behavior belonged to the stub-carrying builds, exactly as the v9.10 note suspected ("the storm is the stubs' side effect"). |
| **Fresh-player intro path** (`newgame_test.py`, clean IndexedDB, real input walk) | **PASS to gameplay.** file select 19.1 s → Z on empty slot 1 → "Start" → name entry → name "A" confirmed → ch5 opening cutscene: 59-60 fps, `snd_dtrans_lw.ogg` loads clean (82 KB, fetched from `chapter5_windows/`), bedroom scene → Susie appears → player control. **Zero shader-compile failures** (document-start WebGL probe on every compile/link), zero pageerrors, no crash. The opening is NOT video-driven: no `vid/` request, no `<video>` element, all in-engine. |
| **Live-site probe** (`live_boot_test.py`, github.io, **service worker allowed**) | **PASS.** SW registered and `controller`-active on every sample; main loop 8.2 s, logo music 11.5 s, `mus/menu.ogg` 22.4 s (166 MB chapter data over the real network); 150 s title idle at 26-28 fps, wasm flat, zero request failures, zero pageerrors, no crash. The v9.10 `CACHE_VERSION` purge path is healthy. |

Plus the prior-session gates that still hold on these bytes:
`tests/FLOWERS_REPORT.md` (Field of Hopes, 200 s, save room 50124) and the
`hazard_test.py` chapter-4 damage gate.

## The intro-video question (the last untested path)

`vid/ch5_intro_en.mp4` (+ `_jp`) IS referenced by the ch5 data — a cutscene
controller builds `video_file = "vid/" + ch5_intro_en + ".mp4"` with
`mus/ch5_intro_audio.ogg` (present in `mus/`), `skip_timer`/`skip_video`
handling and the `videochromasampler` shader (chapter5.unx.part001,
string-window around offset 23,568,000). It plays deep in ch5's story — past
the light-world opening this probe can walk to. `tennaIntroF1_compressed_28.mp4`
is not referenced by that name in any data file (dynamically built name or
unused). Runtime-verifying the mp4 playback itself would need either
story-driven gameplay automation or a plot-forged save at the trigger flag.

Risk assessment for leaving it untested: v9.9 made all six `runnerN.js`
byte-identical to the first build, whose video intro the user confirmed
worked; the video controller is chapter data, also byte-identical to the
first build; the only rewrite in the path is `SHADER_LOOP_FIXES`
(content-addressed GLSL loop bounds, zero compile failures observed across
every room and cutscene probed, including the shader-heavy logo screen).
Nothing in the deployed bytes differs from the first build along the video
path except those verified-clean shader rewrites.

## Mechanics learned (all runtime-confirmed)

1. **The fresh-file input map** (for future probes): file select → Z on empty
   slot 1 reveals "Start"/"Back" (heart on Start) → Z → "ENTER YOUR OWN NAME."
   grid (rows A-J / K-T / U-Z BACK END, cursor starts on 'A') → navigate to
   END, Z → "THIS IS YOUR NAME." NO/YES where **the heart lands CENTERED —
   no default selection; Z alone is a no-op. ArrowRight onto YES, then Z.**
   (This screen is why naive Z-spam probes stall here.)
2. **`chapter5_windows/` must be in any served mirror.** The opening
   cutscene fetches `snd_dtrans_lw.ogg` by bare name; the runner resolves
   bare sound names against `chapter5_windows/` (32 loose files: transition
   sounds, `audiogroup1.dat`, two Windows DLLs, `lang/lang_ja.json`). A
   missing file triggers a ~0.4 s retry loop — tested accidentally for 125 s:
   the tab held 58-60 fps, no crash, but the story beat never resolves.
   The live site serves the directory fine (spot-checked 200s).
3. `mus/menu.ogg` marks the file select; `snd_dtrans_lw.ogg` (bare name,
   `chapter5_windows/`) marks the opening-cutscene start; the logo music is
   `mus/deltarune_logo_ch5_itoki.ogg` at ~9-11 s.
4. `Prepare cache error Error: unable to cache, API not found` (one line,
   ~4-7 s) is the runner's own cache-prep fallback — present in every build
   including SW-blocked local runs. Harmless.
5. The title screen with no saves shows 3 `[EMPTY]` slots plus "Ch 4 Files"
   (import previous-chapter data) — a fresh ch5 file does NOT require ch4
   data; the empty slot creates one directly.

## The bar going forward

Any future change to ch5 chapter data, the runners, or the loader ships only
after: `soak_test.py --soak 300` ALIVE, `newgame_test.py --watch 240`
reaching player control, `flower_test.py --room 50124 --watch 120` ALIVE, the
`hazard_test.py` chapter-4 gate, and `live_boot_test.py --idle 120` ALIVE
after deploy.
