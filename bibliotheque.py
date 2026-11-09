"""Bibliothèque des jeux PhantomStation et sauvegardes de secours."""
import ctypes
import hashlib
import io
import json
import os
import re
import subprocess
import tempfile
import tomllib
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath

from PIL import Image

BASE = Path(__file__).resolve().parent
LOCAL = Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData/Local'))
LIBRARY_FILE = LOCAL / 'PhantomStation' / 'bibliotheque.json'
SAVE_SUFFIXES = {'.mcd', '.mcr', '.mc', '.sav', '.srm', '.gme', '.mcs', '.ps', '.ps2', '.state', '.inputs', '.rrstate', '.rrsram', '.eep', '.vmu'}


def documents_folder():
    buffer = ctypes.create_unicode_buffer(260)
    result = ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buffer)
    return Path(buffer.value) if result == 0 and buffer.value else Path.home() / 'Documents'


def game_info(path, include_cover=True):
    path = Path(path).resolve()
    if path.suffix.lower() != '.exe' or not path.is_file():
        raise ValueError('Fichier EXE introuvable.')
    with zipfile.ZipFile(path) as archive:
        for name in ('phantom-package.txt', 'game.toml'):
            if archive.getinfo(name).file_size > 1024 * 1024:
                raise ValueError('Fichier du jeu invalide.')
        lines = archive.read('phantom-package.txt').decode('utf-8-sig').splitlines()
        if len(lines) < 2 or not re.fullmatch('[0-9a-f]{32}', lines[0]) or Path(lines[1]).name != lines[1]:
            raise ValueError('Ce fichier n’est pas un jeu PhantomStation valide.')
        config = tomllib.loads(archive.read('game.toml').decode('utf-8-sig'))
        source = None
        try:
            source_info = archive.getinfo('phantom-source.json')
            if source_info.file_size <= 64 * 1024:
                source = json.loads(archive.read(source_info).decode('utf-8'))
        except (KeyError, ValueError, UnicodeError):
            pass
        cover = None
        try:
            if not include_cover:
                raise KeyError('cover disabled')
            image_info = archive.getinfo('assets/img/boxart.tga')
            if image_info.file_size <= 32 * 1024 * 1024:
                with Image.open(io.BytesIO(archive.read(image_info))) as image:
                    image.thumbnail((140, 170), Image.Resampling.LANCZOS)
                    cover = image.convert('RGBA')
        except (KeyError, OSError, ValueError, Image.DecompressionBombError):
            pass
    game = config.get('game', {})
    original_name = str(game.get('name') or path.stem)
    title = original_name
    try:
        title_file = LOCAL / 'PhantomStation' / 'Titres' / (lines[0] + '.txt')
        if title_file.is_file() and title_file.stat().st_size <= 1024:
            saved_title = title_file.read_text(encoding='utf-8-sig').strip()
            if 0 < len(saved_title) <= 120:
                title = saved_title
    except (OSError, UnicodeError):
        pass
    return {'path': path, 'id': lines[0], 'runtime': lines[1],
            'documents_saves': len(lines) > 4 and lines[4].strip() == 'documents-saves-v1',
            'name': title, 'original_name': original_name, 'serial': str(game.get('id') or ''), 'cover': cover, 'source': source,
            'platform': game.get('platform') if game.get('platform') in ('PS1', 'PS2', 'NES', 'SNES', 'N64', 'DC') else 'PS1'}


def registered_games():
    try:
        data = json.loads(LIBRARY_FILE.read_text(encoding='utf-8'))
        return [Path(p) for p in data.get('jeux', []) if isinstance(p, str) and Path(p).is_absolute()]
    except (OSError, ValueError, AttributeError):
        return []


def register_game(path):
    path = Path(path).resolve()
    paths = registered_games()
    if not any(str(p).casefold() == str(path).casefold() for p in paths):
        paths.append(path)
    LIBRARY_FILE.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=LIBRARY_FILE.parent, delete=False) as stream:
        temporary = Path(stream.name)
        json.dump({'jeux': [str(p) for p in paths]}, stream, ensure_ascii=False, indent=2)
    try:
        os.replace(temporary, LIBRARY_FILE)
    finally:
        temporary.unlink(missing_ok=True)


def rename_game(game, name):
    """Rename the file and remember a display title without rewriting its disc."""
    name = name.strip()
    if (not name or len(name) > 120 or name[-1] in '. ' or
        any(c in '<>:"/\\|?*' or ord(c) < 32 for c in name) or
        re.fullmatch(r'(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', name)):
        raise ValueError('Choisis un nom de 1 à 120 caractères, sans caractères interdits par Windows.')
    original = Path(game['path']).resolve(strict=True)
    if game_running(game):
        raise ValueError('Ferme le jeu avant de le renommer.')
    current = game_info(original)
    if current['id'] != game['id']:
        raise ValueError('Le fichier du jeu a changé ; actualise la bibliothèque.')
    destination = original.with_name(name + '.exe')
    if destination != original and destination.exists():
        raise ValueError('Un fichier porte déjà ce nom dans ce dossier.')
    title_dir = LOCAL / 'PhantomStation' / 'Titres'
    title_dir.mkdir(parents=True, exist_ok=True)
    title_file = title_dir / (game['id'] + '.txt')
    before = title_file.read_bytes() if title_file.is_file() else None
    with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=title_dir, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(name)
    moved = False
    try:
        if destination != original:
            os.rename(original, destination)
            moved = True
        os.replace(temporary, title_file)
        register_game(destination)
    except Exception:
        if moved and destination.exists() and not original.exists():
            os.rename(destination, original)
        if before is None:
            title_file.unlink(missing_ok=True)
        else:
            title_file.write_bytes(before)
        raise
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def discover_games(output, include_cover=True):
    paths = registered_games()
    try:
        root = Path(output)
        # Include console folders inside the selected directory without following
        # links or wandering indefinitely through an unrelated large tree.
        count = 0
        for current, dirs, files in os.walk(root, followlinks=False):
            folder = Path(current)
            depth = len(folder.relative_to(root).parts)
            dirs[:] = [name for name in dirs if depth < 3 and not (folder / name).is_symlink()
                       and not getattr((folder / name).lstat(), 'st_file_attributes', 0) & 0x400]
            count += 1
            paths.extend(folder / name for name in files if name.lower().endswith('.exe'))
            if count >= 5000 or len(paths) >= 5000:
                break
    except OSError:
        pass
    games, seen = [], set()
    for path in paths:
        key = str(path).casefold()
        if key in seen:
            continue
        seen.add(key)
        try:
            games.append(game_info(path, include_cover=include_cover))
        except (OSError, ValueError, KeyError, zipfile.BadZipFile, UnicodeError):
            continue
    return sorted(games, key=lambda x: (x['name'].casefold(), str(x['path']).casefold()))


def save_directory(game):
    if game.get('platform') in ('NES', 'SNES', 'N64'):
        from cartes_memoire import profile_dir
        profile = profile_dir(game)
        if profile is not None: return profile
    if game['documents_saves']:
        base = documents_folder() / 'PhantomStation' / 'Sauvegardes'
        matches = [p for p in base.glob('* - ' + game['id']) if p.is_dir() and not p.is_symlink()]
        if len(matches) > 1:
            raise ValueError('Plusieurs dossiers de sauvegarde correspondent à ce jeu.')
        return matches[0] if matches else None
    return LOCAL / 'PhantomStation' / 'Jeux' / game['id'] / 'saves'


def game_running(game):
    env = os.environ.copy()
    env['PHANTOM_CHECK_PACKAGE'] = str(game['path'])
    env['PHANTOM_CHECK_RUNTIME'] = str(LOCAL / 'PhantomStation' / 'Jeux' / game['id'] / game['runtime'])
    script = "Get-Process | ForEach-Object { try { if ($_.Path -and ($_.Path -ieq $env:PHANTOM_CHECK_RUNTIME -or $_.Path -ieq $env:PHANTOM_CHECK_PACKAGE)) { 'RUNNING' } } catch {} }"
    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                            capture_output=True, text=True, env=env, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:
        raise OSError('Impossible de vérifier si le jeu est ouvert.')
    return 'RUNNING' in result.stdout


def _save_files(folder):
    if not folder or not folder.is_dir():
        return []
    if folder.is_symlink() or getattr(folder.lstat(), 'st_file_attributes', 0) & 0x400:
        raise ValueError('Le dossier des sauvegardes est un lien.')
    found = []
    for current, dirs, files in os.walk(folder, followlinks=False, onerror=lambda e: (_ for _ in ()).throw(e)):
        for name in dirs + files:
            item = Path(current) / name
            st = item.lstat()
            if item.is_symlink() or getattr(st, 'st_file_attributes', 0) & 0x400:
                raise ValueError('Les sauvegardes contiennent un lien.')
            if item.is_file() and (item.suffix.lower() in SAVE_SUFFIXES or item.name == '.phantom-page'):
                if st.st_size > 128 * 1024 * 1024:
                    raise ValueError('Fichier de sauvegarde trop volumineux : ' + name)
                found.append(item)
    return found


def backup_saves(game, skip_unchanged=False):
    if game_running(game):
        raise ValueError('Ferme le jeu avant de copier ses sauvegardes.')
    folder = save_directory(game)
    files = _save_files(folder)
    if not files:
        return None
    target = documents_folder() / 'PhantomStation' / 'Sauvegardes-secours' / game['id']
    target.mkdir(parents=True, exist_ok=True)
    snapshots = {}
    identities = {}
    for file in files:
        before = file.stat()
        with file.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        after = file.stat()
        if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
            raise OSError('La sauvegarde a changé pendant la vérification : ' + file.name)
        snapshots[file.relative_to(folder).as_posix()] = {'taille': before.st_size, 'sha256': digest}
        identities[file] = (after.st_size, after.st_mtime_ns, after.st_ino)
    if skip_unchanged:
        previous = sorted(target.glob('*.zip'), reverse=True)
        if previous:
            try:
                with zipfile.ZipFile(previous[0]) as archive:
                    info = archive.getinfo('phantom-sauvegarde.json')
                    if info.file_size <= 1024 * 1024:
                        meta = json.loads(archive.read(info).decode('utf-8'))
                        if meta.get('id') == game['id'] and meta.get('fichiers') == snapshots:
                            if all(identities[file] == (file.stat().st_size, file.stat().st_mtime_ns, file.stat().st_ino) for file in files):
                                return None
                            raise OSError('Une sauvegarde a changé pendant la vérification.')
            except (OSError, KeyError, TypeError, AttributeError, ValueError, zipfile.BadZipFile, UnicodeError):
                pass
    name = datetime.now().strftime('%Y-%m-%d_%H-%M-%S_%f') + '.zip'
    path = target / name
    with tempfile.NamedTemporaryFile(dir=target, suffix='.part', delete=False) as stream:
        tmp = Path(stream.name)
    try:
        with zipfile.ZipFile(tmp, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('phantom-sauvegarde.json', json.dumps({'id': game['id'], 'nom': game['name'], 'fichiers': snapshots}, ensure_ascii=False))
            for file in files:
                before = file.stat()
                archive.write(file, file.relative_to(folder).as_posix())
                after = file.stat()
                if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
                    raise OSError('La sauvegarde a changé pendant la copie : ' + file.name)
        with zipfile.ZipFile(tmp) as check:
            for name, expected in snapshots.items():
                with check.open(name) as stream:
                    digest = hashlib.file_digest(stream, 'sha256').hexdigest()
                if digest != expected['sha256']:
                    raise OSError('La sauvegarde a changé pendant la copie : ' + name)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    return path


def restore_saves(game, archive_path):
    if game_running(game):
        raise ValueError('Ferme le jeu avant de restaurer ses sauvegardes.')
    folder = save_directory(game)
    if folder is None or not folder.is_dir():
        raise ValueError('Lance une première fois le jeu pour créer son dossier de sauvegarde.')
    _save_files(folder)
    contents = []
    with zipfile.ZipFile(archive_path) as archive:
        meta = archive.getinfo('phantom-sauvegarde.json')
        if meta.file_size > 1024 * 1024 or json.loads(archive.read(meta).decode('utf-8')).get('id') != game['id']:
            raise ValueError('Cette copie appartient à un autre jeu.')
        total = 0
        names = set()
        for info in archive.infolist():
            if info.filename == 'phantom-sauvegarde.json' or info.is_dir(): continue
            name = PurePosixPath(info.filename)
            allowed = name.suffix.lower() in SAVE_SUFFIXES or (len(name.parts) == 1 and name.name == '.phantom-page')
            if name.is_absolute() or '..' in name.parts or '\\' in info.filename or ':' in info.filename or not allowed:
                raise ValueError('La copie contient un chemin invalide.')
            if name.as_posix().casefold() in names:
                raise ValueError('La copie contient plusieurs fois le même fichier.')
            names.add(name.as_posix().casefold())
            total += info.file_size
            if info.file_size > 128 * 1024 * 1024 or total > 512 * 1024 * 1024:
                raise ValueError('La copie est trop volumineuse.')
            data = archive.read(info)
            if name.as_posix() == '.phantom-page':
                try: page = int(data.decode('ascii').strip())
                except (ValueError, UnicodeError) as exc: raise ValueError('La page sauvegardée est invalide.') from exc
                if not 1 <= page <= 16: raise ValueError('La page sauvegardée est invalide.')
            contents.append((name, data))
    if not contents:
        raise ValueError('Cette copie ne contient aucune sauvegarde.')
    before = backup_saves(game)
    for name, data in contents:
        target = folder.joinpath(*name.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_symlink() or not target.resolve().is_relative_to(folder.resolve()):
            raise ValueError('Chemin de sauvegarde non sûr.')
        with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as stream:
            tmp = Path(stream.name)
            stream.write(data)
        try: os.replace(tmp, target)
        finally: tmp.unlink(missing_ok=True)
    if not any(name.as_posix() == '.phantom-page' for name, _ in contents) and (folder / '.phantom-page').exists():
        _atomic_bytes(folder / '.phantom-page', b'1\n')
    return before


def _atomic_bytes(path, data):
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        tmp = Path(stream.name)
        stream.write(data)
    try: os.replace(tmp, path)
    finally: tmp.unlink(missing_ok=True)


def blank_card():
    """Format the same 128 KiB, 15-block card as the bundled PS1 runtime."""
    data = bytearray(b'\xff' * (128 * 1024))
    def frame(number, first):
        start = number * 128
        data[start:start + 128] = bytes(128)
        for offset, value in first.items(): data[start + offset] = value
        checksum = 0
        for value in data[start:start + 127]: checksum ^= value
        data[start + 127] = checksum
    frame(0, {0: ord('M'), 1: ord('C')})
    for n in range(1, 16): frame(n, {0: 0xa0, 8: 0xff, 9: 0xff})
    for n in range(16, 36): frame(n, {0: 0xff, 1: 0xff, 2: 0xff, 3: 0xff, 8: 0xff, 9: 0xff})
    data[36*128:63*128] = bytes(27 * 128)
    data[63*128:64*128] = data[:128]
    return bytes(data)


def active_page(game):
    folder = save_directory(game)
    if folder is None or not folder.is_dir(): return 1
    marker = folder / '.phantom-page'
    if marker.is_symlink(): raise ValueError('Fichier de page non sûr.')
    try: page = int(marker.read_text(encoding='ascii').strip())
    except FileNotFoundError: return 1
    except (ValueError, UnicodeError) as exc: raise ValueError('La page active ne peut pas être identifiée.') from exc
    if not 1 <= page <= 16: raise ValueError('Page active invalide.')
    return page


def switch_page(game, target):
    if not 1 <= target <= 16: raise ValueError('Choisis une page entre 1 et 16.')
    if game_running(game): raise ValueError('Ferme le jeu avant de changer de page.')
    folder = save_directory(game)
    if folder is None or not folder.is_dir(): raise ValueError('Lance une première fois le jeu pour créer sa carte mémoire.')
    _save_files(folder)  # refuse links before making any change
    card = folder / 'card1.mcd'
    if not card.is_file() or card.is_symlink(): raise ValueError('La carte mémoire par défaut est introuvable. Lance d’abord le jeu avec la carte 1 activée.')
    bank = folder / 'Cartes-PhantomStation'
    if bank.exists() and (bank.is_symlink() or getattr(bank.lstat(), 'st_file_attributes', 0) & 0x400):
        raise ValueError('Le dossier des pages est un lien.')
    bank.mkdir(exist_ok=True)
    journal = bank / '.changement-en-cours.json'
    if journal.exists():
        record = json.loads(journal.read_text(encoding='utf-8'))
        digest = hashlib.sha256(card.read_bytes()).hexdigest()
        if digest == record.get('nouvelle'):
            _atomic_bytes(folder / '.phantom-page', (str(record['vers']) + '\n').encode('ascii'))
        elif digest == record.get('ancienne'):
            _atomic_bytes(folder / '.phantom-page', (str(record['depuis']) + '\n').encode('ascii'))
        else:
            raise ValueError('Un changement de page a été interrompu. Restaure une copie de secours avant de continuer.')
        journal.unlink()
    current = active_page(game)
    if current == target: return current
    old = card.read_bytes()
    if len(old) < 128 * 1024 or old[:2] != b'MC': raise ValueError('La carte mémoire actuelle est invalide ; elle a été conservée.')
    current_path = bank / f'page-{current:02}.mcd'
    next_path = bank / f'page-{target:02}.mcd'
    for item in (current_path, next_path, journal, folder / '.phantom-page'):
        if item.is_symlink() or (item.exists() and getattr(item.lstat(), 'st_file_attributes', 0) & 0x400):
            raise ValueError('Un fichier de page est un lien.')
    next_data = next_path.read_bytes() if next_path.exists() else blank_card()
    if len(next_data) < 128 * 1024 or next_data[:2] != b'MC': raise ValueError('La page choisie est invalide ; elle a été conservée.')
    backup_saves(game)
    _atomic_bytes(current_path, old)
    if not next_path.exists(): _atomic_bytes(next_path, next_data)
    record = {'depuis': current, 'vers': target, 'ancienne': hashlib.sha256(old).hexdigest(), 'nouvelle': hashlib.sha256(next_data).hexdigest()}
    _atomic_bytes(journal, json.dumps(record).encode('utf-8'))
    _atomic_bytes(card, next_data)
    _atomic_bytes(folder / '.phantom-page', (str(target) + '\n').encode('ascii'))
    journal.unlink()
    return target


