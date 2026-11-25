"""Isolated, per-game debug saves imported from a diagnostic report."""
import json
import os
import re
import shutil
import stat
import zipfile
from pathlib import Path, PurePosixPath

import bibliotheque
import cartes_memoire as cm


def _root():
    return bibliotheque.documents_folder() / 'PhantomStation' / 'Debogage'


def _identity(game):
    ident = str(game.get('id', ''))
    if not re.fullmatch(r'[0-9a-f]{32}', ident) or game.get('platform') not in cm.CONSOLES:
        raise ValueError('Identité ou console du jeu invalide.')
    return ident


def _folder(game):
    return _root() / _identity(game)


def _safe_folder(game):
    folder = _folder(game)
    if not folder.exists(): return None
    if folder.is_symlink() or getattr(folder.lstat(), 'st_file_attributes', 0) & 0x400:
        raise ValueError('Le dossier de débogage est un lien.')
    metadata = json.loads((folder / 'debug.json').read_text(encoding='utf-8'))
    if metadata != {'id': _identity(game), 'platform': game['platform'], 'type': 'phantom-debug-v1'}:
        raise ValueError('Le dossier ne correspond pas à une sauvegarde de débogage PhantomStation.')
    return folder


def active(game):
    return _safe_folder(game) is not None


def candidates(game, archive):
    """List suitable save files in a report; never extract unknown paths."""
    console = game['platform']
    _identity(game)
    allowed = {'PS1': {'.mcd', '.mcr', '.mc'}, 'PS2': {'.ps2'}, 'DC': {'.vmu', '.bin'}}
    nintendo = {'.sav', '.srm', '.state', '.inputs', '.rrstate', '.rrsram', '.eep'}
    source_path = Path(archive)
    if not zipfile.is_zipfile(source_path):
        if (source_path.is_file() and not source_path.is_symlink() and source_path.stat().st_size <= 64 * 1024 * 1024
                and source_path.suffix.lower() in allowed.get(console, nintendo)):
            return ['@direct']
        return []
    with zipfile.ZipFile(archive) as source:
        entries = []
        for item in source.infolist():
            name = PurePosixPath(item.filename)
            if (item.is_dir() or name.is_absolute() or not item.filename.startswith('sauvegardes/')
                    or any(p in ('', '.', '..') or ':' in p or '\\' in p for p in name.parts)
                    or stat.S_ISLNK(item.external_attr >> 16) or item.flag_bits & 1
                    or item.file_size > 64 * 1024 * 1024):
                continue
            suffixes = allowed.get(console, nintendo)
            if name.suffix.lower() in suffixes or (console in cm.NINTENDO and name.name == '.phantom-page'):
                entries.append(item.filename)
        return entries


def install(game, archive, members):
    ident = _identity(game)
    if bibliotheque.game_running(game): raise ValueError('Ferme le jeu avant de changer sa sauvegarde de débogage.')
    if active(game): raise ValueError('Une sauvegarde de débogage est déjà active pour ce jeu.')
    valid = candidates(game, archive)
    chosen = list(members)
    if not chosen or len(set(chosen)) != len(chosen) or any(x not in valid for x in chosen):
        raise ValueError('Sélection de sauvegarde invalide.')
    if game['platform'] in cm.CARDS and len(chosen) != 1:
        raise ValueError('Choisis une seule carte mémoire.')
    root = _root(); root.mkdir(parents=True, exist_ok=True)
    if root.is_symlink() or getattr(root.lstat(), 'st_file_attributes', 0) & 0x400:
        raise ValueError('Le dossier de débogage est un lien.')
    folder = _folder(game)
    # Reserve a unique new directory; no existing user save can be replaced.
    folder.mkdir(exist_ok=False)
    try:
        if chosen == ['@direct']:
            original = Path(archive)
            target = folder / cm.CARDS[game['platform']] if game['platform'] in cm.CARDS else folder / 'profil' / original.name
            target.parent.mkdir(parents=True, exist_ok=True)
            with original.open('rb') as src, target.open('xb') as dst:
                shutil.copyfileobj(src, dst, 1024 * 1024)
        else:
            with zipfile.ZipFile(archive) as source:
                if sum(source.getinfo(x).file_size for x in chosen) > 64 * 1024 * 1024:
                    raise ValueError('Sauvegarde de débogage trop volumineuse.')
                for name in chosen:
                    relative = PurePosixPath(name)
                    if game['platform'] in cm.CARDS:
                        target = folder / cm.CARDS[game['platform']]
                    else:
                        if len(relative.parts) < 3 or relative.parts[1] != 'jeu':
                            raise ValueError('Profil de sauvegarde incompatible.')
                        target = folder / 'profil' / Path(*relative.parts[2:])
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with source.open(name) as src, target.open('xb') as dst:
                        shutil.copyfileobj(src, dst, 1024 * 1024)
        if game['platform'] in cm.CARDS:
            cm._validate_card(game['platform'], folder / cm.CARDS[game['platform']])
            if game['platform'] == 'PS1':
                # Slot 2 is also isolated; do not expose the owner's card.
                (folder / 'card2.mcd').write_bytes(bibliotheque.blank_card())
        (folder / 'debug.json').write_text(json.dumps({'id': ident, 'platform': game['platform'], 'type': 'phantom-debug-v1'}), encoding='utf-8')
    except Exception:
        _remove_owned(folder, root)
        raise


def launch_environment(game):
    folder = _safe_folder(game)
    if folder is None: return {}
    console = game['platform']
    if console in cm.NINTENDO:
        with zipfile.ZipFile(game['path']) as archive:
            goldeneye = console == 'N64' and 'data/ge007.pal-final.z64' in archive.namelist()
        marker = '--save-dir' if console == 'N64' else 'PHANTOM_RUNTIME_DATA_ROOT'
    else:
        goldeneye = False
        marker = {'PS1': 'PHANTOM_SELECTED_PS1_CARD2', 'PS2': '--memcard', 'DC': 'PHANTOM_DC_VMU'}[console]
        if console == 'PS1' and not game.get('documents_saves'):
            raise ValueError('Ce jeu PS1 ancien ne permet pas d’isoler toutes ses sauvegardes. Recrée-le avec le compilateur actuel.')
    if not goldeneye and not cm._runtime_contains(game, (marker.encode('ascii'), marker.encode('utf-16le'))):
        raise ValueError('Ce jeu ancien ne permet pas d’isoler les sauvegardes de débogage. Recrée-le avec le compilateur actuel.')
    base = {'PHANTOM_DEBUG_SAVE_DIR': str(folder)}
    if console in cm.NINTENDO:
        base['PHANTOM_SELECTED_PROFILE_DIR'] = str(folder / 'profil')
        return base
    card = folder / cm.CARDS[console]
    cm._validate_card(console, card)
    if console == 'DC': base['PHANTOM_SELECTED_DC_CARD'] = str(card); return base
    if console == 'PS2': base['PHANTOM_SELECTED_PS2_CARD'] = str(card); return base
    cm._validate_card('PS1', folder / 'card2.mcd')
    base.update(PHANTOM_SELECTED_PS1_CARD1=str(card), PHANTOM_SELECTED_PS1_CARD2=str(folder / 'card2.mcd'))
    return base


def _remove_owned(folder, root):
    # Only the per-game folder under our own debug directory may be removed.
    resolved_root, resolved = root.resolve(strict=True), folder.resolve(strict=True)
    if resolved.parent != resolved_root or not re.fullmatch(r'[0-9a-f]{32}', resolved.name):
        raise ValueError('Suppression du dossier de débogage refusée.')
    for current, dirs, files in os.walk(resolved, topdown=False, followlinks=False):
        for name in files + dirs:
            item = Path(current) / name
            st = item.lstat()
            if item.is_symlink() or getattr(st, 'st_file_attributes', 0) & 0x400 or not item.resolve(strict=True).is_relative_to(resolved):
                raise ValueError('Un lien interdit la suppression du dossier de débogage.')
        for name in files: (Path(current) / name).unlink()
        for name in dirs: (Path(current) / name).rmdir()
    resolved.rmdir()


def remove(game):
    if bibliotheque.game_running(game): raise ValueError('Ferme le jeu avant de supprimer sa sauvegarde de débogage.')
    folder = _safe_folder(game)
    if folder is None: raise ValueError('Aucune sauvegarde de débogage active pour ce jeu.')
    _remove_owned(folder, _root())
