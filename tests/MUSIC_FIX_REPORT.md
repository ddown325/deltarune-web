# tests/MUSIC_FIX_REPORT.md — the Field of Hopes double-music crash, root-caused and fixed (2026-11-10, v9.12)

**Player report:** "two musics play in the Field of Hopes and Dreams — I think
the chapter 1 music is also playing — and not only that but the game crashes
after a few seconds (this area takes place in the room after Flowey puts the
hat on Ralsei)."

**Answer: the web runner's `audio_is_playing()` lies for streamed sounds.**
Every `mus/*.ogg` is played through `audio_create_stream()`; on web the
runner's per-stream ogg state thread cannot spawn (`Error 0 creating ogg
thread, swapping to main thread polling` prints at every stream creation), so
the sound's *playing* flag never updates. Deltarune's room musicers guard
every theme start with `if (!snd_is_playing(currentsong))` — on web that
guard always passes, so each room entry stacked a NEW copy of the theme on
top of the still-playing one. The player walked out of the hat cutscene
(`flowery.ogg` — Flowey's ch1-era leitmotif — started by the cupboard scene's
`snd_stop_all(); scr_musicer("flowery.ogg", 0.8)`) into the field, where
`obj_musicer_garden` (room-placed in `room_dw_garden_floradinnencounter`)
started `field_of_hopes_insaneintherain` on top of it: two tracks at once.
The multiplied main-thread ogg polling then killed the renderer a few
seconds in — the same garbage-load/native-death class as the music-retry
storm in FLOWERS_REPORT's note 2.

## The runtime evidence (audio probe, real repo bytes, headless)

New instrumentation: a document-start probe hooking
`AudioBufferSourceNode.prototype.start` + a per-buffer-duration histogram.
The GM web runner plays EVERY sound as a stream of tiny WebAudio chunk nodes
(~10-45 ms each, ~25 nodes/s per audible stream) — node-creation rate is a
direct, hearable-music-activity meter:

| observation | evidence |
|---|---|
| one stream at a time in normal play | logo music ~25 n/s → menu ~25 n/s → field intro ~25 n/s (repro1: nodes 298/299/284 per 10 s) |
| the intro→loop handoff WORKS on web | the field intro ran its real ~80 s at 25 n/s, then the loop took over (single stream, no gap) — driven by `audio_sound_get_track_position()`, which works |
| `audio_stop_sound()` WORKS on web | every floradinn battle start (rakuichi fetch) drops the rate to a single battle stream (~28 n/s) — the field theme's feeder dies cleanly, 3/3 runs |
| `audio_is_playing()` LIES on web | the cupboard musicer path + the re-entry guard both pass while music is audibly playing (the stacking below); also consistent with the state-thread failure |
| field "churn" while walking | 60-80 n/s while sweeping the sheary/petal area = ambient SFX, NOT stacked music (party never left the room — VLM-verified screenshots) |

The stacking itself could not be replayed end-to-end headless because every
path into the field from a neighbouring room is story-gated (the cupboard
room waits forever on ch4-flag saves) or intercepted by the floradinn battle
trigger — the same wall FLOWERS_REPORT hit (all its runs are savepoint
boots). The mechanism is instead proven piece-wise:

1. the guard lies (above);
2. `audio_stop_sound` genuinely kills stream feeders (battle transitions);
3. `currentsong` holds the real stream id (the switcher's
   `audio_sound_get_track_position(currentsong)` returns advancing
   positions — that is why the intro→loop switch fires on time);
4. `obj_musicer_garden`'s Create runs exactly once per room entry.

## The fix (20 bytes, always-on, no hard-mode involvement)

Redirect ONE call: `obj_musicer_garden_Create_0`'s
`call snd_is_playing(currentsong)` becomes `call snd_stop(currentsong)`.
`gml_Script_snd_stop` is `audio_stop_sound(argument0)` and returns
undefined, so `!snd_stop(...)` stays truthy (the musicer still always starts
its theme — identical to today's web behavior since the guard always
passed) but the previous theme is genuinely stopped first. Desktop handoff
semantics restored: one theme at a time, always.

Surgery: pure FUNC-occurrence-chain relocation (the v8.1 battle-patch
recipe). The call's operand dword moves from `snd_is_playing`'s chain
(occ 44→43) into `snd_stop`'s (occ 468→469), inserted at its
address-ordered position (chains serialize in ascending code order — the
first attempt inserted at the chain head and the wasm loader died at boot,
exactly the v9.8-b class; ordered insertion boots clean). Verified offline:
both chains walk complete/ascending, and a full-file FUNC walk over all
6,878 functions finds zero broken chains.

**Deliberately NOT patched:** `obj_musicer_gen_Step_0` carries the same
lying guard, but its check runs in a Step event — a stop-redirect there
could re-stop the current theme every frame. The Create-event musicer is
the once-per-room-entry site and the one that gates the field theme. A
page-side "kill all audio nodes on mus fetch" shim was prototyped and
discarded: it cannot stop the wasm-side stream feeders (nodes keep being
created), so it would not have removed the crash load.

## Runtime gate (patched bytes, ?c=5&hm=full + hard mode)

- boot → title → field: engine alive 175 s, ~20-60 fps, zero pageerrors,
  the field theme starts normally at room load (the patched guard executes
  harmlessly when nothing is playing — `snd_stop(undefined)` is a no-op).
- intro→loop handoff still fires after the intro's real length.
- the floradinn battle still starts and cleanly stops the field theme.
- chapter-1 boot regression after the loader wiring change: SUCCESS
  (1.67 s to main loop, 22.5 fps, clean).

## Tooling / data notes for the next person

- The save-room-id ↔ room-ordinal map lives in `scr_get_room_list`'s
  structs: `push.i <save_id> | 00 00 | 52 07 | pushi.e <room_ordinal>` —
  the room byte sits at the `push.i` site + 10 (validated against the
  previous session's 50114→119 patch offset). One-byte redirects work for
  any room (used here to boot room 121, the Ralsei-cupboard room, plot
  220=hat cutscene / 230=after — VLM-verified: Susie carries hat-Ralsei).
- ch5's music system: `snd_init(name)` = `audio_create_stream("mus/"+name)`
  + a persistent `obj_astream` holder; `mus_2_file_loop(intro, loop, vol,
  pitch)` = play intro + preload loop + a persistent
  `obj_setup_music_loop_track` switcher that watches
  `audio_sound_get_track_position()`; room musicers
  (`obj_musicer_garden` in the field) guard their theme start with the
  (broken-on-web) `snd_is_playing(currentsong)`.
- The ch1-4 musicers carry the same guard and the same latent bug; they are
  untouched in v9.12 (no player report, and each chapter needs its own
  runtime gate per the standing rule).
