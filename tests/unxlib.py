#!/usr/bin/env python3
"""unxlib.py — shared GameMaker 2022.3 .unx loader (step-4 grade, real files)."""
import glob
import re
import struct

u32 = lambda d, o: struct.unpack_from("<I", d, o)[0]
i32 = lambda d, o: struct.unpack_from("<i", d, o)[0]
u16 = lambda d, o: struct.unpack_from("<H", d, o)[0]
i16 = lambda d, o: struct.unpack_from("<h", d, o)[0]


class Unx:
    def __init__(self, drweb, chapter):
        parts = sorted(glob.glob("%s/chapter%d.unx.part*" % (drweb, chapter)),
                       key=lambda p: int(re.search(r"part(\d+)$", p).group(1)))
        assert parts, "no parts for chapter %d" % chapter
        self.parts = parts
        self.data = b"".join(open(p, "rb").read() for p in parts)
        self.chapter = chapter
        d = self.data
        assert d[:4] == b"FORM", "not a FORM"
        self.form_len = u32(d, 4)
        self.chunks, self.order = {}, []
        off = 8
        while off + 8 <= len(d):
            nm = d[off:off + 4].decode("latin1")
            sz = u32(d, off + 4)
            self.chunks[nm] = (off + 8, sz)
            self.order.append(nm)
            off += 8 + sz
        self.walk_end = off
        # STRG
        sb, ss = self.chunks["STRG"]
        self.strg_count = u32(d, sb)
        self.strg_table = [u32(d, sb + 4 + 4 * i) for i in range(self.strg_count)]
        self.str_strings = []
        for p in self.strg_table:
            ln = u32(d, p)
            self.str_strings.append(d[p + 4:p + 4 + ln].decode("utf-8", "replace"))
        self.charoff2idx = {p + 4: i for i, p in enumerate(self.strg_table)}
        # string data region (after table) end = chunk end
        self.strg_base, self.strg_end = sb, sb + ss
        # VARI
        vb, vs = self.chunks["VARI"]
        self.vari_c = struct.unpack_from("<III", d, vb)
        self.varrecs = []
        p = vb + 12
        end = vb + vs - 4  # last 4 bytes = pad
        while p + 20 <= end:
            nm, inst, vid, occ, first = struct.unpack_from("<IiIII", d, p)
            self.varrecs.append(dict(off=p, nmoff=nm, inst=inst, vid=vid,
                                     occ=occ, first=first,
                                     name=self._str(nm)))
            p += 20
        # FUNC
        fb, fs = self.chunks["FUNC"]
        fc = u32(d, fb)
        self.funcrecs = []
        p = fb + 4
        for i in range(fc):
            nm, occ, first = struct.unpack_from("<III", d, p)
            self.funcrecs.append(dict(idx=i, off=p, nmoff=nm, occ=occ, first=first,
                                      name=self._str(nm)))
            p += 12
        self.func_count = fc
        # CodeLocals (FUNC part 2)
        self.codelocals = []
        if p + 4 <= fb + fs:
            clc = u32(d, p)
            p += 4
            for _ in range(clc):
                n, nameco = struct.unpack_from("<II", d, p)
                p += 8
                locs = []
                for _ in range(n):
                    a, b2 = struct.unpack_from("<II", d, p)
                    p += 8
                    locs.append((a, b2, self._str(b2)))
                self.codelocals.append((self._str(nameco), locs))
        # CODE
        cb, cs = self.chunks["CODE"]
        cc = u32(d, cb)
        self.code_entries = []
        for i in range(cc):
            ptr = u32(d, cb + 4 + 4 * i)
            nm, blen, B, C, E = struct.unpack_from("<IiIiI", d, ptr)
            self.code_entries.append(dict(idx=i, ptr=ptr, nmoff=nm,
                                           blen=blen, B=B, C=C, E=E,
                                           start=ptr + C, end=ptr + C + blen,
                                           name=self._str(nm)))
        self.code_by_name = {}
        for e in self.code_entries:
            self.code_by_name.setdefault(e["name"], e)
        # SPRT — sprite entries: count, ptrs, each entry begins with u32
        # name charoff (GM sprite resource layout).
        self.sprite_name_charoffs = set()
        if "SPRT" in self.chunks:
            pb, ps = self.chunks["SPRT"]
            cnt = u32(d, pb)
            ptrs = [u32(d, pb + 4 + 4 * i) for i in range(cnt)]
            for q in ptrs:
                if pb <= q + 4 <= pb + ps:
                    self.sprite_name_charoffs.add(u32(d, q))

    def _str(self, charoff):
        i = self.charoff2idx.get(charoff)
        return self.str_strings[i] if i is not None else "<bad:%#x>" % charoff

    def stridx_of(self, charoff):
        return self.charoff2idx.get(charoff)

    def string_spanning(self, addr):
        """Return (idx, start_charoff, lenfield_off, value) of the STRG string
        whose [u32 len][chars][NUL][pad] record contains addr, else None."""
        d = self.data
        for i, p in enumerate(self.strg_table):
            ln = u32(d, p)
            if p <= addr < p + 4 + ln + 4:
                return (i, p + 4, p, self.str_strings[i])
        return None


# ---------------------------------------------------------------- disassembler
SIZES = {0x00: 2, 0x01: 2, 0x05: 6, 0x07: 2, 0x08: 2, 0x09: 2, 0x0A: 2, 0x0B: 2,
         0x0C: 2, 0x0D: 2, 0x0E: 2, 0x0F: 2, 0x10: 2, 0x11: 2, 0x12: 2, 0x13: 2,
         0x14: 2, 0x15: 4, 0x45: 6, 0x7D: 4, 0x84: 4, 0x86: 4, 0x99: 6, 0x9A: 4,
         0x9C: 2, 0x9D: 2, 0x9E: 4, 0xB6: 4, 0xB7: 4, 0xB8: 4, 0xB9: 4, 0xBA: 4,
         0xBB: 4, 0xC0: 6, 0xC1: 6, 0xC2: 6, 0xC3: 6, 0xD9: 6, 0xFF: 2}
BINOPS = {0x08: "mul", 0x09: "div", 0x0A: "rem", 0x0B: "mod", 0x0C: "add",
          0x0D: "sub", 0x0E: "and", 0x0F: "or", 0x10: "xor", 0x11: "neg",
          0x12: "not", 0x13: "shl", 0x14: "shr"}
TY = {0: "d", 1: "f", 2: "i", 3: "l", 4: "s", 5: "v", 6: "?", 0xF: "e"}


def decode(data, addr, names=None):
    """Decode one instruction at addr. Returns dict."""
    extra, op = data[addr], data[addr + 1]
    sz = SIZES.get(op)
    if sz is None:
        return dict(addr=addr, extra=extra, op=op, sz=2, raw=data[addr:addr + 2],
                    text="??op %02x.%02x" % (extra, op))
    raw = data[addr:addr + sz]
    t = "op%02x.%02x" % (op, extra)
    if op in (0xC0, 0xC1, 0xC2, 0xC3, 0x05):
        dw = u32(data, addr + 2)
        rid, tc = dw & 0xFFFF, (dw >> 24) & 0xFF
        nm = (names or {}).get(rid, "ref%d" % rid)
        kind = {0xC0: "push.v", 0xC1: "push.1", 0xC2: "push.gl",
                0xC3: "push.arr", 0x05: "push.bltn"}[op]
        t = "%-8s %s [r=%d tc=%02x]" % (kind, nm, rid, tc)
    elif op == 0x45:
        dw = u32(data, addr + 2)
        rid, tc = dw & 0xFFFF, (dw >> 24) & 0xFF
        nm = (names or {}).get(rid, "ref%d" % rid)
        t = "pop.%s.%s  %s [r=%d tc=%02x]" % (TY.get(extra & 0xF, "?"),
                                              TY.get(extra >> 4, "?"), nm, rid, tc)
    elif op == 0x84:
        t = "pushi.%s  %d" % (TY.get(extra >> 4, "?"), i16(data, addr + 2))
    elif op in (0xB6, 0xB7, 0xB8):
        off = i16(data, addr + 2)
        nm = {0xB6: "b", 0xB7: "bt", 0xB8: "bf"}[op]
        t = "%-8s -> %#x" % (nm, addr + sz + 2 * off)
    elif op == 0xD9:
        dw = u32(data, addr + 2)
        t = "call     link=%#06x hi=%#06x" % (dw & 0xFFFF, dw >> 16)
    elif op == 0x9E:
        t = "callv    %d" % i16(data, addr + 2)
    elif op == 0x15:
        t = "cmp.%s.%s code=%#06x" % (TY.get(extra & 0xF, "?"), TY.get(extra >> 4, "?"),
                                      u16(data, addr + 2))
    elif op == 0x07:
        t = "conv.%s.%s" % (TY.get(extra & 0xF, "?"), TY.get(extra >> 4, "?"))
    elif op in BINOPS:
        t = "%-8s .%s.%s" % (BINOPS[op], TY.get(extra & 0xF, "?"), TY.get(extra >> 4, "?"))
    elif op == 0xFF:
        t = "break    (%d)" % extra
    elif op in (0x00, 0x01):
        t = "marker   %d" % extra
    elif op == 0x9D:
        t = "exit"
    elif op == 0x9C:
        t = "ret"
    return dict(addr=addr, extra=extra, op=op, sz=sz, raw=raw, text=t)


def disasm_range(data, start, end, names=None):
    out, i = [], start
    while i < end:
        ins = decode(data, i, names)
        out.append(ins)
        i += ins["sz"]
    return out
