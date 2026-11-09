"""Gestion des fichiers de jeux sans modifier les sauvegardes."""
import errno
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import bibliotheque

CONSOLES = ('PS1', 'PS2', 'NES', 'SNES', 'N64', 'DC')


def console_directory(root, console):
    if console not in CONSOLES:
        raise ValueError('Console inconnue.')
    return Path(root) / console


def _write_paths(paths):
    target = bibliotheque.LIBRARY_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        data = json.loads(target.read_text(encoding='utf-8'))
        if not isinstance(data, dict): data = {}
    except (OSError, ValueError):
        data = {}
    data['jeux'] = list(dict.fromkeys(str(p) for p in paths))
    temp = None
    try:
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=target.parent, delete=False) as stream:
            temp = Path(stream.name)
            json.dump(data, stream, ensure_ascii=False, indent=2)
        os.replace(temp, target)
    finally:
        if temp is not None: temp.unlink(missing_ok=True)


def replace_registration(old, new=None):
    old = str(Path(old).resolve()).casefold()
    paths = [p for p in bibliotheque.registered_games() if str(p.resolve()).casefold() != old]
    if new is not None and not any(str(p.resolve()).casefold() == str(Path(new).resolve()).casefold() for p in paths):
        paths.append(Path(new).resolve())
    _write_paths(paths)


def games_in_directory(root):
    root = Path(root).resolve()
    return [g for g in bibliotheque.discover_games(root, include_cover=False)
            if Path(g['path']).resolve().is_relative_to(root)]


def recycle_game(game):
    path = Path(game['path'])
    if path != path.resolve(strict=True) or path.is_symlink():
        raise ValueError('Le chemin du jeu a changé.')
    current = bibliotheque.game_info(path, include_cover=False)
    if current['id'] != game['id']:
        raise ValueError('Le fichier du jeu a changé ; actualise la bibliothèque.')
    if bibliotheque.game_running(current):
        raise ValueError('Ferme le jeu avant de le supprimer.')
    env = os.environ.copy()
    env['PHANTOM_RECYCLE_GAME'] = str(path)
    script = r'''$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName Microsoft.VisualBasic
[Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile(
    $env:PHANTOM_RECYCLE_GAME,
    [Microsoft.VisualBasic.FileIO.UIOption]::OnlyErrorDialogs,
    [Microsoft.VisualBasic.FileIO.RecycleOption]::SendToRecycleBin)'''
    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                            env=env, capture_output=True, text=True, encoding='utf-8', errors='replace',
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), timeout=60)
    if result.returncode or path.exists():
        raise OSError((result.stderr or result.stdout or 'Le jeu n’a pas été déplacé dans la Corbeille.').strip())
    replace_registration(path)


def _identity(path):
    st = path.stat()
    return st.st_size, st.st_mtime_ns, st.st_ino


def move_games(games, old_root, new_root, progress=lambda *_: None):
    """Déplacer uniquement les EXE validés, sans écraser de fichier existant."""
    old_root, new_root = Path(old_root).resolve(), Path(new_root).resolve()
    if old_root == new_root: return []
    plan, destinations = [], set()
    for game in games:
        src = Path(game['path'])
        if src != src.resolve(strict=True) or src.is_symlink() or not src.is_relative_to(old_root):
            raise ValueError('Le chemin du jeu a changé : ' + str(src))
        dst = console_directory(new_root, game['platform']) / src.name
        if src == dst:
            continue
        key = str(dst).casefold()
        if key in destinations or dst.exists():
            raise FileExistsError('Un fichier porte déjà ce nom : ' + str(dst))
        destinations.add(key)
        plan.append((game, src, dst, _identity(src)))
    moved = []
    for index, (game, src, dst, identity) in enumerate(plan, 1):
        progress(index, len(plan), game['name'])
        if _identity(src) != identity or bibliotheque.game_info(src, include_cover=False)['id'] != game['id']:
            raise ValueError('Le jeu a changé avant son déplacement : ' + str(src))
        if bibliotheque.game_running(game):
            raise ValueError('Ferme ce jeu avant de le déplacer : ' + game['name'])
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.parent.resolve() != dst.parent or not dst.parent.resolve().is_relative_to(new_root):
            raise ValueError('Le dossier de la console est un lien ou a changé : ' + str(dst.parent))
        if dst.exists(): raise FileExistsError('Un fichier existe déjà : ' + str(dst))
        try:
            os.rename(src, dst)
        except OSError as exc:
            if exc.errno != errno.EXDEV and getattr(exc, 'winerror', None) != 17: raise
            temp = None
            try:
                with tempfile.NamedTemporaryFile(dir=dst.parent, suffix='.part', delete=False) as stream:
                    temp = Path(stream.name)
                    with src.open('rb') as source:
                        shutil.copyfileobj(source, stream, 4 * 1024 * 1024)
                if _identity(src) != identity:
                    raise OSError('Le jeu a changé pendant la copie : ' + src.name)
                with src.open('rb') as a, temp.open('rb') as b:
                    if hashlib.file_digest(a, 'sha256').digest() != hashlib.file_digest(b, 'sha256').digest():
                        raise OSError('La vérification de la copie a échoué : ' + src.name)
                if _identity(src) != identity: raise OSError('Le jeu a changé pendant la vérification : ' + src.name)
                os.rename(temp, dst)
                temp = None
                replace_registration(src, dst)
                src.unlink()
            finally:
                if temp is not None: temp.unlink(missing_ok=True)
        else:
            try: replace_registration(src, dst)
            except Exception:
                if not src.exists(): os.rename(dst, src)
                raise
        moved.append(dst)
    return moved
