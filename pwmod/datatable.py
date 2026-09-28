"""Read and edit a cooked UE4 DataTable (.uasset + .uexp pair) as used by Project Wingman.

Only what the build needs: tagged-property parsing, in-place bool/int edits, rewriting a
row's top-level array, and appending a name to the name map. Every size-changing edit fixes
the property tag size, the export's SerialSize/SerialOffset and the header offsets, then
re-parses so later edits see fresh offsets.
"""
import re
import struct

NATIVE_STRUCTS = {'Vector', 'Rotator', 'Guid', 'LinearColor', 'Color', 'Vector2D', 'Quat',
                  'IntPoint', 'Box', 'DateTime', 'Timespan', 'SoftObjectPath',
                  'GameplayTagContainer', 'Vector4'}

# Blueprint struct members are named Field_<n>_<GUID>; strip that so paths are stable.
short = lambda p: re.sub(r'_\d+_[0-9A-F]{8,}', '', p)


class _R:
    def __init__(self, b, p=0):
        self.b, self.p = b, p

    def i32(self):
        v = struct.unpack_from('<i', self.b, self.p)[0]; self.p += 4; return v

    def u8(self):
        v = self.b[self.p]; self.p += 1; return v

    def raw(self, n):
        v = self.b[self.p:self.p + n]; self.p += n; return v

    def fstr(self):
        n = self.i32()
        if n == 0: return ''
        if n < 0: return self.raw(-n * 2)[:-2].decode('utf-16-le')
        return self.raw(n)[:-1].decode('utf-8', 'replace')


def fstring(v):
    b = v.encode() + b'\0'
    return struct.pack('<i', len(b)) + b


def summary(ua):
    """FPackageFileSummary values plus the byte position of each fixed-size field."""
    r = _R(ua); s = {}; pos = {}

    def f(name, fmt):
        pos[name] = r.p
        s[name] = struct.unpack_from(fmt, ua, r.p)[0]
        r.p += struct.calcsize(fmt)
        return s[name]

    f('tag', '<i'); f('legacy', '<i'); f('ue3', '<i'); f('ue4', '<i'); f('lic', '<i')
    r.raw(20 * f('ncustom', '<i'))
    f('TotalHeaderSize', '<i'); r.fstr(); f('PackageFlags', '<I')
    f('NameCount', '<i'); f('NameOffset', '<i')
    if s['ue4'] >= 516 and not (s['PackageFlags'] & 0x80000000):
        r.fstr()  # localization id
    f('GatherCount', '<i'); f('GatherOffset', '<i')
    f('ExportCount', '<i'); f('ExportOffset', '<i'); f('ImportCount', '<i'); f('ImportOffset', '<i')
    f('DependsOffset', '<i'); f('SoftRefCount', '<i'); f('SoftRefOffset', '<i')
    f('SearchableNamesOffset', '<i'); f('ThumbnailTableOffset', '<i')
    r.raw(16); ng = f('GenCount', '<i'); s['GenPos'] = r.p; r.raw(8 * ng)
    for _ in range(2):  # saved-by / compatible-with engine versions
        r.raw(10); r.fstr()
    f('CompressionFlags', '<I'); f('CompressedChunks', '<i'); f('PackageSource', '<I')
    for _ in range(f('AddPkgCount', '<i')): r.fstr()
    f('AssetRegistryDataOffset', '<i'); f('BulkDataStartOffset', '<q')
    f('WorldTileInfoDataOffset', '<i')
    r.raw(4 * f('ChunkCount', '<i'))
    f('PreloadDependencyCount', '<i'); f('PreloadDependencyOffset', '<i')
    return s, pos


# Header fields that point past the name map and move when a name is appended.
_AFTER_NAMES = ('ImportOffset', 'ExportOffset', 'DependsOffset', 'AssetRegistryDataOffset',
                'PreloadDependencyOffset', 'TotalHeaderSize')


class DataTable:
    def __init__(self, uasset, uexp):
        self.ua = bytes(uasset)
        self.ue = bytes(uexp)
        self._parse()

    # ---------- parsing
    def _parse(self):
        s, _ = summary(self.ua)
        if s['ExportCount'] != 1:
            raise ValueError('expected a single-export DataTable package')
        r = _R(self.ua, s['NameOffset'])
        self.names, self.name_entries = [], []
        for _ in range(s['NameCount']):
            st = r.p
            self.names.append(r.fstr()); r.raw(4)  # two 16-bit hashes
            self.name_entries.append(self.ua[st:r.p])
        r.p = s['ImportOffset']
        self.imports = []
        for _ in range(s['ImportCount']):
            r.raw(8 + 8 + 4)
            on = r.i32(); r.i32()
            self.imports.append(self.names[on])
        self.leaves, self.rows, self.tags = [], [], {}
        r = _R(self.ue)
        self._walk(r, '<obj>', '<obj>', len(self.ue))
        r.i32()  # no-guid flag
        for _ in range(r.i32()):
            rn = self._fname(r)
            self._walk(r, '', rn, len(self.ue), top=True)
            self.rows.append(rn)
        self.index = {(l[0], short(l[1])): l for l in self.leaves}

    def _fname(self, r):
        i, n = r.i32(), r.i32()
        return self.names[i] if n == 0 else f'{self.names[i]}_{n - 1}'

    def _walk(self, r, path, row, end, top=False):
        while r.p < end:
            name = self._fname(r)
            if name == 'None':
                return
            typ = self._fname(r)
            size_pos = r.p
            size = r.i32(); aidx = r.i32(); extra = None
            if typ == 'StructProperty': extra = self._fname(r); r.raw(16)
            elif typ == 'BoolProperty': boolpos = r.p; extra = r.u8()
            elif typ in ('ByteProperty', 'EnumProperty', 'ArrayProperty', 'SetProperty'):
                extra = self._fname(r)
            elif typ == 'MapProperty': extra = (self._fname(r), self._fname(r))
            if r.u8(): r.raw(16)
            key = f'{path}.{name}' + (f'[{aidx}]' if aidx else '')
            vstart = r.p
            if top:
                self.tags[(row, short(key))] = (typ, size_pos, vstart, size)
            if typ == 'BoolProperty':
                self.leaves.append((row, key, typ, boolpos, 1, extra))
            else:
                self._value(r, typ, extra, key, row, vstart + size, size)
            r.p = vstart + size

    def _scalar(self, r, typ, extra, size):
        if typ == 'IntProperty': return r.i32()
        if typ == 'FloatProperty': return struct.unpack('<f', r.raw(4))[0]
        if typ == 'NameProperty': return self._fname(r)
        if typ in ('ByteProperty', 'EnumProperty'):
            if typ == 'ByteProperty' and (extra == 'None' or size == 1): return r.u8()
            return self._fname(r)
        if typ == 'StrProperty': return r.fstr()
        if typ == 'ObjectProperty':
            i = r.i32()
            return ('import:' + (self.imports[-i - 1] if i < 0 else str(i))) if i else None
        if typ == 'SoftObjectProperty':
            a = self._fname(r); b = r.fstr(); return a + (':' + b if b else '')
        return 'RAW:' + r.raw(size).hex()

    def _value(self, r, typ, extra, key, row, end, size):
        start = r.p
        if typ == 'StructProperty':
            if extra in NATIVE_STRUCTS:
                self.leaves.append((row, key, extra, start, size, r.raw(size).hex())); return
            self._walk(r, key, row, end); return
        if typ in ('ArrayProperty', 'SetProperty'):
            if typ == 'SetProperty': r.i32()
            n = r.i32()
            self.leaves.append((row, key + '#len', 'len', r.p - 4, 4, n))
            if extra == 'StructProperty':
                self._fname(r); self._fname(r); r.i32(); r.i32(); sname = self._fname(r)
                r.raw(16); r.u8()
                for k in range(n):
                    if sname in NATIVE_STRUCTS:
                        sz = (size - (r.p - start)) // max(n, 1)
                        self.leaves.append((row, f'{key}[{k}]', sname, r.p, sz, r.raw(sz).hex()))
                    else:
                        self._walk(r, f'{key}[{k}]', row, end)
                return
            for k in range(n):
                s0 = r.p
                v = r.u8() if extra == 'BoolProperty' else \
                    self._scalar(r, extra, None, 1 if extra == 'ByteProperty' else 0)
                self.leaves.append((row, f'{key}[{k}]', extra, s0, r.p - s0, v))
            return
        if typ == 'MapProperty':
            self.leaves.append((row, key, 'Map', start, size, r.raw(size).hex())); return
        v = self._scalar(r, typ, extra, size)
        self.leaves.append((row, key, typ, start, r.p - start, v))

    # ---------- queries
    def values(self):
        """{(row, short path): value} for every leaf."""
        return {k: l[5] for k, l in self.index.items()}

    def get(self, row, path):
        return self.index[(row, path)][5]

    # ---------- edits
    def set(self, row, path, value):
        """In-place edit of a bool or int leaf."""
        _, _, typ, off, _, _ = self.index[(row, path)]
        ue = bytearray(self.ue)
        if typ == 'BoolProperty': ue[off] = int(value)
        elif typ == 'IntProperty': struct.pack_into('<i', ue, off, int(value))
        else: raise ValueError(f'cannot set {typ} in place ({row} {path})')
        self.ue = bytes(ue)
        self._parse()

    def set_array(self, row, prop, values, elem):
        """Replace a row's top-level ArrayProperty value. elem: 'int' | 'str' | 'name'."""
        typ, size_pos, vstart, size = self.tags[(row, '.' + prop)]
        if typ != 'ArrayProperty':
            raise ValueError(f'{row}.{prop} is {typ}, not an array')
        enc = {'int': lambda v: struct.pack('<i', v), 'str': fstring,
               'name': lambda v: struct.pack('<ii', self.name_index(v), 0)}[elem]
        data = struct.pack('<i', len(values)) + b''.join(enc(v) for v in values)
        ue = bytearray(self.ue)
        ue[vstart:vstart + size] = data
        struct.pack_into('<i', ue, size_pos, len(data))
        self.ue = bytes(ue)
        self._resize_export(len(data) - size)

    def set_str(self, row, path, value):
        """Replace a StrProperty that is an element of a row's top-level array."""
        prop = path.split('[')[0]
        _, _, typ, off, size, _ = self.index[(row, path)]
        if typ != 'StrProperty':
            raise ValueError(f'{row} {path} is {typ}')
        new = fstring(value)
        _, size_pos, _, _ = self.tags[(row, prop)]
        ue = bytearray(self.ue)
        ue[off:off + size] = new
        struct.pack_into('<i', ue, size_pos, struct.unpack_from('<i', ue, size_pos)[0] + len(new) - size)
        self.ue = bytes(ue)
        self._resize_export(len(new) - size)

    def set_name(self, row, path, value):
        _, _, typ, off, _, _ = self.index[(row, path)]
        if typ != 'NameProperty':
            raise ValueError(f'{row} {path} is {typ}')
        ue = bytearray(self.ue)
        struct.pack_into('<ii', ue, off, self.name_index(value), 0)
        self.ue = bytes(ue)
        self._parse()

    def name_index(self, name):
        return self.names.index(name)

    def add_name(self, entry):
        """Append a serialized name-map entry (FString + 4 hash bytes) taken from another
        package that already contains the name. No-op if the name is already present."""
        name = _R(entry).fstr()
        if name in self.names:
            return
        s, pos = summary(self.ua)
        ua = bytearray(self.ua)
        ua[s['ImportOffset']:s['ImportOffset']] = entry  # name map ends where imports begin
        nd = len(entry)
        struct.pack_into('<i', ua, pos['NameCount'], s['NameCount'] + 1)
        for k in _AFTER_NAMES:
            if s[k]:
                struct.pack_into('<i', ua, pos[k], s[k] + nd)
        struct.pack_into('<q', ua, pos['BulkDataStartOffset'], s['BulkDataStartOffset'] + nd)
        g = s['GenPos'] + 8 * (s['GenCount'] - 1)
        ec, nc = struct.unpack_from('<ii', ua, g)
        struct.pack_into('<ii', ua, g, ec, nc + 1)
        so = s['ExportOffset'] + nd + 28  # class, super, template, outer, FName, flags
        ssize, soff = struct.unpack_from('<qq', ua, so)
        struct.pack_into('<qq', ua, so, ssize, soff + nd)
        self.ua = bytes(ua)
        self._parse()

    def name_entry(self, name):
        return self.name_entries[self.names.index(name)]

    def _resize_export(self, grow):
        s, pos = summary(self.ua)
        ua = bytearray(self.ua)
        struct.pack_into('<q', ua, pos['BulkDataStartOffset'], s['BulkDataStartOffset'] + grow)
        so = s['ExportOffset'] + 28
        ssize, soff = struct.unpack_from('<qq', ua, so)
        struct.pack_into('<qq', ua, so, ssize + grow, soff)
        self.ua = bytes(ua)
        self._parse()

    def check(self):
        """Raise if the header disagrees with the data."""
        s, _ = summary(self.ua)
        ssize, soff = struct.unpack_from('<qq', self.ua, s['ExportOffset'] + 28)
        assert soff == s['TotalHeaderSize'] == len(self.ua), 'export offset / header size mismatch'
        assert ssize == len(self.ue) - 4, 'export SerialSize mismatch'
        assert s['BulkDataStartOffset'] == len(self.ua) + len(self.ue) - 4, 'bulk data offset mismatch'
