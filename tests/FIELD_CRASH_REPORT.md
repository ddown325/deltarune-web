# tests/FIELD_CRASH_REPORT.md — the Field of Hopes crash, root-caused and fixed (2026-11-10, v9.11)

**Player report:** "it crashed after running into the Field of Hopes and Dreams
— is it because one of the petals hit Kris even though they don't do dmg?"

**Answer: no — the petals were innocent.** The crash was the ch5 hard-mode
**speed component** (the `obj_battlecontroller` turntimer rewrites + the
`ATTACK_LEN_HARD_EXTRA` re-scaling across 47 enemy Step functions), dying the
first time a ch5 encounter script ever executed on the web port. Fix: ch5's
speed tables are pulled in v9.11 (ch5 hard mode = 2x battle damage + the
always-on base 1.2x attack length; ch1-4 untouched).

## Why nobody could have caught this before v9.10

The ch5 speed writes shipped in v8.1 with static-only verification, during an
era when **no player could reach ch5 gameplay at all** (the intro was frozen
black from v8.1 through v9.9; see the README history). The first day the
garden was reachable (v9.10, 2026-11-09) was the first day any of that
bytecode ever executed. The first player to run into the field with HARD mode
on hit it within minutes.

## The reproduction (headless, software GL, real repo bytes)

`tests/bisect_test.py` + `tests/fixcheck_test.py`: a forged `filech5_0`
(real ch4 save body, room line overridden to 50124 = the floradinn field room)
loaded through the REAL file-select flow, plus a pure-DATA route patch that
moves room 122's `obj_mainchara` spawn record to the floradinn trigger region.
No bytecode edits, no chains — spawn coordinates only.

| round | config | verdict |
|---|---|---|
| bis1 | hm=off, spawn embedded in slope solids, all active objects moved away | **CRASH** (harness artifact — see below) |
| bis2 | hm=off, the 8 `obj_*_dark` slope instances moved away | ALIVE |
| bis3 | hm=off, spawn (3900,1330), nothing removed | ALIVE |
| bis4 | **hm=full**, spawn (3900,1330) | **CRASH at ~33 s** (≈2 s after room load) |
| bis5 | **hm=damage**, spawn (3900,1330) | **ALIVE** |
| bis6 | **hm=speed**, spawn (3900,1330) | **CRASH** |
| bis7 | hm=speed, sheary waves removed | **CRASH** |
| bis8 | hm=speed, floradinn trigger removed | **ALIVE** |
| fx1 | fix applied via route interception, hm=full, crash position | **ALIVE** |
| fxfinal | fix baked into the real index.html, hm=full, crash position | **ALIVE** |

Crash signature in every failing round: renderer process death, **zero** page
errors, zero GML errors, zero shader failures, memory flat (watched with a
ps/free sampler — not OOM), ~300 ms after the room's music files are requested.
Same class as the v9.8 trampoline disasters: native wasm death with no
diagnostic.

## The two confounders, ruled out along the way

1. **The "petals"** (`obj_dw_garden_shearyencounter_waves`): ambient
   field creatures that wander, then alert (squawk + sprite change) when Kris
   comes within 120px — they deal no damage, matching the player's
   description. Round bis7 removed all 14 of them: still crashed. Innocent.
2. **A harness artifact**: spawning the party *embedded inside the slope
   solid* (the `obj_sul_dark`/`obj_sdr_dark` pool banks at the room's ledges)
   kills the renderer natively even on pristine data (bis1 vs bis2). Real
   players cannot spawn inside geometry; only forged saves can. Documented so
   the next bisect doesn't chase it.

## What was verified clean in passing (all new runtime coverage for ch5)

- **The garden video room** (`room_dw_garden_video` — the game's ONLY
  `video_open`/`video_draw` caller): plays `vid/ch5_intro_en.mp4` end-to-end
  headless — video frames render, `video_end` fires, `dr.ini` is written, the
  room transitions to meetflowery. Reached by patching one byte of
  `scr_get_room_list`'s save-id array so save 50114 loads room 119
  (`tests/videoroom_test.py`). The console-only YUV/`shader_replace` path
  exits early on web (`is_console` guard) and the format-0 surface path works.
- **A ch5 battle start**: the floradinn encounter opens the battle
  (`mus/rakuichi_buster_wip.ogg`), the menu is navigable, no crash
  (`tests/battle_test.py`, hard mode damage-only round bis5).
- **The field at the savepoint**: 200+ s walking with hard mode full stack
  (`flower_test.py --hard --walk`), 60 fps, petal particles animating.

## The bar going forward

Per HARNESS.md's standing rule (written after the v9.7 overworld-damage
incident): **no ch5 speed/turntimer bytecode ships again until it survives a
real ch5 battle in this harness** — encounter started, enemy turn taken,
damage received. `bisect_test.py`/`fixcheck_test.py` are the templates; the
spawn record offset for room 122 is `4043324` in `chapter5.unx.part001`.
