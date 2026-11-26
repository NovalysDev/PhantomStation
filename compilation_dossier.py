"""Compilation d'un dossier entier : trouver les jeux d'une console dans un
dossier (et ses sous-dossiers) et leur donner un titre propre.

Le titre garde le nom d'origine du fichier, sans les mentions entre
parenthèses ou crochets (région, langues, date, révision, numéro de disque) :
« Re-Volt (Europe) (En,Fr,De,Es,It) » devient « Re-Volt ».
"""
from __future__ import annotations

import os
import re
from pathlib import Path

FORMATS = {'PS1': ('.cue', '.iso', '.bin'), 'PS2': ('.iso',), 'DC': ('.chd',), 'NES': ('.nes',),
           'SNES': ('.sfc', '.smc'), 'N64': ('.z64', '.v64', '.n64')}
ARCHIVES = ('.zip', '.7z', '.rar')
PROFONDEUR = 3            # le dossier choisi et deux niveaux de sous-dossiers
DISQUE = re.compile(r'\s*[\[(]\s*(?:disc|disk|disque|cd)\s*[-_. ]*(\d+)(?:\s*(?:of|sur|/)\s*\d+)?\s*[\])]', re.I)
ARTICLE_FINAL = re.compile(r'^(?P<titre>.+?),\s*(?P<article>The|A|An)(?P<suite>\s+-\s+.*)?$', re.I)
# « Disney's », « Disney-Pixar », « Pixar's » devant le titre (pas « Walt Disney World »).
MARQUE = re.compile(r"^(?:Walt\s+)?(?:Disney|Pixar)(?:['’]s)?(?:\s*[-/&•·]\s*(?:Disney|Pixar)(?:['’]s)?)?\s+(?!World\b)", re.I)


def sans_marque(titre: str) -> str:
    """« Disney-Pixar Toy Story 2 » -> « Toy Story 2 »."""
    reste = MARQUE.sub('', titre, count=1).strip(' -:')
    return reste or titre


def clean_game_title(text: str) -> str:
    """Titre d'origine sans région, langues, date, révision, numéro de disque
    ni marque Disney / Pixar devant. « Legend of Zelda, The - X » redevient
    « The Legend of Zelda - X »."""
    titre = text.replace('_', ' ')
    titre = re.sub(r'\s*[\[(][^\])]*[\])]', ' ', titre)
    titre = re.sub(r'\s+', ' ', titre).strip(' -.,')
    m = ARTICLE_FINAL.match(titre)
    if m:
        titre = f"{m.group('article')} {m.group('titre')}{m.group('suite') or ''}"
    titre = sans_marque(titre)
    return titre or text.strip()


def disc_number(stem: str) -> int | None:
    m = DISQUE.search(stem)
    return int(m.group(1)) if m else None


def _cle(path: Path) -> str:
    try:
        path = path.resolve()
    except OSError:
        path = path.absolute()
    return os.path.normcase(str(path))


def _fichiers_du_cue(cue: Path) -> set[str]:
    """Pistes citées par un .cue : elles font partie du même disque."""
    try:
        if cue.stat().st_size > 256 * 1024:
            return set()
        texte = cue.read_text(encoding='utf-8', errors='replace')
    except OSError:
        return set()
    noms = re.findall(r'^\s*FILE\s+(?:"([^"]+)"|(\S+))', texte, re.I | re.M)
    return {_cle(cue.parent / (a or b)) for a, b in noms}


def scan_folder(folder, console, iso_platform=None):
    """Jeux de cette console dans le dossier : rien n'est écarté.

    Renvoie (jeux, archives) : jeux = [(titre, [chemins des disques], console)]
    dans l'ordre des dossiers. Les disques d'un même jeu PS1 ou Dreamcast vont
    ensemble ; deux versions d'un jeu restent deux jeux ; un .iso PS1 trouvé
    en PS2 (ou l'inverse) est compilé avec sa vraie console. Les archives
    (.zip, .7z, .rar) sont à extraire puis compiler (extraire_archive)."""
    folder = Path(folder)
    extensions = FORMATS[console]
    fichiers, archives = [], []
    for racine, dossiers, noms in os.walk(folder):
        if len(Path(racine).relative_to(folder).parts) >= PROFONDEUR - 1:
            dossiers[:] = []
        dossiers.sort(key=str.casefold)
        for nom in sorted(noms, key=str.casefold):
            chemin = Path(racine) / nom
            suffixe = chemin.suffix.lower()
            if suffixe in extensions:
                fichiers.append(chemin)
            elif suffixe in ARCHIVES:
                archives.append(chemin)
    if console == 'PS1':
        pistes = set()
        for cue in (p for p in fichiers if p.suffix.lower() == '.cue'):
            pistes |= _fichiers_du_cue(cue)
        fichiers = [p for p in fichiers if not (p.suffix.lower() == '.bin' and
                                                (_cle(p) in pistes or p.with_suffix('.cue').is_file()))]
    consoles = {}
    for p in fichiers:
        autre = iso_platform(p) if iso_platform and p.suffix.lower() == '.iso' else None
        consoles[_cle(p)] = autre if autre in ('PS1', 'PS2') else console
    groupes: dict[tuple, list[Path]] = {}
    for p in fichiers:
        sa_console = consoles[_cle(p)]
        # Les disques d'un même jeu ne diffèrent que par « (Disc N) » : PS1 et
        # Dreamcast les compilent ensemble ; la PS2 compile chaque disque à part.
        ensemble = disc_number(p.stem) and sa_console in ('PS1', 'DC')
        cle = (sa_console, DISQUE.sub('', p.stem).strip().casefold() if ensemble else _cle(p))
        groupes.setdefault(cle, []).append(p)
    jeux = []
    for (sa_console, _), disques in groupes.items():
        disques.sort(key=lambda p: (disc_number(p.stem) or 0, p.name.casefold()))
        jeux.append((clean_game_title(DISQUE.sub('', disques[0].stem)), disques, sa_console))
    return jeux, archives


def outil_7z() -> str | None:
    """7-Zip, comme pour les archives SBI : sur le PATH, installé, ou fourni."""
    import shutil
    base = Path(__file__).resolve().parent
    for chemin in (shutil.which('7z'), str(Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / '7-Zip/7z.exe'),
                   str(base / 'toolchain/bin/7z.exe')):
        if chemin and Path(chemin).is_file():
            return chemin
    return None


def extraire_archive(archive, destination) -> None:
    """Extrait une archive de jeux dans un dossier vide. Un .zip est lu
    directement ; .7z et .rar passent par 7-Zip. Lève OSError en cas d'échec."""
    import subprocess
    import zipfile
    archive, destination = Path(archive), Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    if archive.suffix.lower() == '.zip':
        racine = destination.resolve()
        with zipfile.ZipFile(archive) as z:
            for entree in z.infolist():
                cible = (destination / entree.filename).resolve()
                if racine not in cible.parents and cible != racine:
                    raise OSError('Chemin invalide dans l’archive : ' + entree.filename)
            z.extractall(destination)
        return
    outil = outil_7z()
    if not outil:
        raise OSError('7-Zip est nécessaire pour extraire ' + archive.name)
    fini = subprocess.run([outil, 'x', '-y', '-bd', '-o' + str(destination), str(archive)],
                          capture_output=True, text=True, errors='replace',
                          creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if fini.returncode:
        raise OSError('Extraction impossible de ' + archive.name + ' : ' + (fini.stderr or fini.stdout).strip()[-300:])
