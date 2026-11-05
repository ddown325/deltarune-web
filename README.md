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

- You take **2× damage** from enemy attacks **and field hazards** (cars, bullets, chases, spikes — `scr_damage_all_overworld` is patched too)
- Enemy/boss attack phases run **1.2× as fast**
- The choice is saved in `localStorage` and remembered until you swap it
- The version line in the corner also shows the current mode (`kantasu` / `kantasu [HARD]`), updating live the moment you flip the toggle

**Bisecting a crash** — you can load only one half of the hard mode patches with a URL parameter:

- `index.html?c=1&hm=damage` — only the 2× damage patches (battle + overworld)
- `index.html?c=1&hm=speed` — only the 1.2× attack speed patch
- `index.html?c=1&hm=off` — pristine chapter data (normal mode, even if HARD is selected)
- `index.html?c=1&hm=full` — everything (default)

## v9.4 fixes

- **Overworld 2x damage actually works now — the whole party, doubled.** Every previous attempt (v9/v9.1/v9.3) wrote the trampoline's `pop.v.v self.hpdiff` as an *orphan*: a variable reference the GameMaker loader never resolves, because the loader only resolves nodes it reaches by walking each variable's occurrence chain (VARI record: name, instance type, string id, occurrence count, first-occurrence address; every node's ref dword is the byte offset to the next occurrence, the last node carries the variable's name-string-id, and the loader *rewrites* every visited node — proven in production by the battle patch's call-chain relink since v8.1). v9.4 *inserts* the scratch's pop into the hpdiff chain properly: the old terminator's dword becomes a forward hop to the scratch, the scratch's pop dword carries the terminator name-id, and the VARI occurrence count is bumped by one — the exact mirror of the battle patch's proven count-1 removal. Verified per chapter by simulating the loader's full walk of every VARI chain on the patched file: zero new failures vs pristine, the hpdiff chain ends exactly at the scratch's pop, both branch targets land on instruction boundaries, and the executed store becomes `hpdiff = tdamage * 2` for every party member.
- **Ch5 intro crash fixed — v9/v9.1/v9.3 all corrupted LIVE SPRITE NAMES.** The old scratch was carved into "unused" sprite-name strings chosen by a code-coverage check, but sprite names are referenced from the SPRT chunk, not code: `spr_face_torielglasses` (ch1), `IMAGE_LOGO_CENTER_HEART` (ch2/3), `spr_diner_table_messy` (ch4) and `spr_susie_noelle_lift_look_smile_shadow_floor` (ch5 — a Susie+Noelle sprite, exactly the ch5 intro cast) all had their first 20 characters overwritten with bytecode. v9.4's scratch lives in the STRG chunk's zero-padding tail (29-108 unused bytes after the last string, verified unreferenced file-wide), so no string content changes at all.
- **Save-filesystem storm mitigation (JS-side, zero bytecode).** The ch3/4/5 title/logo room re-evaluates its boot-menu decision every step until audio group 1 finishes loading: ini files are re-opened/re-parsed/re-written dozens of times per second (`obj_initializer2_Step_0` -> `scr_chapter_save_file_exists`, gated by `audio_group_is_loaded(1)`). The page now (1) drops the `_GetSaveFileName` debug spam from `Module.print` (a wasm->JS string marshal + console.log per file op — verified headless: console spam during boot drops from thousands of lines to zero), (2) skips IndexedDB puts whose file content is byte-identical to the last put for that key, and (3) caches the backend's remote entry listing between real writes. Real saves are unaffected (changed content = real put; caches reset on every real write).
- **Why v9.1's latch black-screened ch3/4/5** (for the record): it latched the *entire* per-step block in `obj_initializer2_Step_0`, but that block also carries the title room's room logic (`roomchoice`/`menu_go`/`screen_border_alpha`/`scr_windowcaption`). A correct fix rate-limits only the two expensive save-check calls (every ~250ms instead of every step); that is deliberately deferred — the JS mitigation above ships first because it cannot break boot.
- All five chapters verified headless with the full v9.4 patch stack: engine reaches `Entering main loop`, no page errors, no renderer crash, console spam eliminated.

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
- **owdamage** — the out-of-battle damage pipeline (bullets, cars, chases, spikes — every field hazard) runs through `scr_damage_all_overworld`, whose party loop carries the per-member damage assignment `hpdiff = tdamage` (`hpdiff` then feeds the per-member HP subtraction). That 16-byte store is redirected through a 20-byte bytecode trampoline — `pushi.e 2 / mul.i.v / pop.v.v self.hpdiff / b-back` — carved into the characters of an unused sprite-name string (sprite names are index-resolved metadata; the sprite itself is untouched). The scratch's `pop` ref dword is the **hpdiff name-string-id** (the chain terminator — the form the loader itself normalizes every visited dword to), so the trampoline computes exactly `hpdiff = tdamage × 2` and nothing else changes. The original chain node at the store site stays byte-identical (dead but pristine), so the VARI occurrence chain walks exactly as in the unpatched game — verified per chapter by simulating the loader's full chain walk: zero differences vs pristine. Per chapter this touches exactly 24 bytes: the 4-byte branch replacing the store's pop word plus the 20-byte scratch.
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
