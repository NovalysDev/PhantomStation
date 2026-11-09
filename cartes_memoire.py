"""Cartes partagées et profils de sauvegarde sélectionnés dans la bibliothèque."""
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import uuid
import zipfile
from datetime import datetime
from pathlib import Path

import bibliotheque

CONSOLES = ('PS1', 'PS2', 'DC', 'NES', 'SNES', 'N64')
CARDS = {'PS1': 'card1.mcd', 'PS2': 'carte.ps2', 'DC': 'carte.vmu'}
NINTENDO = ('NES', 'SNES', 'N64')
LOCAL = Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData/Local')) / 'PhantomStation'
CONFIG = LOCAL / 'cartes-selection.json'


def root():
    return bibliotheque.documents_folder() / 'PhantomStation' / 'Cartes-et-profils'


def _write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = None
    try:
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent, delete=False) as f:
            temp = Path(f.name)
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(temp, path)
    finally:
        if temp is not None: temp.unlink(missing_ok=True)


def _config():
    try:
        data = json.loads(CONFIG.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {}
    except (ValueError, UnicodeError) as exc:
        raise ValueError('La sélection des cartes est illisible ; aucune carte ne sera choisie automatiquement.') from exc
    if not isinstance(data, dict):
        raise ValueError('La sélection des cartes est invalide ; aucune carte ne sera choisie automatiquement.')
    return data


def list_cards(console):
    if console not in CONSOLES: raise ValueError('Console inconnue.')
    folder = root() / console
    result = []
    if not folder.is_dir(): return result
    for item in folder.iterdir():
        try:
            if not item.is_dir() or item.is_symlink() or not re.fullmatch(r'[0-9a-f]{32}', item.name): continue
            metadata = json.loads((item / 'carte.json').read_text(encoding='utf-8'))
            name = metadata.get('nom')
            if not isinstance(name, str) or not name or len(name) > 80: continue
            result.append({'id': item.name, 'nom': name, 'console': console, 'directory': item})
        except (OSError, ValueError, AttributeError):
            continue
    return sorted(result, key=lambda c: c['nom'].casefold())


def _selection_key(console, slot):
    if slot != 1 and (console not in ('PS1', 'PS2') or slot != 2): raise ValueError('Emplacement indisponible.')
    return console if slot == 1 else console + ':2'


def default_label(console, slot=1):
    key = _selection_key(console, slot)
    value = _config().get('label:' + key, '')
    if not isinstance(value, str) or len(value) > 80:
        raise ValueError('Le nom de la sauvegarde actuelle est invalide.')
    return value


def rename_default(console, slot, name):
    if console not in CONSOLES: raise ValueError('Console inconnue.')
    key = _selection_key(console, slot)
    if console == 'PS2' and slot == 2:
        raise ValueError('Cet emplacement est vide : choisis une carte à renommer.')
    name = name.strip()
    if not name or len(name) > 80 or any(ord(c) < 32 for c in name):
        raise ValueError('Choisis un nom de 1 à 80 caractères.')
    data = _config()
    data['label:' + key] = name
    _write_json(CONFIG, data)


def selected(console, slot=1):
    chosen = _config().get(_selection_key(console, slot), '')
    if not chosen: return None
    if not isinstance(chosen, str) or not re.fullmatch(r'[0-9a-f]{32}', chosen):
        raise ValueError('La sélection de sauvegarde est invalide.')
    for card in list_cards(console):
        if card['id'] == chosen: return card
    raise ValueError('La carte ou le profil sélectionné est introuvable. Choisis-en un autre.')


def select(console, card_id=None, slot=1):
    if console not in CONSOLES: raise ValueError('Console inconnue.')
    key = _selection_key(console, slot)
    if card_id and not any(card['id'] == card_id for card in list_cards(console)):
        raise ValueError('Cette carte est introuvable.')
    data = _config()
    if console in ('PS1', 'PS2') and card_id and data.get(_selection_key(console, 2 if slot == 1 else 1)) == card_id:
        raise ValueError('Choisis deux cartes différentes pour les deux emplacements.')
    data[key] = card_id or ''
    _write_json(CONFIG, data)


def blank_vmu():
    data = bytearray(256 * 512)
    root_block = 255 * 512; fat = 254 * 512
    data[root_block:root_block + 16] = bytes([0x55] * 16)
    def put(offset, value): data[offset:offset + 2] = value.to_bytes(2, 'little')
    for offset, value in ((0x40,255),(0x42,0),(0x44,255),(0x46,254),(0x48,1),
                          (0x4a,253),(0x4c,13),(0x50,200),(0x52,31),(0x54,0),(0x56,128)):
        put(root_block + offset, value)
    for i in range(256): put(fat + i * 2, 0xfffc)
    put(fat + 255 * 2, 0xfffa); put(fat + 254 * 2, 0xfffa)
    for i in range(253, 241, -1): put(fat + i * 2, i - 1)
    put(fat + 241 * 2, 0xfffa)
    return data


def _validate_card(console, source):
    source = Path(source)
    if not source.is_file() or source.is_symlink(): raise ValueError('Fichier de carte introuvable ou invalide.')
    size = source.stat().st_size
    if console == 'PS1':
        with source.open('rb') as f: header = f.read(2)
        if not 128 * 1024 <= size <= 256 * 1024 or header != b'MC':
            raise ValueError('Carte PS1 invalide : fichier .mcd compatible attendu.')
    elif console == 'DC' and size != 128 * 1024:
        raise ValueError('Carte Dreamcast invalide : fichier VMU de 128 Ko attendu.')
    elif console == 'PS2' and size != 8650752:
        raise ValueError('Carte PS2 invalide : image de 8 650 752 octets attendue par ce moteur.')


def _validate_name(console, name, exclude_id=None):
    if console not in CONSOLES: raise ValueError('Console inconnue.')
    name = name.strip()
    if not name or len(name) > 80 or any(ord(c) < 32 for c in name):
        raise ValueError('Choisis un nom de 1 à 80 caractères.')
    if any(c['id'] != exclude_id and c['nom'].casefold() == name.casefold() for c in list_cards(console)):
        raise ValueError('Ce nom existe déjà pour cette console.')
    return name


def rename(console, card_id, name):
    name = _validate_name(console, name, card_id)
    card = next((c for c in list_cards(console) if c['id'] == card_id), None)
    if card is None: raise ValueError('Cette carte ou ce profil est introuvable.')
    metadata_path = card['directory'] / 'carte.json'
    metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
    metadata['nom'] = name
    _write_json(metadata_path, metadata)
    return {**card, 'nom': name}


def create(console, name, source=None):
    name = _validate_name(console, name)
    if source is not None:
        if console not in CARDS: raise ValueError('Pour cette console, crée un profil de sauvegarde.')
        _validate_card(console, source)
    card_id = uuid.uuid4().hex
    folder = root() / console / card_id
    folder.mkdir(parents=True, exist_ok=False)
    if console in CARDS:
        target = folder / CARDS[console]
        if source is not None:
            before = Path(source).stat()
            with Path(source).open('rb') as src, target.open('xb') as dst:
                shutil.copyfileobj(src, dst, 1024 * 1024)
            after = Path(source).stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise OSError('La carte d’origine a changé pendant son importation.')
            with Path(source).open('rb') as src, target.open('rb') as dst:
                if hashlib.file_digest(src, 'sha256').digest() != hashlib.file_digest(dst, 'sha256').digest():
                    raise OSError('La vérification de la carte importée a échoué.')
            _validate_card(console, target)
        elif console == 'PS1': target.write_bytes(bibliotheque.blank_card())
        elif console == 'DC': target.write_bytes(blank_vmu())
        else:
            blank = Path(__file__).resolve().parent / 'assets' / 'carte-ps2-vierge.ps2'
            _validate_card('PS2', blank)
            with blank.open('rb') as src, target.open('xb') as dst: shutil.copyfileobj(src, dst, 1024 * 1024)
    _write_json(folder / 'carte.json', {'nom': name, 'console': console})
    return {'id': card_id, 'nom': name, 'console': console, 'directory': folder}


def profile_dir(game):
    console = game.get('platform')
    if console not in NINTENDO: return None
    card = selected(console)
    if card is None: return None
    if not re.fullmatch(r'[0-9a-f]{32}', game['id']): raise ValueError('Identité du jeu invalide.')
    return card['directory'] / 'jeux' / game['id']


def _runtime_contains(game, markers):
    tail = b''
    longest = max(map(len, markers))
    with zipfile.ZipFile(game['path']) as archive, archive.open(game['runtime']) as stream:
        head = stream.read(1024 * 1024)
        if any(marker in head for marker in markers): return True
        packed = b'UPX0' in head and b'UPX1' in head
        tail = head[-longest:]
        while True:
            block = stream.read(1024 * 1024)
            if not block: break
            combined = tail + block
            if any(marker in combined for marker in markers): return True
            tail = combined[-longest:]
    if not packed: return False
    upx = Path(__file__).resolve().parent / 'nintendo-recomp' / 'assets' / 'tools' / 'upx.exe'
    if not upx.is_file(): raise OSError('L’outil de vérification du jeu compressé est introuvable.')
    with tempfile.TemporaryDirectory(prefix='phantom-verification-carte-') as tmp:
        packed_file, decoded = Path(tmp) / 'runtime.exe', Path(tmp) / 'runtime-decode.exe'
        with zipfile.ZipFile(game['path']) as archive, archive.open(game['runtime']) as src, packed_file.open('xb') as dst:
            shutil.copyfileobj(src, dst, 1024 * 1024)
        result = subprocess.run([str(upx), '-d', '-o', str(decoded), str(packed_file)],
                                capture_output=True, timeout=60,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode or not decoded.is_file():
            raise ValueError('Impossible de vérifier la compatibilité du jeu compressé avec ce profil.')
        with decoded.open('rb') as stream:
            tail = b''
            while block := stream.read(1024 * 1024):
                data = tail + block
                if any(marker in data for marker in markers): return True
                tail = data[-longest:]
    return False


def launch_environment(game):
    console = game.get('platform')
    card = selected(console)
    card2 = selected(console, 2) if console in ('PS1', 'PS2') else None
    if card is None and card2 is None: return {}
    if console in NINTENDO:
        goldeneye = False
        if console == 'N64':
            with zipfile.ZipFile(game['path']) as archive:
                goldeneye = 'data/ge007.pal-final.z64' in archive.namelist()
            markers = (b'--save-dir',)
        else:
            marker = 'PHANTOM_RUNTIME_DATA_ROOT'
            markers = (marker.encode('ascii'), marker.encode('utf-16le'))
        if not goldeneye and not _runtime_contains(game, markers):
            raise ValueError('Ce jeu ancien ne permet pas de choisir un profil depuis la bibliothèque. Il faut le recréer avec le compilateur actuel.')
        folder = profile_dir(game); folder.mkdir(parents=True, exist_ok=True)
        return {'PHANTOM_SELECTED_PROFILE_DIR': str(folder)}
    if console == 'PS1':
        marker = 'PHANTOM_SELECTED_PS1_CARD2'
        if not _runtime_contains(game, (marker.encode('ascii'), marker.encode('utf-16le'))):
            raise ValueError('Ce jeu PS1 ancien ne permet pas de choisir les deux cartes depuis la bibliothèque. Il faut le recréer avec le compilateur actuel ou choisir les cartes depuis son lanceur.')
        env = {}
        for index, entry in ((1, card), (2, card2)):
            if entry is not None:
                path = entry['directory'] / CARDS['PS1']; _validate_card('PS1', path)
                env['PHANTOM_SELECTED_PS1_CARD' + str(index)] = str(path)
        return env
    if console == 'PS2' and card2 is not None:
        marker = b'PHANTOM_SELECTED_PS2_CARD2'
        if not _runtime_contains(game, (marker, marker.decode('ascii').encode('utf-16le'))):
            raise ValueError('Ce jeu PS2 ancien ne prend pas en charge la deuxième carte depuis la bibliothèque. Il faut le recréer avec le compilateur actuel.')
    env = {}
    if card2 is not None:
        path2 = card2['directory'] / CARDS[console]; _validate_card(console, path2)
        env['PHANTOM_SELECTED_PS2_CARD2'] = str(path2)
    if card is None: return env
    path = card['directory'] / CARDS[console]
    _validate_card(console, path)
    marker = '--memcard' if console == 'PS2' else 'PHANTOM_DC_VMU'
    if not _runtime_contains(game, (marker.encode('ascii'), marker.encode('utf-16le'))):
        raise ValueError('Ce jeu ancien ne permet pas de sélectionner une carte depuis la bibliothèque. Il faut le recréer avec le compilateur actuel.')
    if console == 'PS2':
        env['PHANTOM_SELECTED_PS2_CARD'] = str(path)
        return env
    return {'PHANTOM_SELECTED_DC_CARD': str(path)}


def backup_selected(console, slot=1):
    card = selected(console, slot)
    if card is None or console not in CARDS: return None
    src = card['directory'] / CARDS[console]
    _validate_card(console, src)
    before = src.stat()
    with src.open('rb') as f: digest = hashlib.file_digest(f, 'sha256').digest()
    checked = src.stat()
    if (before.st_size, before.st_mtime_ns) != (checked.st_size, checked.st_mtime_ns):
        raise OSError('La carte a changé pendant sa vérification.')
    target = bibliotheque.documents_folder() / 'PhantomStation' / 'Sauvegardes-secours' / 'Cartes' / console / card['id']
    target.mkdir(parents=True, exist_ok=True)
    previous = sorted(target.glob('*' + src.suffix), reverse=True)
    if previous:
        with previous[0].open('rb') as f:
            if hashlib.file_digest(f, 'sha256').digest() == digest: return None
    temp = None
    try:
        with tempfile.NamedTemporaryFile(dir=target, suffix='.part', delete=False) as f:
            temp = Path(f.name)
            with src.open('rb') as stream: shutil.copyfileobj(stream, f, 1024 * 1024)
        with temp.open('rb') as f:
            if hashlib.file_digest(f, 'sha256').digest() != digest: raise OSError('La carte a changé pendant sa copie.')
        after = src.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise OSError('La carte a changé pendant sa copie.')
        destination = target / (datetime.now().strftime('%Y-%m-%d_%H-%M-%S_%f') + src.suffix)
        os.rename(temp, destination); temp = None
        return destination
    finally:
        if temp is not None: temp.unlink(missing_ok=True)
