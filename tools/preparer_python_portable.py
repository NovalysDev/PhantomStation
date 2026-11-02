"""Prepare PhantomStation's Windows Python runtime in a new staging directory.

This is a developer-side preparation step. It never changes an existing
installation, downloads packages, or copies the developer's full site-packages.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


def copy_directory(source: Path, destination: Path, ignored=()):
    if not source.is_dir():
        raise RuntimeError(f"Dossier requis introuvable : {source}")
    shutil.copytree(source, destination, ignore=shutil.ignore_patterns(*ignored), symlinks=False)


def main():
    parser = argparse.ArgumentParser(description="Préparer Python pour une distribution PhantomStation")
    parser.add_argument('destination', type=Path, help='Nouveau dossier runtime-python à créer')
    args = parser.parse_args()
    if sys.platform != 'win32' or sys.version_info < (3, 11):
        raise SystemExit('Un Python Windows 3.11 ou plus récent est requis.')
    source = Path(sys.base_prefix).resolve(strict=True)
    destination = args.destination.resolve()
    if destination.exists():
        raise SystemExit('Le dossier de destination existe déjà : ' + str(destination))
    if source == destination or source in destination.parents:
        raise SystemExit('La destination doit être hors de l’installation Python source.')

    # Resolve required packages before creating anything. Only these packages
    # and their own distribution metadata are copied from site-packages.
    import PIL
    import numpy
    packages = [Path(PIL.__file__).resolve().parent, Path(numpy.__file__).resolve().parent]
    site = packages[0].parent
    if any(package.parent != site for package in packages):
        raise SystemExit('Pillow et NumPy doivent se trouver dans le même site-packages.')
    metadata = sorted(site.glob('pillow-*.dist-info')) + sorted(site.glob('numpy-*.dist-info'))
    if len(metadata) != 2:
        raise SystemExit('Les métadonnées de licence Pillow ou NumPy sont introuvables ou ambiguës.')
    required_files = ['python.exe', 'pythonw.exe', 'python3.dll',
                      f'python{sys.version_info.major}{sys.version_info.minor}.dll',
                      'vcruntime140.dll', 'vcruntime140_1.dll', 'LICENSE.txt']
    for name in required_files:
        if not (source / name).is_file():
            raise SystemExit('Fichier Python introuvable : ' + name)

    destination.mkdir(parents=True)
    for name in required_files:
        shutil.copy2(source / name, destination / name)
    copy_directory(source / 'DLLs', destination / 'DLLs', ('__pycache__', '*.pyc'))
    copy_directory(source / 'tcl', destination / 'tcl', ('__pycache__', '*.pyc'))
    copy_directory(source / 'Lib', destination / 'Lib', ('site-packages', '__pycache__', '*.pyc', 'test', 'tests'))
    target_site = destination / 'Lib' / 'site-packages'
    target_site.mkdir()
    for package in packages + metadata:
        copy_directory(package, target_site / package.name, ('__pycache__', '*.pyc', 'tests'))
    numpy_libs = site / 'numpy.libs'
    if numpy_libs.is_dir():
        copy_directory(numpy_libs, target_site / numpy_libs.name)

    env = os.environ.copy()
    for key in ('PYTHONHOME', 'PYTHONPATH', 'PYTHONUSERBASE', 'VIRTUAL_ENV'):
        env.pop(key, None)
    env['PYTHONNOUSERSITE'] = '1'
    probe = ('import sys, tkinter, PIL, numpy; '
             'assert sys.prefix == sys.base_prefix; '
             'print(sys.executable); print("Tk", tkinter.TkVersion, "Pillow", PIL.__version__, "NumPy", numpy.__version__)')
    subprocess.run([str(destination / 'python.exe'), '-I', '-c', probe], env=env, check=True)
    print('Python autonome préparé :', destination)


if __name__ == '__main__':
    main()
