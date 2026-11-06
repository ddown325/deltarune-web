#!/usr/bin/env python3
"""saveforge.py — craft Deltarune web-port saves for hazard testing.

Base saves: real save files (XtronXI/deltarune-saves, fetched from GitHub)
edited BYTE-EXACTLY (only whole lines replaced, CRLF + trailing-space value
formatting preserved) so the runner parses them identically to real saves.

Format (community-documented, verified against the real files):
  line 0: playerName, line 1: vesselName, lines 2-6: skip
  7-9: party (char indices), 10: money, 11: xp, 12: lv, 13: inv, 14: invc
  15: inDarkWorld, then 5 characters x {hp, maxhp, atk, def, mag, guts,
  weapon, armor1, armor2, style, 4x10 weaponStats, 12 spells}, battle stats,
  inventory, lightWorld stats, 2509 flags, then:
  [-3] plot, [-2] ROOM (canonical id = chapter*10000+n), [-1] time
"""
from __future__ import annotations
import struct
from pathlib import Path

RESEARCH = Path(__file__).resolve().parent / "research"
SAVES = RESEARCH / "saves"

# canonical room ids (community rooms.ts, tennaproject/tenna-editor)
ROOMS = {
    ("ch3", "room_dw_green_room"): 30134,
    ("ch4", "room_dw_churchb_extinguisher"): 40260,
    ("ch4", "room_dw_churchc_pretitan"): 40281,   # base save's original room
}

CHAR_BLOCK = {1: 10 + 4 * 8 + 12,   # V1: header + 4x8 weaponStats + 12 spells
              2: 10 + 4 * 10 + 12}  # V2: header + 4x10 weaponStats + 12 spells


def load_save(path: Path) -> list[bytes]:
    """Return save as list of raw line bodies (no CRLF)."""
    raw = path.read_bytes()
    parts = raw.split(b"\r\n")
    if parts and parts[-1] == b"":
        parts = parts[:-1]
    return parts


def save_bytes(lines: list[bytes]) -> bytes:
    return b"\r\n".join(lines) + b"\r\n"


def field_indices(fmt=2):
    """Return index dict for the given save format version."""
    if fmt == 1:
        nchars, ws, cb = 4, 8, CHAR_BLOCK[1]
    else:
        nchars, ws, cb = 5, 10, CHAR_BLOCK[2]
    idx = {
        "name": 0, "vessel": 1, "party0": 7, "party1": 8, "party2": 9,
        "money": 10, "xp": 11, "lv": 12, "inv": 13, "invc": 14, "dark": 15,
    }
    c = 16
    idx["chars"] = []
    for i in range(nchars):
        h = c + i * cb
        idx["chars"].append({
            "hp": h, "maxhp": h + 1, "atk": h + 2, "def": h + 3, "mag": h + 4,
            "guts": h + 5, "weapon": h + 6, "armor1": h + 7, "armor2": h + 8,
            "style": h + 9, "stats": h + 10, "spells": h + 10 + 4 * ws,
        })
    c += nchars * cb
    idx["after_chars"] = c
    # tail fields are relative to the end
    idx["plot-3"] = -3
    idx["room-2"] = -2
    idx["time-1"] = -1
    return idx


def num(v) -> bytes:
    """Format a number like the game does (trailing space, e+08 style)."""
    if isinstance(v, float) and v >= 1e6:
        s = f"{v:e}"
        # 1.234e+06 style (two-digit exponent)
        mant, exp = s.split("e")
        sign = exp[0]
        digs = exp[1:]
        if len(digs) == 1:
            digs = "0" + digs
        s = f"{mant}e{sign}{digs}"
    else:
        s = str(int(v)) if float(v).is_integer() else str(v)
    return (s + " ").encode()


def set_room(lines: list[bytes], room_id: int) -> list[bytes]:
    out = list(lines)
    out[-2] = num(room_id)
    return out


def set_dark(lines: list[bytes], on=True) -> list[bytes]:
    out = list(lines)
    out[15] = num(1 if on else 0)
    return out


def set_party(lines: list[bytes], party=(1, 2, 3)) -> list[bytes]:
    out = list(lines)
    out[7], out[8], out[9] = num(party[0]), num(party[1]), num(party[2])
    return out


def set_hp(lines: list[bytes], hp: int, maxhp: int = None, fmt=2) -> list[bytes]:
    """Set every character's hp (and optionally maxhp)."""
    out = list(lines)
    idx = field_indices(fmt)
    for c in idx["chars"]:
        out[c["hp"]] = num(hp)
        if maxhp is not None:
            out[c["maxhp"]] = num(maxhp)
    return out


def get_hp(lines: list[bytes], fmt=2):
    idx = field_indices(fmt)
    return [int(float(lines[c["hp"]])) for c in idx["chars"]]


def get_room(lines: list[bytes]) -> int:
    return int(float(lines[-2]))


def craft(chapter: int, room_id: int, hp: int | None = None,
          maxhp: int | None = None, party=(1, 2, 3), dark=True,
          base: Path | None = None) -> bytes:
    """Craft a filech{chapter}_0 payload for the target room."""
    if base is None:
        base = SAVES / f"filech{chapter}_0"
        if not base.exists():
            raise FileNotFoundError(f"no base save for ch{chapter}: {base}")
    lines = load_save(base)
    lines = set_room(lines, room_id)
    lines = set_dark(lines, dark)
    lines = set_party(lines, party)
    if hp is not None:
        lines = set_hp(lines, hp, maxhp)
    return save_bytes(lines)


def dr_ini(chapter: int, room_id: int, name: str = "XTRON",
           lv: int = 2, love: int = 1, time: float = 1.0e6) -> bytes:
    """Craft dr.ini (file-select metadata, GM ini format: quoted values)."""
    def real(v):
        return f'"{float(v):.6f}"'
    rows = [
        f"[slot0]",
        f"Name=\"{name}\"",
        f"Level={real(lv)}",
        f"Love={real(love)}",
        f"Time={real(time)}",
        f"Date={real(45000.0)}",
        f"Room={real(room_id)}",
        f"InitLang={real(0)}",
        f"UraBoss={real(0)}",
        f"Version=\"0\"",
    ]
    return ("\r\n".join(rows) + "\r\n").encode()


if __name__ == "__main__":
    for ch, room in ((3, 30134), (4, 40260)):
        b = craft(ch, room, hp=90, maxhp=90)
        out = RESEARCH / "saves" / f"crafted_filech{ch}_0"
        out.write_bytes(b)
        ls = load_save(out)
        print(f"crafted {out} ({len(b)}B, {len(ls)} lines): "
              f"room={get_room(ls)} hp={get_hp(ls)} party={[ls[i] for i in (7,8,9)]} dark={ls[15]}")
    di = dr_ini(4, 40260)
    (RESEARCH / "saves" / "crafted_dr.ini").write_bytes(di)
    print("dr.ini:\n" + di.decode())
