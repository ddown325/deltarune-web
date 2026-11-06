# DELTARUNE Web Port — Hard Mode Edition

An unofficial browser port of DELTARUNE (Chapters 1–5) with a built-in **HARD MODE**, SOUL-style loading screens and a single-file version.

## Play

- **Full site** — open `index.html` via GitHub Pages (or any static host)
  - Chapter select: `index.html` (or `?c=0`)
  - Jump straight to a chapter: `index.html?c=1` … `?c=5`
- **Single file** — download `onefile.html` alone, put it anywhere (even your desktop) and open it. It streams all game data from the Pages deployment, so it needs internet. Works great as a mobile home-screen app.

Controls: arrows to move, **Z / Enter** to confirm, **X** to cancel.

## HARD MODE

Pick your mode on the **chapter select screen** itself: the **MODE** selector sits in the bottom-right corner of the menu, level with the game's version line. It's styled exactly like the game's own footer options — the red SOUL marks the option you're on, the active one lights up yellow. Click **NORMAL** / **HARD**, or press **H** to flip it.

- You take **2× damage** from enemy attacks in battles, and **1.2× faster enemy attack patterns**. (Out-of-battle hazard 2x is **off** as of v9.7 — every bytecode attempt so far corrupted the damage state and crashed; see the v9.7 note below.)
- Enemy/boss attack phases run **1.2× as fast**
- The choice is saved in `localStorage` and remembered until you swap it
- The version line in the corner also shows the current mode (`kantasu` / `kantasu [HARD]`), updating live the moment you flip the toggle

**Bisecting a crash** — you can load only one half of the hard mode patches with a URL parameter:

- `index.html?c=1&hm=damage` — only the 2× battle-damage patches
- `index.html?c=1&hm=speed` — only the 1.2× attack speed patch
- `index.html?c=1&hm=off` — pristine chapter data (normal mode, even if HARD is selected)
- `index.html?c=1&hm=full` — everything (default)

## v9.7 hotfix (current)

- **The v9.6 overworld 2x patch is PULLED — it crashed the game on EVERY out-of-battle damage hit.** The v9.6 rewrite passed every static proof (VARI/FUNC chain-walk against the real runner algorithm, branch-boundary checks, per-chapter byte diffs) and a boot+soak E2E — but no test ever actually **took overworld damage**, which is the only moment the rewritten code executes. Fifth failed attempt (v9/v9.1/v9.3/v9.4/v9.6). Hard mode now doubles damage **in battles only** (the production-stable battle patch). The overworld table (`window.OVERWORLD_DMG_PATCHES`) is empty again. **Rule going forward: no overworld damage patch ships again until it survives a runtime test that takes real hazard damage in a real browser session.**
- The ch5 boot-storm fix (STORM_PATCHES) and the WebGL1 shader-loop fix from v9.5/v9.6 are unaffected and stay live.
- Battle 2x + 1.2x attack speed: unchanged, still applied with `?hm=full` (default) / `?hm=damage` / `?hm=speed`.

## v9.6 fixes (superseded by the v9.7 hotfix)

- **Overworld (out-of-battle) 2x damage is BACK — for the whole party, and this time derived from a real decompilation.** In `scr_damage_all_overworld`, the loop-top funnel `damage = remdamage; target = ti; tdamage = damage` (52 bytes) is rewritten in place, branch-free and byte-length-preserving, into `tdamage = remdamage * 2; target = ti` — the doubled value rides the VM stack straight through the untouched `target = ti` store. The two `damage` variable-nodes the rewrite orphans are spliced out of `damage`'s occurrence chain with the exact count-surgery the battle patch has used since v8.1 (occ −2, predecessor delta relink). Every chapter verified by whole-file VARI/FUNC chain-walk simulation against the *actual* runner algorithm (extracted from runner.wasm and validated against both proven production patches): zero unintended diffs, every branch target on an instruction boundary, `scr_damage` byte-identical, and headless E2E boot+soak PASS on all five chapters with the patch live. The kill-clamp, d_cancel and downed logic all sit downstream of the store, so they see exactly 2x and nothing else changes. (Why v9.4 failed, now provable: it hand-modeled the chain walk with the wrong node base — VARI nodes live at `first+4`, FUNC nodes at `first` — so its inserted node corrupted resolution for 198 unrelated `damage` references.)
- **The ch5 boot-storm crash is fixed.** Chain of causes, all runtime-proven this time: ch5's audio group 1 is an embedded byte-slice (loads in ~4s), so `obj_initializer2_Step_0`'s `audio_group_is_loaded(1)` gate opens almost immediately — and its boot-menu block (4 save-file ini opens) then ran **every step** (200-370/s). Each ini open leaks ~81KB of native memory in the web runner → the tab OOM-died at ~143s (dmesg-confirmed renderer kill, 3/3 repro). Worse, the block's `room_goto(roomchoice)` to the room you're already in **restarts the room** in this web runner (a no-op on desktop) — every restart spawned a fresh initializer and re-ran everything: the intro could never advance. The fix (ch5-only, always on — the crash hit every ch5 player): (1) a per-instance latch — `roomchoice` (initialized to 0 by a rewritten dead `tempflag` write in Create) is pushed at the block entry and `bt` skips the block once it has run; (2) a same-room goto guard — `if (room == roomchoice) skip room_goto` — the desktop no-op made explicit, built from the web-dead `is_console` padding. Same whole-file proof harness as above; E2E: ch5 boots to the title, renders continuously, zero errors, 300s+ survival (was: dead at 143s).
- **Known issue (next iteration):** the save-select screen's `DEVICE_MENU_Step_0` still re-scans the 6 save slots + dr.ini every step (the desktop game does this too — harmless there; on web it leaks ~0.6MB/s). The game is fully playable — booting, intro and battles are unaffected — but idling on the save-select for a very long time on a low-RAM machine can eventually exhaust the tab. The same latch recipe applies.
- Chapters 1-4: byte-identical behavior (the storm table is ch5-only; verified ch1/ch2/ch3/ch4 E2E with the full v9.6 stack: boot, hard mode, overworld patch applied, no regressions).

## v9.5 fixes (superseded by v9.6)

- **Ch5 intro crash fixed at the ROOT: the shipped GLSL shaders are illegal under WebGL 1.** The chapter 5 Windows build embeds 8 shader families whose fragment code uses `for` loops with **uniform** bounds — `for (float i = 0.0; i <= blur_radius; i += 1.0)`, `for (float i = 0.0; i < paletteHeight; i++)`, `for (float i = 0.0; i < sample_count; i += 1.0)` and `for (float i = corner.y; i < u_Uvs.w; i += u_pixelSize.y)` (the init being non-constant is equally illegal). WebGL 1 compiles GLSL ES 1.00, where a loop index may only be compared against a **constant** expression — so *every one of these shaders fails to compile* (`ERROR: 'i' : Loop index cannot be compared with non-constant expression`). Two of them (the logo blur + the wave palette shader) are used by the ch5 intro: with the shaders dead the intro froze/crashed a few seconds into the X-marks/`del` logo screen. This was misdiagnosed three times as bytecode-scratch corruption (v9/v9.1/v9.3 sprite names, v9.4 STRG tail) — the real cause was never the scratch. The remaining families (castle reflection, separable blur, spiral blur, pixel-scan palette) would have crashed later areas the same way.
- **The fix** rewrites each loop header with a **constant upper bound plus an inner guard** (`for (float i = 0.0; i < 64.0; i++) { if (blur_radius < i) break; …`) — the exact idiom ch5's own `MAX_SAMPLES`/`u_samples` and `MAX_QUALITY`/`q` shaders already use, so it is proven compatible with the runner's compiler path. Absorbed body lines are re-emitted whitespace-minified and the result padded with spaces so **each replacement is exactly the same byte length** as the original text — string-table framing, lengths and pointers are untouched. Applied at load time as a **content search** over the downloaded chapter parts (`SHADER_LOOP_FIXES` in `index.html`): 16 sites in chapter 5 (each family's GLSL-ES + desktop-GL variants), a no-op on chapters 1–4 (verified — they contain none of these loops). Verified headless end-to-end: `SHADER FIX: rewrote 16` at load, **zero** shader compile errors (was 2 at boot + more later), intro renders the X-marks/`del` logo screen (the exact frame the crash report showed) and the engine stays alive — previously it hard-froze the renderer within seconds of that screen.
- **Overworld (out-of-battle) 2× damage REMOVED.** The v9/v9.1/v9.3/v9.4 bytecode attempts at doubling `scr_damage_all_overworld`'s `hpdiff` store all mis-modeled the GameMaker 2022.3 variable-occurrence chains, and v9.4 shipped with real symptoms: damage applying to Kris only, inconsistent amounts, party members getting DOWNED by ordinary field hazards, and a hard crash when a downed member was hit again. Hard mode now doubles damage **in battles only** (the battle patch, production-proven since v8.1). `window.OVERWORLD_DMG_PATCHES` is empty in v9.5. A correct overworld implementation needs a decompiler-driven rewrite of the whole function, not byte-offset chain surgery.
- Chapters 1–4 verified unaffected: `?c=1&hm=full` (hard mode ON) boots, battle + attack-length patches apply, intro renders and advances; the shader-fix search reports 0 matches outside chapter 5.

## v9.4.1 fixes (superseded by v9.5)

- **v9.4.1: the v9.4 save-filesystem storm fix is REVERTED.** It broke entering chapters from the chapter select: the game prints its chapter-change signal through a `_GetSaveFileName` debug line (`_GetSaveFileName in:chapter1_mac out:chapter1_mac`), and the fix's `Module.print` filter dropped EVERY line containing `_GetSaveFileName` — so the page never saw `in:chapterN_mac` and never navigated, and no chapter would start. The fix's IDB hooks (skip identical `Bk` puts, cache `lk` listings) were also unsafe with existing saves: any engine file operation that bypassed the wrapped backend would leave the wrapper's caches stale and could eat writes. The save-poll storm itself is harmless (console spam + redundant identical IDB writes); a safe future fix would rate-limit only the two save-check calls in `obj_initializer2_Step_0` (see below) instead of touching the print/FS pipeline.

- **Overworld 2x damage actually works now — the whole party, doubled.** Every previous attempt (v9/v9.1/v9.3) wrote the trampoline's `pop.v.v self.hpdiff` as an *orphan*: a variable reference the GameMaker loader never resolves, because the loader only resolves nodes it reaches by walking each variable's occurrence chain (VARI record: name, instance type, string id, occurrence count, first-occurrence address; every node's ref dword is the byte offset to the next occurrence, the last node carries the variable's name-string-id, and the loader *rewrites* every visited node — proven in production by the battle patch's call-chain relink since v8.1). v9.4 *inserts* the scratch's pop into the hpdiff chain properly: the old terminator's dword becomes a forward hop to the scratch, the scratch's pop dword carries the terminator name-id, and the VARI occurrence count is bumped by one — the exact mirror of the battle patch's proven count-1 removal. Verified per chapter by simulating the loader's full walk of every VARI chain on the patched file: zero new failures vs pristine, the hpdiff chain ends exactly at the scratch's pop, both branch targets land on instruction boundaries, and the executed store becomes `hpdiff = tdamage * 2` for every party member.
- **Ch5 intro crash fixed — v9/v9.1/v9.3 all corrupted LIVE SPRITE NAMES.** The old scratch was carved into "unused" sprite-name strings chosen by a code-coverage check, but sprite names are referenced from the SPRT chunk, not code: `spr_face_torielglasses` (ch1), `IMAGE_LOGO_CENTER_HEART` (ch2/3), `spr_diner_table_messy` (ch4) and `spr_susie_noelle_lift_look_smile_shadow_floor` (ch5 — a Susie+Noelle sprite, exactly the ch5 intro cast) all had their first 20 characters overwritten with bytecode. v9.4's scratch lives in the STRG chunk's zero-padding tail (29-108 unused bytes after the last string, verified unreferenced file-wide), so no string content changes at all.
- **(Historical, v9.4, reverted in v9.4.1) Save-filesystem storm mitigation.** The ch3/4/5 title/logo room re-evaluates its boot-menu decision every step until audio group 1 finishes loading: ini files are re-opened/re-parsed/re-written dozens of times per second (`obj_initializer2_Step_0` -> `scr_chapter_save_file_exists`, gated by `audio_group_is_loaded(1)`). v9.4 tried to make this cheap by filtering `_GetSaveFileName` lines, skipping identical IDB puts and caching entry listings — see the v9.4.1 note above for why that was reverted.
- **Why v9.1's latch black-screened ch3/4/5** (for the record): it latched the *entire* per-step block in `obj_initializer2_Step_0`, but that block also carries the title room's room logic (`roomchoice`/`menu_go`/`screen_border_alpha`/`scr_windowcaption`). A correct fix rate-limits only the two expensive save-check calls (every ~250ms instead of every step); that remains the deferred approach — do NOT filter the print/FS pipeline (see v9.4.1).
- All five chapters verified headless with the full v9.4 patch stack: engine reaches `Entering main loop`, no page errors, no renderer crash. v9.4.1 additionally verified END-TO-END headless: menu game -> chapter select -> `in:chapter1_mac` signal fires -> page navigates to `?c=1` -> chapter boots (the exact flow v9.4 broke).

## What's in this port

- **All five chapters** in the browser, with a Save File Manager (export / import saves as ZIP)
- **Fast revisits** — a service worker (`sw.js`) keeps the immutable game files (chapter parts, engine files, music, videos) in Cache Storage permanently. GitHub Pages only caches for 10 minutes, which used to force a 60–170 MB re-download every visit before you could even reach save select; now revisits boot from disk in about a second. The engine's own files also download in parallel with the chapter archive, and hard-mode patching happens in place instead of assembling a full file copy (no more 400 ms pause either)
- **HARD MODE** — see above
- **SOUL loading screens** — while a chapter downloads you get the chapter title, a pixel SOUL that fills up with real download progress, a live percentage, stage messages (`LOADING DATA` → `APPLYING HARD MODE` → `LOADING ENGINE` → `STARTING GAME`) and a **RETRY** button if the connection drops
- **Per-chapter tab titles** — the browser tab reads `Chapter 1` … `Chapter 5` while you play
- Custom site favicon (`favicon.ico` on every page)
- Quit button with a proper quit screen

## How HARD MODE works (technical)

Hard Mode is implemented as byte-level patches to the GameMaker data files, applied in the browser right before the game boots:

- **damage** — inside `scr_damage`, the `ceil(...)` call that finalises damage taken by the party is replaced by `× 2` (same instruction size, stack-neutral, typed as `mul.i.v` exactly like the compiler emits it). The `ceil` function's reference chain is repaired accordingly (occurrence count −1, the previous link re-pointed past the removed call, per chapter).
- **owdamage** — REMOVED in v9.5 (see above). The v9.4 chain-insertion implementation corrupted the `hpdiff` variable resolution (Kris-only damage, downing, crash-on-hit-while-downed). The table ships empty; a future attempt must be derived from a real decompilation of `scr_damage_all_overworld`, not hand-modeled occurrence chains.
- **speed** — in `obj_battlecontroller`'s Step event, `global.turntimer -= 1` becomes `-= 1.2`, so attack phases count down 1.2× faster. The restructured `&&` short-circuit around it is re-emitted in an equivalent shorter form, and the `turntimer` / `reset` variable reference chains are repaired (deltas re-pointed, per chapter).

Both components are stack-balanced, keep every branch target on an instruction boundary, and leave all FUNC/VARI reference chains walking exactly as in the original data (verified per chapter: only `scr_damage` and `obj_battlecontroller_Step_0` decode differently, and only in the patched region).

### The MODE selector (chapter select)

The chapter-select screen (`game.unx`) only gets one tiny patch: the 24-byte version line in the corner is baked to read `kantasu` or `kantasu [HARD]`, matching the selected mode (same length as the original line, so nothing moves). The **MODE** selector itself lives on the page side (`#drModeBar`): it's anchored to the game canvas, drawn in the game's own style (Press Start 2P, red SOUL, yellow highlight) and flips the mode instantly — including rewriting the version line *inside the running game's memory*, so the corner label changes the same frame you click, with no reload.

The chapter archives (`chapter*.unx.part*`) are untouched on disk; Normal Mode loads them unmodified.

> History: v5 briefly shipped a fully native in-game MODE choice (an injected `obj_ui_choice` installed by patched console-init code). It hung the game at boot, so v6 reverted it — the chapter-select data file is now byte-identical to the original apart from the 21-byte version-line text.

### Boot robustness (v7)

The boot path never fails silently any more. Every failure mode now either self-heals or shows a message on the loading screen:

- **Chunk downloads** are ranged and resumable: every chunk is verified against its exact expected size (a table baked into the page from the repo), a 30-second stall watchdog aborts dead connections, received bytes are kept and the remainder is fetched with `Range`, each attempt is cache-busted, and persistent failures end in `DOWNLOAD FAILED` + RETRY. A stalled or truncated chunk can no longer reach the engine — in v6 and earlier a truncated `game.unx` made the engine hang forever at `LOADING ENGINE` with no error anywhere (this is the actual cause of the "stuck loading the engine" reports: the CDN intermittently closes a big download early with a clean-but-short body).
- **Engine files are prefetched the same way** — `runner.data` and `runner.wasm` are downloaded through the same resumable verifier and handed to the engine as blob URLs, so a truncated engine file can't silently kill the boot either.
- **WebGL pre-check** — if the browser blocks WebGL (hardware acceleration off, broken driver), the loading screen says `WEBGL BLOCKED - ENABLE HARDWARE ACCELERATION` instead of dying to a black screen mid-boot.
- **Engine watchdog** — if the engine doesn't reach `Entering main loop` within 3 minutes, the loading screen says `ENGINE STUCK (ns) - CHECK CONSOLE` and the console carries a reportable line with the engine's last status; page errors during boot are surfaced on the loading screen too.
- **Cache-busting** — the runner script, runner data and archive chunks are fetched with `?v=7`, so a stale copy poisoned into a proxy cache can't reach the engine either.

## Running your own copy

Everything is static — no build step, no server code:

1. Clone / download this repo
2. Serve it from any static host (GitHub Pages, itch.io ZIP upload, a local `python -m http.server`, …)
3. Open `index.html`

Repo layout: `index.html` + `onefile.html` (the site), `game.unx.part001` (chapter-select launcher), `chapter*.unx.part*` (chapter data), `runner*` (the GameMaker web runtime), `chapter*_windows/`, `mus/`, `vid/` (chapter assets).

## Credits

- **DELTARUNE** by Toby Fox — [deltarune.com](https://deltarune.com)
- Web port by **kantasu**
- The GameMaker-to-web loader engineering this project started from was built by **Mollomm1**
- *Press Start 2P* font by CodeMan38 — SIL Open Font License 1.1
