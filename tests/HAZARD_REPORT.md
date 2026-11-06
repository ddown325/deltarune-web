# TASK B STEP 2 — HAZARD TEST REPORT (real-browser overworld damage)

Work dir: `/home/z/my-project/e2e/` (drweb served READ-ONLY at 127.0.0.1:8901).
Harness: Playwright chromium headless, `--enable-unsafe-swiftshader` (+ `--no-sandbox
--disable-dev-shm-usage --mute-audio --autoplay-policy=no-user-gesture-required`),
context `service_workers="block"`. All results reproducible via `hazard_test.py`.

## 1. Hazard + route (fastest reachable from a fresh boot)

**Hazard: ch2 `room_dw_mansion_sparks` (canonical room id 20229)** — the Queen's-mansion
wall-plug corridor. Its sparking outlets are `obj_hangplug_spark` instances
(target=3, damage=18, always-on) of the `obj_overworldbulletparent` class; walking into
a spark fires the call chain (confirmed by the captured v9.6 crash stack):
`obj_mainchara_Step_0` → collision → `obj_overworldbulletparent Other_15 (User Defined 5)`
→ **`scr_damage_all_overworld`**.

Route = **crafted IndexedDB save** (no walking from chapter start):
- Base: a real community save at the Cyber-City progress point
  (`WW92030-STORAGE/DELTARUNE` `X2/ch2_city`, plot 67, KRIS) — fetched from GitHub.
- Edited byte-exactly with `saveforge.py`: room line → 20229, dark=1, party (1,2,3),
  HP 90/90. Format: one value per line, CRLF, trailing spaces (V2: 3055 lines; tail =
  plot/room/time) — validated against the tennaproject save-parser source.
- Injected pre-load via `context.add_init_script` into the runner's Emscripten IDBFS
  store `/_savedata`/`FILE_DATA` as `{timestamp, mode:33206, contents:Uint8Array}`
  under key `/_savedata/filech2_0` — the exact format the port's own upload feature uses.
- Boot `?c=2&hm=damage` (hard mode ON via `localStorage.dr_webport_hardmode='1'` init
  script; `?hm=damage` isolates the damage component: battle 2x + overworld table, no
  speed patches). Console confirms `HARD MODE (?hm=damage)`.

Timings: main loop 2.3s → file select 12.5s (menu.ogg) → click + Z×3 loads the save →
room loaded 20.3s → **first hazard hit ≈ 24.2s** (≈4s of walking).

Engagement (the only scripted walking): slow diagonal sweep (ArrowLeft+ArrowUp held
together, 1.2s moves + 1.8–2.5s pauses) along the top wall where the plugs spark, then
an ArrowLeft run and small down/up jiggles; ends with `c` (menu). The pauses matter:
straight runs passed the sparks between flicker cycles without hits.

## 2. Hit confirmation (multiple independent signals)

- **HP HUD**: party HP bars (yellow) in the bottom-left HUD box — control run showed
  9 detections (387 yellow px) from t=39.7s to 45.8s (`hud_hits` in the JSON logs).
- **GAME OVER**: with HP 90/90 and 18 damage/hit the party died mid-engagement; the
  final screen OCRs as "GAME OVER … This is not your fate…" (tesseract).
  Consistent with 5 hits × 18 = 90 (the coordinator's damage=18 ground truth).
- **Crash timing correlation**: in both crash runs the failure lands at 24.18–24.30s —
  the FIRST spark contact (≈4s after room load) — while the no-table control survives
  the same engagement to 72s+.
- snd_hurt1 fetch: appears only once (sound caches); too weak as a repeat marker.

## 3. Test matrix (identical boots; only the table differs)

The table is injected by `context.route()` rewriting of the served index.html:
`window.OVERWORLD_DMG_PATCHES = {};` → `= <table>;` (verified replaced every run;
hard-mode console banner present every run).

### TEST 1 — v9.6 table (from `git show 95522d8:index.html`) → CRASH (PASS)
Run `test1_v96_v2` (`logs/hazard_test1_v96_v2.log`, exit PASS):
```
[  24.18s][log] ERROR!!! :: ################...
[  24.18s][log] ERROR in action number 1
[  24.18s][log] of Other Event: User Defined 5
[  24.18s][log] for object obj_overworldbulletparent:
[  24.18s][log] DoSet :2: Malformed variable
[  24.18s][log] at gml_Script_scr_damage_all_overworld
[  24.18s][log] gml_Script_scr_damage_all_overworld (line -1)
[  24.18s][log] gml_Object_obj_overworldbulletparent_Other_15
[  24.18s][log] gml_Object_obj_mainchara_Step_0 (line -1)
[  24.18s][PAGEERR] Xa
```
After the error the canvas is **permanently frozen** — every screenshot from the next
engagement step to the end pixel-diffs 0.0000 against the previous one for 45+ s
(0 HUD hits, no further console activity). The uncaught exception surfaces as
`Xa` (minified wasm RuntimeError). This is the v9.6 defect reproduced end-to-end in a
real browser: the first out-of-battle hit aborts the GM VM inside
`scr_damage_all_overworld` ("DoSet: Malformed variable" — the mangled funnel store).

### TEST 2 — no table (v9.7 empty) → NO CRASH (PASS, control)
Run `test2_none_v2` (`logs/hazard_test2_none_v2.log`, exit PASS):
- No pageerrors, no renderer crash, rAF alive throughout, 30s post-watch clean.
- 9 HP-HUD damage detections; party died from accumulated spark hits → GAME OVER
  screen (engine handled it normally — the game-over screen is a healthy static frame).
- Damage magnitude: 5 hits to die at 90 HP ≈ 18/hit — matches the expected spark
  damage (16/18 class). No 2x involved (no overworld table injected).

### TEST 3 — v9.8 candidate (`analysis/final_patch.json`) → HARD CRASH (FAIL)
Run `test3_v98` (`logs/hazard_test3_v98.json/log`):
- **Renderer process crashed at 24.30s** — the same first-hit moment as TEST 1 —
  while the only difference vs the passing control is the injected table.
- No console error, no pageerror: the wasm died natively (the engagement aborted 3.9s
  in; `page.screenshot` failed with "Target crashed").
- Verdict: the v9.8 trampoline **crashes the real engine at the first overworld damage
  event** — a harder failure than v9.6 (process death vs VM abort). The static/mini-VM
  proofs in `analysis/final_proof.log` did not transfer to the real VM.

## 4. HP / 2x observability

- Control damage per hit ≈18 (5 hits → death at 90 HP); HP bars + game-over screen +
  tesseract OCR of the GAME OVER screen all consistent.
- The 2x verification for the candidate is **moot: the candidate crashes on hit #1**,
  before any damage is observable. No HP/number comparison was possible. (dmgwriter
  numbers never survived long enough to OCR in the crash runs; in the control run the
  party died before the menu step could capture exact HP.)

## 5. Verdicts

| Test | Table | Result | Signature |
|---|---|---|---|
| TEST 1 | v9.6 | **CRASH — defect reproduced (PASS)** | `DoSet :2: Malformed variable at gml_Script_scr_damage_all_overworld` (from obj_overworldbulletparent User-5 ← obj_mainchara_Step_0) + pageerror `Xa` + canvas frozen @24.18s |
| TEST 2 | none | **NO CRASH — control PASS** | 9 damage HUD hits, party died (GAME OVER), engine alive ≥30s after |
| TEST 3 | v9.8 candidate | **CRASH — candidate FAILS** | renderer process crash @24.30s at first hit, no JS-level signature |

**Bottom line: the v9.8 candidate (final_patch.json ch2) must NOT ship — it crashes
the real engine exactly like v9.6 did, one damage event into the overworld.**

## 6. Fidelity limits (honest)

- Chapter coverage: matrix run on ch2 only (fastest reliable hazard). v9.6/v9.8 tables
  for other chapters were not E2E-tested (the harness supports `--chapter 3/4` with
  room 30134/40260 + tuned walks, but those hazards are state-gated: the ch4
  extinguisher's damage path did not fire in earlier probes, ch3 green-room racing
  never started, ch2 traffic-room cars never spawned with either save).
- Hit timing depends on spark flicker cycles; the engagement is a sweep+pause pattern,
  not a deterministic single-step trigger (control run: first HUD hit ~19s into the
  sweep; crash runs: first contact ~4s in — the crash itself needs only ONE hit).
- The pageerror surfaces as minified `Xa`; the GM console error above it is the
  human-readable signature.
- 2x-damage magnitude not verified (candidate crashes pre-observation). The control's
  ~18/hit (5 hits → death at 90 HP) matches the expected spark damage.
- The save is a real community save edited on 3 lines (room/dark/HP); everything else
  (plot 67 flags, inventory) is genuine mid-ch2 state.
- Damage clamps per the decompiled GML: never kills below 1 HP per hit — death here is
  accumulation across hits, consistent with what the control run showed.

## 7. Artifacts

- `hazard_test.py` — reusable: `--table {v96|none|<path.json>} --chapter N [--hp N
  --maxhp N]` → verdict line + exit 0 PASS / 1 FAIL.
- `saveforge.py` — save crafting (V1/V2 formats, room/party/HP editing).
- `boot_harness.py`, `rooms.py`, `find_callers.py` — rebuilt step-1 harness + RE tools.
- Logs: `logs/hazard_test1_v96_v2.{log,json}`, `logs/hazard_test2_none_v2.{log,json}`,
  `logs/hazard_test3_v98.{log,json}` (+ earlier probes `hazard_probe_*`,
  `hazard_test1_v96_final`, `test2_none_final`, exploration logs).
- Shots: `shots/hazard_<tag>_*.png` (engagement steps, rapid hold-shots, final/game-over).
- Save sources: `research/saves/` (real base saves + crafted), `research/tenna/`
  (community save-format sources), `research/roommap.json`, `research/callers.json`.
