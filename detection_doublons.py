"""Identification légère des images et recherche des jeux déjà compilés."""
import hashlib
import json
import re
import unicodedata
from pathlib import Path

SOURCE_ENTRY = 'phantom-source.json'
SAMPLE_SIZE = 64 * 1024


def normalized_title(value):
    text = Path(str(value)).stem if str(value).lower().endswith(('.iso', '.bin', '.cue', '.chd', '.nes', '.sfc', '.smc', '.z64', '.v64', '.n64', '.exe')) else str(value)
    text = re.sub(r'\([^()]*\)|\[[^\[\]]*\]', ' ', text)
    text = re.sub(r'\b(?:disc|disque|cd)\s*[-_. ]*\d+\b', ' ', text, flags=re.I)
    text = re.sub(r'\b(?:france|europe|usa|japan|japon|pal|ntsc)\b', ' ', text, flags=re.I)
    text = unicodedata.normalize('NFKD', text.casefold())
    text = ''.join(char for char in text if not unicodedata.combining(char))
    return ' '.join(re.findall(r'[^\W_]+', text, flags=re.UNICODE))


def sample_file(path):
    path = Path(path)
    size = path.stat().st_size
    digest = hashlib.sha256()
    digest.update(b'phantom-source-sample-v1\0')
    digest.update(size.to_bytes(8, 'big'))
    positions = sorted({0, max(0, size // 4 - SAMPLE_SIZE // 2),
                        max(0, size // 2 - SAMPLE_SIZE // 2),
                        max(0, 3 * size // 4 - SAMPLE_SIZE // 2),
                        max(0, size - SAMPLE_SIZE)})
    with path.open('rb') as stream:
        for offset in positions:
            stream.seek(offset)
            digest.update(offset.to_bytes(8, 'big'))
            digest.update(stream.read(SAMPLE_SIZE))
    return digest.hexdigest()


def fingerprint_groups(console, groups):
    """Fingerprint ordered discs and their tracks without reading full disc images."""
    if console not in ('PS1', 'PS2', 'NES', 'SNES', 'N64', 'DC') or not groups:
        raise ValueError('Sources du jeu indisponibles.')
    fingerprints = [[sample_file(path) for path in group] for group in groups]
    if any(not group for group in fingerprints):
        raise ValueError('Une image de jeu est vide.')
    return {'version': 1, 'console': console, 'disques': fingerprints}


def fingerprint_selection(console, selected):
    groups = []
    for value in selected:
        path = Path(value)
        if console == 'PS1' and path.suffix.lower() == '.bin' and path.with_suffix('.cue').is_file():
            path = path.with_suffix('.cue')
        if console == 'PS1' and path.suffix.lower() == '.cue':
            from emballer import _cue_files
            _, tracks = _cue_files(path)
            groups.append([source for source, _ in tracks])
        else:
            groups.append([path])
    return fingerprint_groups(console, groups)


def encoded_source(console, groups):
    return json.dumps(fingerprint_groups(console, groups), ensure_ascii=False, separators=(',', ':')).encode('utf-8')


def find_duplicates(console, name, selected, games):
    try:
        selected_fingerprint = fingerprint_selection(console, selected)
    except (OSError, ValueError, UnicodeError):
        selected_fingerprint = None
    titles = {normalized_title(value) for value in [name, *selected] if value}
    titles.discard('')
    matches = []
    for game in games:
        if game.get('platform') != console:
            continue
        source = game.get('source')
        same_source = bool(selected_fingerprint and source == selected_fingerprint)
        game_titles = {normalized_title(value) for value in
                       (game.get('name', ''), game.get('original_name', ''), game['path'].stem) if value}
        same_title = bool(titles & game_titles)
        if same_source or same_title:
            matches.append((game, 'source' if same_source else 'titre'))
    return sorted(matches, key=lambda result: (result[1] != 'source', result[0]['name'].casefold()))
