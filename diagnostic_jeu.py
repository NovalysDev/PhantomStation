"""Prepare an explicit, game-specific diagnostic without sending it."""
from datetime import datetime
import hashlib
import os
from pathlib import Path
import platform
import re
import uuid
import zipfile

from bibliotheque import documents_folder, save_directory, _save_files


def game_save_files(info, selected=None):
    """Find the game's own saves and the cards selected for this session."""
    selected = selected or {}
    console = info.get('platform')
    files = []
    folder = (Path(selected['PHANTOM_SELECTED_PROFILE_DIR']) if selected.get('PHANTOM_SELECTED_PROFILE_DIR') else
              save_directory(info))
    # A selected Dreamcast card replaces the game's default VMU.
    if not (console == 'DC' and selected.get('PHANTOM_SELECTED_DC_CARD')):
        for item in _save_files(folder):
            files.append((item, 'sauvegardes/jeu/' + item.relative_to(folder).as_posix()))
    keys = ('PHANTOM_SELECTED_DC_CARD', 'PHANTOM_SELECTED_PS1_CARD1', 'PHANTOM_SELECTED_PS1_CARD2',
            'PHANTOM_SELECTED_PS2_CARD', 'PHANTOM_SELECTED_PS2_CARD2')
    for index, key in enumerate(keys, 1):
        if selected.get(key):
            path = Path(selected[key])
            if path.is_symlink() or not path.is_file() or getattr(path.lstat(), 'st_file_attributes', 0) & 0x400:
                raise ValueError('La carte utilisée par le jeu est introuvable ou invalide.')
            files.append((path, f'sauvegardes/carte-{index}/{path.name}'))
    return files


def game_learned_blocks(info, journal):
    """Return only Dreamcast memory blocks recorded during this journal's session."""
    if info.get('platform') != 'DC':
        return []
    ident = str(info.get('id', ''))
    if not re.fullmatch(r'[0-9a-f]{32}', ident):
        raise ValueError('Identité du jeu invalide.')
    local = Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData/Local')) / 'PhantomStation'
    boot = local / 'Jeux' / ident / '1ST_READ.BIN'
    if not boot.is_file() or boot.is_symlink():
        return []
    with boot.open('rb') as source:
        digest = hashlib.file_digest(source, 'sha1').hexdigest()
    root = local / 'Dreamcast-apprentissage' / digest
    if not root.is_dir() or root.is_symlink():
        return []
    with Path(journal).open('r', encoding='utf-8-sig', errors='replace') as source:
        first = source.readline(2048)
    match = re.search(r'\| (\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) \|', first)
    if not match:
        return []
    started = datetime.strptime(match.group(1), '%Y-%m-%d %H:%M:%S').timestamp()
    candidates = []
    for folder in root.iterdir():
        calls = folder / 'appels.txt'
        if (re.fullmatch(r'partie-\d{8}-\d{6}', folder.name) and folder.is_dir() and not folder.is_symlink()
                and calls.is_file() and not calls.is_symlink() and calls.stat().st_mtime >= started - 2):
            candidates.append((folder.name, folder))
    if not candidates:
        return []
    folder = max(candidates)[1]
    result = [(folder / 'appels.txt', 'blocs-dreamcast/appels.txt')]
    for item in sorted(folder.iterdir()):
        if re.fullmatch(r'bloc-\d{3}\.bin', item.name) and item.is_file() and not item.is_symlink():
            result.append((item, 'blocs-dreamcast/' + item.name))
    return result


def create_game_diagnostic(info, description, journals_root=None, reports_root=None, save_files=(), include_blocks=False):
    ident = str(info.get('id', ''))
    if not re.fullmatch(r'[0-9a-f]{32}', ident):
        raise ValueError('Identité du jeu invalide.')
    description = str(description).strip()
    if len(description) < 5:
        raise ValueError('Décris brièvement le problème.')
    title = str(info.get('name') or 'Jeu')
    local = Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData/Local'))
    journals_root = Path(journals_root) if journals_root else local / 'PhantomStation/Journaux/Jeux'
    reports_root = Path(reports_root) if reports_root else documents_folder() / 'PhantomStation/Rapports'
    reports_root.mkdir(parents=True, exist_ok=True)
    candidates = []
    if journals_root.is_dir():
        for folder in journals_root.iterdir():
            if not folder.is_dir() or folder.is_symlink() or not folder.name.endswith(' - ' + ident):
                continue
            journal = folder / 'journal.log'
            if journal.is_file() and not journal.is_symlink(): candidates.append(journal)
    current = max(candidates, key=lambda p: (p.stat().st_mtime_ns, p.name), default=None)
    if current is None:
        raise ValueError('Aucun journal pour ce jeu : lance une partie avant de créer le diagnostic.')
    files = [(current, 'journal.log')] + list(save_files)
    if include_blocks:
        files += game_learned_blocks(info, current)
    summary = ('Rapport de jeu PhantomStation\nDate : ' + datetime.now().astimezone().strftime('%d/%m/%Y %H:%M:%S %z')
               + '\nJeu : ' + title + '\nConsole : ' + str(info.get('platform') or 'inconnue')
               + '\nSystème : ' + platform.platform() + '\n\nProblème :\n' + description
               + '\n\nFichiers joints : ' + ', '.join(n for _, n in files) + '\n')
    excerpt = ''
    if current:
        with current.open('rb') as stream:
            stream.seek(max(0, current.stat().st_size - 1600))
            excerpt = stream.read().decode('utf-8', errors='replace').replace('\x00', '')
    report_text = summary + ('\n--- Dernières lignes du jeu ---\n' + excerpt if excerpt else '')
    path = reports_root / ('diagnostic-jeu-' + datetime.now().strftime('%Y-%m-%d_%H-%M-%S') + '-' + uuid.uuid4().hex[:8] + '.zip')
    try:
        with zipfile.ZipFile(path, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as out:
            out.writestr('rapport.txt', summary)
            for source, name in files:
                out.write(source, name)
        if path.stat().st_size > 8 * 1024 * 1024:
            raise ValueError('Le rapport dépasse la limite d’envoi de 8 Mio. Réessaie sans sauvegarde.')
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return path, report_text
