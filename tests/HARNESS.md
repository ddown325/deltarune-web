# tests/ — the overworld-damage runtime gate

A real-browser harness that makes the party take REAL overworld hazard damage
in a headless Chromium, against a locally-served copy of this repo, with an
arbitrary `window.OVERWORLD_DMG_PATCHES` table injected via Playwright route
interception (the repo itself is never modified).

This exists because **every static proof method has now failed at runtime**:
seven overworld-2x designs (v9, v9.1, v9.2, v9.3, v9.4, v9.6, and the two
v9.8 candidates) passed chain-walk simulations, branch checks, and mini-VM
equivalence proofs — and every one of them crashed the real engine. The
harness is the only verification that counts.

## Usage

```bash
# deps: python3, playwright (chromium), PIL; tesseract for OCR (optional)
python3 tests/hazard_test.py --table none  --chapter 2   # control: damage, no crash
python3 tests/hazard_test.py --table v96   --chapter 2   # v9.6 repro: MUST crash
python3 tests/hazard_test.py --table path/to/candidate.json --chapter 2
```

Exit 0 + `VERDICT: PASS` = the run behaved as expected. For a NEW patch table,
PASS means: damage taken (HUD hits / party HP drop), no renderer crash, no
pageerror, engine alive ≥30s after the hit.

What it does: boots `?c=N&hm=damage` with hard mode ON (localStorage init
script), injects a save forged by `saveforge.py` directly into the runner's
IndexedDB (`/_savedata`, the port's own upload format) that spawns the party
inside a hazard room, walks the party into the hazard with a slow-sweep input
script, and watches console + pageerror + renderer liveness + the HP HUD.
`verify_inject.py` proves the route-injection actually lands in the page.
`unxlib.py` is the GameMaker-2022.3 unx parser (chunks, VARI/FUNC chains,
disassembler) used for bytecode analysis.

Confirmed-working hazard room: ch2 `room_dw_mansion_sparks` (20229) — always-on
sparking outlets, target=3, damage=18, whole party. ch1 `room_forest_area2`
(10357, four darkponmen with orbiting bullets, damage=16) is configured;
ch3/ch4 rooms are present but engagement is not yet reliable there.

## The postmortem this harness proved (2026-11-06)

Run against the LIVE v9.7 build with three different tables:

| table | result at runtime |
|---|---|
| none (v9.7) | PASS — 18 dmg/hit, 10 HUD hits, party died of accumulated damage, engine stable |
| v9.6 (95522d8) | CRASH reproduced — `ERROR ... User Defined 5 for object obj_overworldbulletparent: DoSet :2: Malformed variable at gml_Script_scr_damage_all_overworld`, canvas frozen from first hit |
| v9.8-a (verbatim-dword trampoline) | CRASH — renderer process died at the FIRST damage event (native wasm death); the off-chain raw pop dword `08 00 00 a0` does not resolve in the real VM |
| v9.8-b (chain-insertion trampoline) | CRASH — the loader itself dies at boot with the patched chapter file (main loop never reached); reproduces 2/2 in a clean environment while the control boots in 2.4s |

Mechanics learned the hard way (all runtime-confirmed):

1. `scr_damage_all_overworld` is called from `obj_overworldbulletparent`'s
   User-5 event when `target == 3` (whole party); `obj_mainchara_Step_0`'s
   collision check fires it when `global.inv < 0`.
2. The function cancels hits with `hpdiff <= 1` and clamps to `hp-1`
   (overworld hazards can never kill), then sets i-frames `invc*40`.
3. Off-chain (unnormalized) variable-reference dwords in the GameMaker VM
   resolve by **name-id**, not by raw refId — a copied raw dword is
   "Malformed variable" at runtime. Any trampoline whose scratch carries an
   off-chain dword MUST link the scratch node into the variable's VARI
   occurrence chain… but see (4).
4. Even a chain-linked scratch (v9.8-b) killed the wasm loader at boot —
   the production loader's chain walk is stricter than any offline
   simulation of it. Chain-link encoding notes: signed-24-bit byte deltas
   in operand dwords; v9.4's original failure is now understood as relink
   deltas exceeding the +8,388,607 signed-24 limit and wrapping negative.
5. `callv` is NOT a no-op filler — it variable-calls the stack top (v9.6's
   crash: `pushi.e 0 / callv -1` filler aborted the VM on every damage event).

## The bar for any future attempt

A new overworld patch ships ONLY after `hazard_test.py --table <candidate>`
returns PASS on chapter 2 (and ideally chapter 1) — real damage, whole party,
no crash, engine alive — in this harness, from a clean clone. Static proofs
(chain walks, mini-VM) are useful for design but carry **zero** shipping
weight on their own.
