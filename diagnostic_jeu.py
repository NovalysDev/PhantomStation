"""Prepare an explicit, game-specific diagnostic without sending it."""
from datetime import datetime
import os
from pathlib import Path
import platform
import re
import uuid
import zipfile

from bibliotheque import documents_folder


def create_game_diagnostic(info, description, journals_root=None, reports_root=None):
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
    files = [(current, 'journal.log')]
    summary = ('Rapport de jeu PhantomStation\nDate : ' + datetime.now().astimezone().strftime('%d/%m/%Y %H:%M:%S %z')
               + '\nJeu : ' + title + '\nConsole : ' + str(info.get('platform') or 'inconnue')
               + '\nSystème : ' + platform.platform() + '\n\nProblème :\n' + description
               + '\n\nJournaux joints : ' + (', '.join(n for _, n in files) if files else 'aucun journal disponible') + '\n')
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
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return path, report_text
