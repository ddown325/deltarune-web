# DELTARUNE Web Port — Hard Mode Edition

An unofficial browser port of DELTARUNE (Chapters 1–5) with a built-in **HARD MODE**, SOUL-style loading screens and a single-file version.

## Play

- **Full site** — open `index.html` via GitHub Pages (or any static host)
  - Chapter select: `index.html` (or `?c=0`)
  - Jump straight to a chapter: `index.html?c=1` … `?c=5`
- **Single file** — download `onefile.html` alone, put it anywhere (even your desktop) and open it. It streams all game data from the Pages deployment, so it needs internet. Works great as a mobile home-screen app.

Controls: arrows to move, **Z / Enter** to confirm, **X** to cancel.

## HARD MODE

Pick your mode on the **chapter select screen** itself: the bottom menu row now has three real game options — **Quit · English · NORMAL/HARD**. The difficulty choice is a native menu entry drawn by the game: navigate to it with **left/right**, confirm with **Z**, and it flips between `NORMAL` and `HARD` right in the row (with the game's own selection sound and SOUL cursor). The yellow option with the SOUL next to it is the one you're on.

- You take **2× damage** from enemy attacks
- Enemy/boss attack phases run **1.2× as fast**
- The choice is saved in `localStorage` and remembered until you swap it
- The version line in the corner also shows the current mode (`kantasu` / `kantasu [HARD]`)

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

### The native MODE choice (chapter select)

The difficulty toggle is real game code, not an HTML overlay. `game.unx` (the chapter-select app) gets a patch set (`CHAPTER_SELECT_PATCHES` in the page source) that:

- redirects a dead console-only branch in `obj_CHAPTER_SELECT`'s create code so `obj_init_console` (Switch/PS4-only, never used on the web build) is spawned alongside the normal `obj_init_pc` initializer;
- overwrites `obj_init_console`'s create script (and two of its unused method bodies) with a small installer: it creates a genuine `obj_ui_choice` reading `NORMAL`/`HARD`, positions it in the footer row next to `Quit` and `English`, and appends it to the footer's `_choices` array — so the game's own left/right rotation, highlight, SOUL cursor and confirm sound all work on it for free;
- gives the choice a `trigger_event` handler that flips the mode flag, rewrites the choice text and reports the change through the game's own debug output (`drmode:hard` / `drmode:normal`), which the page picks up from the console and stores in `localStorage`.

Every patched byte region was rebuilt with full reference-chain surgery (VARI/FUNC occurrence counts, chain links, terminal string ids) and verified statically: all functions decode to their exact original lengths, all variable and function reference chains walk cleanly, no VM type violations, all jump targets land on instruction boundaries, and the patch table reproduces the intended patched file byte-for-byte.

The chapter archives (`chapter*.unx.part*`) are untouched on disk; Normal Mode loads them unmodified.

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
