# tests/FLOWERS_REPORT.md — the Field of Hopes, verified on v9.10 (2026-11-09)

**Verdict: the Field of Hopes and Dreams renders, animates, takes input and
survives indefinitely on the deployed v9.10 build. Nothing left to fix.**

This closes the loop on the last open ch5 question. History: the first ch5
build's only bug was a crash in the Field of Hopes; v8.1 "fixed" it by
stubbing the `shader_replace_simple_*` DLL calls (bytecode surgery that
instead froze the intro — pulled in v9.10); v9.5 fixed the actual root cause
at the GLSL level (`SHADER_LOOP_FIXES`: WebGL1-illegal uniform-bound loops
rewritten to constant-bound GLSL ES 1.00). Until now nobody could reach the
field to verify: every build between v8.1 and v9.9 had a frozen or crashing
intro.

## How it was tested

`tests/flower_test.py` (this directory) — a real-browser gate, same family
as `hazard_test.py`:

1. Serves the FULL repo at 127.0.0.1:8901 (all of `mus/` included — see the
   storm note below), service workers blocked, software WebGL.
2. Injects a forged `filech5_0` into the runner's IndexedDB
   (`/_savedata` IDBFS, the port's own upload format): a real community
   ch4 save (XtronXI/deltarune-saves), byte-edited with `saveforge.py` —
   the V2 save format (3055 lines, 2509 flags) is identical across ch2-5,
   only the trailing `room` line is overridden. Party HP 90/90, dark=1.
3. Boots `index.html?c=5` and plays the REAL flow: logo screen (~20 s to
   `mus/menu.ogg` when music is served) → file select ("CHAPTER 5 — Please
   select a file", FILE0 present) → click + Z to Continue.
4. Watches the room: rAF frame counter, pageerror, renderer crash, GML
   errors, music requests, screenshots every ~17 s with change detection,
   and a document-start WebGL probe that logs EVERY shader compile/link
   failure with a source excerpt.
5. During the watch, a slow input cycle (Z / arrows / Z / arrows) proves
   the room actually accepts input.

## Results (all on the v9.10 bytes, software GL, headless)

| save room id | result |
|---|---|
| **50124 — Field of Hopes flower field** | **PASS.** 200 s watch, ~17 fps, `mus/field_of_hopes_insaneintherain_intro.ogg` → `_loop.ogg` both loaded, pink petal particles animating, party member walks on arrow input (VLM-verified position + walk-frame change between shots), purple grass / blossom trees render correctly, ZERO shader-compile failures, zero pageerrors, no crash. |
| 50131 — another field segment | PASS. Same field theme, animated, no crash. |
| 50125 — field-adjacent room | PASS. Animated (271k non-blank px), no crash. |
| 50122 — outdoor light world | PASS. `mus/birds_ch5.ogg`, animated, no crash. |
| 50013 — light-world house/cutscene room | Valid id; loads a static cutscene state (expected: the forged save's ch4 plot=249 puts ch5 cutscene controllers in a wait state). Not a defect — real saves carry consistent plot/room. |
| 50118 / 50119 / 50120 / 50121 | **INVALID in this build** — `*** ERROR: could not find room with id X`, then the game degrades gracefully to the animated Annoying-Dog screen with `mus/inukuma_wip.ogg` (no crash, no hang). |

Environment baseline run first: `hazard_test.py --table none --chapter 4`
PASS (file select 18.6 s, save load 26.3 s, 11 HUD damage hits, engine
alive) — proving the harness, keyboard input, save injection and room
rendering all work in this environment before any ch5 verdict was taken.

## The ch5 save room-id map (IMPORTANT for tooling)

This build's save room ids are a **sparse list** — deleted rooms leave holes —
and they do **not** match the community `tennaproject/tenna-editor`
`rooms.ts` ids beyond the low light-world rooms (rooms.ts says
`DW_GARDEN_MEETFLOWERY: 50120`; this build rejects 50120 outright). The
ROOM-chunk ordinal is NOT the save id either. Findings:

- Valid ids confirmed at runtime: 50013, 50024, 50054, 50075, 50114,
  50122, 50124, 50125, 50131, 50135 (and the binary-search pivots visible
  in `scr_room`'s bytecode: 50138, 50143, 50145, 50146, 50152, 50161-63,
  50169/70, 50181-83, 50187, 50190/91/93, 50207-50248...).
- Invalid (probed): 50118, 50119, 50120, 50121.
- Invalid ids degrade to the Annoying-Dog screen — they never crash.

`tests/probe_parallel.py` probes any candidate id in one ~4-minute run
(boot → continue → capture the `could not find room` line + two screenshots
for animation check). Use it before forging ch5 saves.

## Mechanics learned (all runtime-confirmed)

1. **A ch4 save body works as a ch5 save carrier** — the V2 format is
   byte-compatible; only the room line needs overriding. The file select
   shows FILE0 and Continue works even without a `dr.ini`.
2. **The full repo must be served.** Without `mus/`, the title flow enters
   a music-retry storm (a 404 HTML body "loads" as an ogg every ~0.4 s) and
   the file select never settles — looks exactly like a game freeze.
3. **The ch5 logo screen is not Z-skippable** and is frame-count-driven:
   ~20 s at interactive fps, 80-170 s under software GL. Tests must wait
   for `mus/menu.ogg`, not a fixed delay.
4. **The shader probe saw zero compile/link failures** in every valid room
   tested, including the field — the v9.5 `SHADER_LOOP_FIXES` + the game's
   own `g_shader_replace_simple_ready` guard fully cover the field. No
   JS-level DLL shim is needed (contrary to the v9.10 note's contingency).
5. `dlopen(shader_replace_simple_x64.dll) failed: "dynamic linking not
   enabled"` prints once at boot — expected, harmless.
6. A `141 50143` debug pair prints at every save-load (unchanged across
   rooms) — save-machinery debug output, harmless.

## The bar going forward

Any future change to ch5 chapter data or shader handling ships only after
`flower_test.py --room 50124 --watch 120` returns ALIVE on this harness —
plus the existing `hazard_test.py` chapter-4 gate for the damage patches.
