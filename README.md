# DELTARUNE Web Port — Hard Mode Edition

An unofficial browser port of DELTARUNE (Chapters 1–5) with a built-in **HARD MODE**, SOUL-style loading screens and a single-file version.

## Play

- **Full site** — open `index.html` via GitHub Pages (or any static host)
  - Chapter select: `index.html` (or `?c=0`)
  - Jump straight to a chapter: `index.html?c=1` … `?c=5`
- **Single file** — download `onefile.html` alone, put it anywhere (even your desktop) and open it. It streams all game data from the Pages deployment, so it needs internet. Works great as a mobile home-screen app.

Controls: arrows to move, **Z / Enter** to confirm, **X** to cancel. Fullscreen, save management and the difficulty toggle are in the button bar.

## What's in this port

- **All five chapters** in the browser, with a Save File Manager (export / import saves as ZIP)
- **HARD MODE** — a persistent difficulty toggle inside the Save File Manager (or the `MODE` badge button):
  - You take **2× damage** from enemy attacks
  - Enemy/boss attack phases run **1.2× as fast**
  - The choice is saved and remembered until you swap it — no prompt when starting a chapter
  - Turn it off and the game will call you a **COWARD.** (It's waiting for you.)
- **SOUL loading screens** — while a chapter downloads you get the chapter title, a pixel SOUL that fills up with real download progress, a live percentage, stage messages (`LOADING DATA` → `APPLYING HARD MODE` → `LOADING ENGINE` → `STARTING GAME`) and a **RETRY** button if the connection drops
- **Quit button** with a proper quit screen
- Custom site favicon

## How HARD MODE works (technical)

Hard Mode is implemented as byte-level patches to the GameMaker data files, applied in the browser right before the game boots:

- `scr_damage`: damage taken by the party is doubled (`tdamage = (tdamage - defense) × 2`)
- Every constant that starts or extends an enemy attack phase (`global.turntimer = N`) is scaled to `round(N / 1.2)`, so each phase ends after N/1.2 frames instead of N — attack phases run **1.2× as fast**. The countdown itself stays a plain integer −1 per frame: no instructions move, no branches change, no reference chains are touched.

The patch data is embedded in `index.html` / `onefile.html`; the original chapter archives (`chapter*.unx.part*`) are untouched. Normal Mode loads the unmodified data.

## Running your own copy

Everything is static — no build step, no server code:

1. Clone / download this repo
2. Serve it from any static host (GitHub Pages, itch.io ZIP upload, a local `python -m http.server`, …)
3. Open `index.html`

Repo layout: `index.html` + `onefile.html` (the site), `game.unx.part001` (chapter-select launcher), `chapter*.unx.part*` (chapter data), `runner*` (the GameMaker web runtime), `chapter*_windows/`, `mus/`, `vid/` (chapter assets).

## Credits

- **DELTARUNE** by Toby Fox — [deltarune.com](https://deltarune.com)
- Web port by **Kantasu**
- The GameMaker-to-web loader engineering this project started from was built by **Mollomm1**
- *Press Start 2P* font by CodeMan38 — SIL Open Font License 1.1

*Unofficial fan project. DELTARUNE and all its assets belong to Toby Fox.*
