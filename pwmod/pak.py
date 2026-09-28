"""Minimal Unreal Engine 4 .pak reader and writer.

Reads: legacy index (v3-v9) and the v10/v11 path-hash + full-directory index used by the
current Project Wingman build. Entries must be unencrypted; zlib compression is supported
for the legacy versions the mods use.
Writes: v3, uncompressed, mount point ../../../ (the format the original mods ship in).
"""
import hashlib
import io
import struct
import zlib

MAGIC = 0x5A6F12E1


def _fstr(f):
    n = struct.unpack('<i', f.read(4))[0]
    if n == 0:
        return ''
    if n < 0:
        return f.read(-n * 2)[:-2].decode('utf-16-le')
    return f.read(n)[:-1].decode('utf-8', 'replace')


def _footer(f):
    f.seek(0, 2)
    size = f.tell()
    f.seek(max(0, size - 300))
    tail = f.read()
    i = tail.rfind(struct.pack('<I', MAGIC))
    if i < 0:
        raise ValueError('not a pak file (no footer magic)')
    _, ver, off, sz = struct.unpack('<IIqq', tail[i:i + 24])
    return ver, off, sz


def _legacy_entry(f):
    off, csize, usize, comp = struct.unpack('<qqqi', f.read(28))
    f.read(20)  # sha1
    blocks = []
    if comp:
        n = struct.unpack('<i', f.read(4))[0]
        blocks = [struct.unpack('<qq', f.read(16)) for _ in range(n)]
    f.read(1)  # encrypted flag
    f.read(4)  # compression block size
    return dict(offset=off, usize=usize, comp=comp, blocks=blocks)


def _encoded_entry(enc, p):
    bits = struct.unpack_from('<I', enc, p)[0]
    p += 4
    comp = (bits >> 23) & 0x3f
    if (bits >> 22) & 1:
        raise ValueError('encrypted pak entries are not supported')

    def rd(small):
        nonlocal p
        if small:
            v = struct.unpack_from('<I', enc, p)[0]
            p += 4
        else:
            v = struct.unpack_from('<q', enc, p)[0]
            p += 8
        return v

    off = rd(bits & (1 << 31))
    usize = rd(bits & (1 << 30))
    return dict(offset=off, usize=usize, comp=comp, blocks=[])


class Pak:
    def __init__(self, path):
        self.path = path
        with open(path, 'rb') as f:
            self.version, ioff, isz = _footer(f)
            f.seek(ioff)
            idx = io.BytesIO(f.read(isz))
            mount = _fstr(idx)
            count = struct.unpack('<i', idx.read(4))[0]
            self.entries = {}
            if self.version < 10:
                for _ in range(count):
                    name = _fstr(idx)
                    self.entries[mount + name] = _legacy_entry(idx)
                return
            idx.read(8)  # path hash seed
            if struct.unpack('<i', idx.read(4))[0]:
                idx.read(8 + 8 + 20)
            if not struct.unpack('<i', idx.read(4))[0]:
                raise ValueError('pak has no full directory index')
            fdi_off, fdi_sz = struct.unpack('<qq', idx.read(16))
            idx.read(20)
            enc = idx.read(struct.unpack('<i', idx.read(4))[0])
            f.seek(fdi_off)
            fdi = io.BytesIO(f.read(fdi_sz))
        for _ in range(struct.unpack('<i', fdi.read(4))[0]):
            d = _fstr(fdi)
            for _ in range(struct.unpack('<i', fdi.read(4))[0]):
                fn = _fstr(fdi)
                loc = struct.unpack('<i', fdi.read(4))[0]
                self.entries[mount + d + fn] = _encoded_entry(enc, loc)

    @staticmethod
    def norm(name):
        return name.replace('../../../', '', 1)

    def find(self, suffix):
        """Normalised path of the single entry ending in `suffix`, or None."""
        hits = [k for k in self.entries if k.endswith(suffix)]
        return self.norm(hits[0]) if hits else None

    def read(self, name):
        key = next(k for k in self.entries if self.norm(k) == name)
        e = self.entries[key]
        with open(self.path, 'rb') as f:
            if e['comp']:
                if e['comp'] != 1 or self.version >= 5:
                    raise ValueError(f'unsupported compression in {self.path}')
                out = b''
                for a, b in e['blocks']:  # v<5: absolute offsets
                    f.seek(a)
                    out += zlib.decompress(f.read(b - a))
            else:
                # skip the inline entry header: offset, csize, usize, comp, sha1, flags, block size
                f.seek(e['offset'] + 8 + 8 + 8 + 4 + 20 + 1 + 4)
                out = f.read(e['usize'])
        if len(out) != e['usize']:
            raise ValueError(f'short read for {name}')
        return out


def write_pak(path, files):
    """files: list of (relative path under ../../../, bytes)."""
    def fstr(s):
        b = s.encode() + b'\0'
        return struct.pack('<i', len(b)) + b

    body, index = bytearray(), bytearray()
    for name, data in files:
        sha = hashlib.sha1(data).digest()
        off = len(body)
        tail = sha + b'\0' + struct.pack('<I', 0)
        body += struct.pack('<qqqi', 0, len(data), len(data), 0) + tail + data
        index += fstr(name) + struct.pack('<qqqi', off, len(data), len(data), 0) + tail
    index = fstr('../../../') + struct.pack('<i', len(files)) + index
    footer = struct.pack('<IIqq', MAGIC, 3, len(body), len(index)) + hashlib.sha1(index).digest()
    with open(path, 'wb') as f:
        f.write(bytes(body + index + footer))
