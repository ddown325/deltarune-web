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

- You take **2× damage** from enemy attacks
- Enemy/boss attack phases run **1.2× as fast**
- The choice is saved in `localStorage` and remembered until you swap it
- The version line in the corner also shows the current mode (`kantasu` / `kantasu [HARD]`), updating live the moment you flip the toggle

**Bisecting a crash** — you can load only one half of the hard mode patches with a URL parameter:

- `index.html?c=1&hm=damage` — only the 2× damage patch
- `index.html?c=1&hm=speed` — only the 1.2× attack speed patch
- `index.html?c=1&hm=off` — pristine chapter data (normal mode, even if HARD is selected)
- `index.html?c=1&hm=full` — everything (default)

## What's in this port

- **All five chapters** in the browser, with a Save File Manager (export / import saves as ZIP)
- **HARD MODE** — see above
- **SOUL loading screens** — while a chapter downloads you get the chapter title, a pixel SOUL that fills up with real download progress, a live percentage, stage messages (`LOADING DATA` → `APPLYING HARD MODE` → `LOADING ENGINE` → `STARTING GAME`) and a **RETRY** button if the connection drops
- **Per-chapter tab titles** — the browser tab reads `Chapter 1` … `Chapter 5` while you play
- Custom site favicon (`favicon.ico` on every page)
- Quit button with a proper quit screen

## How HARD MODE works (technical)

Hard Mode is implemented as byte-level patches to the GameMaker data files, applied in the browser right before the game boots:

- **damage** — inside `scr_damage`, the `ceil(...)` call that finalises damage taken by the party is replaced by `× 2` (same instruction size, stack-neutral, typed as `mul.i.v` exactly like the compiler emits it). The `ceil` function's reference chain is repaired accordingly (occurrence count −1, the previous link re-pointed past the removed call, per chapter).
- **speed** — in `obj_battlecontroller`'s Step event, `global.turntimer -= 1` becomes `-= 1.2`, so attack phases count down 1.2× faster. The restructured `&&` short-circuit around it is re-emitted in an equivalent shorter form, and the `turntimer` / `reset` variable reference chains are repaired (deltas re-pointed, per chapter).

Both components are stack-balanced, keep every branch target on an instruction boundary, and leave all FUNC/VARI reference chains walking exactly as in the original data (verified per chapter: only `scr_damage` and `obj_battlecontroller_Step_0` decode differently, and only in the patched region).

### The MODE selector (chapter select)

The chapter-select screen (`game.unx`) only gets one tiny patch: the 24-byte version line in the corner is baked to read `kantasu` or `kantasu [HARD]`, matching the selected mode (same length as the original line, so nothing moves). The **MODE** selector itself lives on the page side (`#drModeBar`): it's anchored to the game canvas, drawn in the game's own style (Press Start 2P, red SOUL, yellow highlight) and flips the mode instantly — including rewriting the version line *inside the running game's memory*, so the corner label changes the same frame you click, with no reload.

The chapter archives (`chapter*.unx.part*`) are untouched on disk; Normal Mode loads them unmodified.

> History: v5 briefly shipped a fully native in-game MODE choice (an injected `obj_ui_choice` installed by patched console-init code). It hung the game at boot, so v6 reverted it — the chapter-select data file is now byte-identical to the original apart from the 21-byte version-line text.

### Boot robustness (v7)

The boot path never fails silently any more. Every failure mode now either self-heals or shows a message on the loading screen:

- **Chunk downloads** are streamed with a 30-second stall watchdog, verified against `Content-Length`, and retried up to 3 times — a stalled or truncated chunk can no longer reach the engine (which used to hang forever at `LOADING ENGINE` with no error anywhere).
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
