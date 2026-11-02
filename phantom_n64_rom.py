"""Identification et normalisation non destructive des cartouches Nintendo 64."""
from dataclasses import dataclass
import hashlib
from pathlib import Path

MAX_ROM_BYTES = 128 * 1024 * 1024
N64_EXTENSIONS = {'.z64', '.n64', '.v64'}


@dataclass(frozen=True)
class N64Rom:
    data: bytes
    sha1: str
    title: str
    country: str
    revision: int
    input_format: str


def read_n64_rom(path):
    path = Path(path)
    size = path.stat().st_size
    if size < 64 or size > MAX_ROM_BYTES or size % 4:
        raise ValueError('Fichier N64 invalide : taille de cartouche incorrecte.')
    raw = path.read_bytes()
    if len(raw) != size:
        raise ValueError('Le fichier N64 a changé pendant sa lecture.')
    magic = raw[:4]
    if magic == b'\x80\x37\x12\x40':
        data, input_format = raw, 'z64'
    elif magic == b'\x37\x80\x40\x12':
        result = bytearray(size)
        result[0::2], result[1::2] = raw[1::2], raw[0::2]
        data, input_format = bytes(result), 'v64'
    elif magic == b'\x40\x12\x37\x80':
        result = bytearray(size)
        for lane in range(4):
            result[lane::4] = raw[3 - lane::4]
        data, input_format = bytes(result), 'n64'
    else:
        raise ValueError('Fichier N64 invalide : en-tête de cartouche non reconnu.')
    raw_title = data[0x20:0x34].decode('ascii', errors='replace').strip(' \x00')
    title = ''.join(c for c in raw_title if c.isprintable()).strip() or 'Jeu N64 inconnu'
    country = {ord('P'): 'Europe', ord('E'): 'États-Unis', ord('J'): 'Japon',
               ord('F'): 'France', ord('D'): 'Allemagne', ord('I'): 'Italie',
               ord('S'): 'Espagne', ord('U'): 'Australie'}.get(data[0x3e], 'région inconnue')
    return N64Rom(data, hashlib.sha1(data).hexdigest(), title, country, data[0x3f], input_format)
