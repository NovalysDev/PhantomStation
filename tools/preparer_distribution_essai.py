"""Assemble a clean local PhantomStation test distribution from verified tools.

No game image, save, project, or generated game executable is copied. This is
an internal staging tool, not a publisher or a licensing check.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
STAGE = BASE / 'test-local' / 'distribution-essai'
GAME_EXTENSIONS = {'.iso', '.bin', '.cue', '.z64', '.n64', '.v64', '.nes',
                   '.sfc', '.smc', '.sav', '.state', '.mcd', '.ps2', '.sbi'}


def ignore_for(source, root_excluded=()):
    def ignore(directory, names):
        excluded = {name for name in names if name in {'__pycache__', '_triage'}
                    or name.endswith(('.pyc', '.pyo'))
                    or Path(name).suffix.lower() in GAME_EXTENSIONS}
        if Path(directory) == source:
            excluded.update(set(names).intersection(root_excluded))
        return excluded
    return ignore


def copy_tree(source, destination, root_excluded=()):
    if not source.is_dir():
        raise RuntimeError('Dossier requis introuvable : ' + str(source))
    shutil.copytree(source, destination, ignore=ignore_for(source, root_excluded), symlinks=False)


def main():
    global STAGE
    parser = argparse.ArgumentParser(description='Préparer un dossier autonome de test')
    parser.add_argument('destination', type=Path, help='Nouveau dossier vide à créer')
    parser.add_argument('--preparation', type=Path, default=STAGE,
                        help='Dossier de travail contenant les moteurs et outils préparés')
    parser.add_argument('--goldeneye', type=Path, default=BASE / 'n64-engine/goldeneye-pal',
                        help='Dossier local du moteur GoldenEye, si disponible')
    args = parser.parse_args()
    STAGE = args.preparation.resolve()
    destination = args.destination.resolve()
    if destination.exists():
        raise SystemExit('La destination existe déjà : ' + str(destination))
    if destination == BASE or destination in BASE.parents or STAGE in destination.parents:
        raise SystemExit('La destination doit être distincte des sources et du dossier de préparation.')
    required = ['runtime-python/python.exe', 'runtime-python/pythonw.exe',
                'outils/git-portable/cmd/git.exe', 'outils/dependances-git/psxrecomp.git/HEAD',
                'outils/dependances-git/recomp-ui.git/HEAD',
                'outils/dependances-git/recomp-net.git/HEAD',
                'outils/dependances-git/rbengine.git/HEAD', 'toolchain/bin/cmake.exe',
                'psxrecomp/tools/new_project_layout/setup_project.ps1',
                'nintendo-recomp/.deps/nesrecomp-phantom/common/nes_cart.h',
                'nintendo-recomp/.deps/snesrecomp/runner/runner.cmake',
                'ps2-native-recomp/tools/portable/mingit/usr/bin/bash.exe',
                'n64-engine/generique/n64recomp.exe', 'PhantomStation.exe']
    missing = [name for name in required if not (STAGE / name).is_file()]
    if missing:
        raise SystemExit('Préparation incomplète :\n' + '\n'.join(missing))

    destination.mkdir(parents=True)
    for file in STAGE.iterdir():
        if file.is_file() and (file.suffix.lower() in {'.py', '.cs', '.h', '.hpp'} or
                               file.name in {'phantom_version.json', 'ps2-source-manifest.txt',
                                             'LISEZ-MOI.md', 'PhantomStation.exe', 'unique_stub.exe'}):
            shutil.copy2(file, destination / file.name)
    for name, excluded in (
        ('runtime-python', ()), ('outils/git-portable', ()), ('outils/dependances-git', ()),
        ('outils/archives-ps1', ()),
        ('vendor/interface', ()), ('assets', ()), ('bios-personnalise', ()), ('toolchain', ()),
        ('psxrecomp', ('.git', '.cache', 'generated')),
        ('n64-engine/generique', ()), ('n64-recomp-lab/moteur-n64-commun', ('.git',)),
        ('nintendo-recomp', ('.git', '.deps', '.build', 'Export', 'MEDIAS', 'site', 'tests', 'docs')),
        ('ps2-native-recomp', ('games', 'generated', 'build', '.git', '.github', 'web')),
    ):
        print('Préparation :', name, flush=True)
        copy_tree(STAGE / name, destination / name, excluded)

    # This is the application-supplied blank memory card, not a user's save.
    blank_card = STAGE / 'assets/carte-ps2-vierge.ps2'
    if not blank_card.is_file():
        raise RuntimeError('Carte mémoire PS2 vierge du programme introuvable.')
    shutil.copy2(blank_card, destination / 'assets/carte-ps2-vierge.ps2')
    bios = STAGE / 'bios-personnalise/phantom-bios.bin'
    if not bios.is_file():
        raise RuntimeError('BIOS PhantomStation du programme introuvable.')
    shutil.copy2(bios, destination / 'bios-personnalise/phantom-bios.bin')

    # These are source checkouts inspected by the Nintendo engine. Re-clone
    # only the checked-out commit and remove remotes and local reflogs.
    dest_deps = destination / 'nintendo-recomp/.deps'
    dest_deps.mkdir()
    copy_tree(STAGE / 'nintendo-recomp/.deps/SDL', dest_deps / 'SDL')
    git = destination / 'outils/git-portable/cmd/git.exe'
    for name in ('nesrecomp-phantom', 'snesrecomp'):
        original = STAGE / 'nintendo-recomp/.deps' / name
        checkout = dest_deps / name
        subprocess.run([str(git), '-c', 'protocol.file.allow=always', 'clone', '--quiet',
                        '--depth', '1', original.resolve().as_uri(), str(checkout)], check=True)
        subprocess.run([str(git), '-C', str(checkout), 'remote', 'remove', 'origin'], check=True)
    shutil.copy2(STAGE / 'nintendo-recomp/.deps/nesrecomp-phantom/common/nes_cart.h',
                 dest_deps / 'nesrecomp-phantom/common/nes_cart.h')

    # GoldenEye's dedicated port contains material with unresolved provenance.
    # Keep this local-only build explicit; it must be reviewed before sharing.
    goldeneye = args.goldeneye.resolve()
    if goldeneye.is_dir():
        print('Préparation : moteur GoldenEye, usage local de test uniquement', flush=True)
        copy_tree(goldeneye, destination / 'n64-engine/goldeneye-pal', ('.git',))
    (destination / 'DISTRIBUTION-ESSAI.txt').write_text(
        'Prototype local de test. Ne pas publier avant validation des licences, '
        'des contenus et des essais sur un PC indépendant.\n', encoding='utf-8')
    print('Dossier de test préparé :', destination, flush=True)


if __name__ == '__main__':
    main()
