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

## v9.3 fixes

- **Chapter 5 black screen fixed.** The v9.1 "save-poll latch" trampoline for ch3/4/5 broke the boot sequence outright — it is fully reverted (back to the v8.1 patch set: ch5 shader stubs only). The save-poll storm it tried to kill is real (see below), but the latch never worked; with it gone every chapter boots again.
- **Overworld 2× damage hits the whole group now — the real bug found.** The patched store in `scr_damage_all_overworld`'s party loop is the 16-byte `hpdiff = tdamage` assignment (`[push.v.d self.tdamage] [pop.v.v self.hpdiff]`). How GameMaker 2022.2+ variable references work: the 4-byte ref dword after a push/pop holds the **offset to the next occurrence of the same variable** (the VARI occurrence chain); only the *last* occurrence carries the variable's **name-string-id** as terminator. The loader walks every chain and normalizes each visited dword to that name-id, which is what the executing VM reads. v9/v9.1 had the *right* scratch dword (the hpdiff name-id) but also **re-linked the chain through the scratch** — the walk then treated the name-id as an offset, diverged into string data, and every `hpdiff` reference after that point (the HP-subtraction reads for members 2/3) was left unresolved: "2× damage only hits one person". v9.2 correctly dropped the relinks but **misread the pristine dword `+8` as a variable id** and copied it into the scratch — at runtime that resolves to whatever variable is named by string #8, the doubled damage vanishes there and `hpdiff` goes stale. v9.3 keeps v9.2's structure (no relinks; the chain still walks through the original, now-dead dword at the store site) and restores the correct scratch dword (the hpdiff chain terminator name-id: `0x7a9`/`0x194f`/`0x26f2`/`0x2707`/`0x7e4` per chapter). Verified by automated full-chain walks on all five chapters: **zero differences vs pristine**.
- **Branch encoding confirmed.** Branch offsets are **23-bit two's-complement values in instruction units** (the compiler sign-extends backward jumps into `0x7F…` byte-twos, forward jumps use `0x00…`); the scratch's branch-back word clears bit 23 to match the compiler's own encoding, and both branch targets land exactly on instruction boundaries (verified arithmetically per chapter).
- **Known remaining issue:** the ch3/4/5 title-room save-poll storm (~4 FS opens per step while the chapter logo screen is up, visible as a wall of `_GetSaveFileName` lines in the console) still causes the ch5 "DELTARUNE opening" lag. The v9.1 latch was the right idea with a broken implementation; a correct fix needs a carefully verified gate on `obj_initializer2`'s state machine and is deferred — the game boots and plays fine, it just hitches at the title screen.

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
