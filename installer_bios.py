"""Ajout expérimental du BIOS PhantomStation à un projet PSXRecomp précis."""
import os
import re
import shutil
import subprocess
from pathlib import Path

BASE = Path(__file__).resolve().parent
BIOS = BASE / 'bios-personnalise'
REVISION = 'a7b37e079e8c6e98e04cd1cd42ba559e95911ea7'


def install_phantom_bios(project, env):
    project = Path(project)
    framework = project / 'psxrecomp'
    result = subprocess.run(['git', '-C', str(framework), 'rev-parse', 'HEAD'],
                            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, errors='replace', creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode or result.stdout.strip() != REVISION:
        raise ValueError('Le BIOS PhantomStation expérimental nécessite la version PSXRecomp vérifiée. Le moteur a changé : intégration arrêtée pour éviter un jeu défectueux.')
    for src, dest in [('phantom-bios.bin', 'bios/openbios.bin'), ('OpenBIOS.toml', 'bios/OpenBIOS.toml'),
                      ('phantom_elf_seeds.json', 'recompiler/seeds/phantom_elf_seeds.json'),
                      ('OpenBIOS_dispatch.c', 'generated/OpenBIOS_dispatch.c'),
                      ('OpenBIOS_full.c', 'generated/OpenBIOS_full.c'),
                      ('LICENSE-OpenBIOS.txt', 'bios/PhantomStation.LICENSE')]:
        source = BIOS / src
        if not source.is_file(): raise ValueError('Composant du BIOS PhantomStation manquant : ' + src)
        (framework / dest).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, framework / dest)
    cmake = str(BASE / 'toolchain' / 'bin' / 'cmake.exe')
    stamp = subprocess.run([cmake, '-DROOT=' + framework.as_posix(), '-DPROFILE=bios/OpenBIOS.toml',
                            '-P', str(framework / 'tools' / 'bios_emitter_fingerprint.cmake')],
                           cwd=str(framework), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           text=True, errors='replace', creationflags=subprocess.CREATE_NO_WINDOW)
    if stamp.returncode: raise ValueError('Vérification du BIOS impossible : ' + stamp.stderr.strip())
    (framework / 'generated' / 'OpenBIOS.emitter.sha').write_text(stamp.stdout, encoding='ascii')
    cmake_file = project / 'CMakeLists.txt'
    cmake_text = cmake_file.read_text(encoding='utf-8')
    marker = 'set(PSX_NATIVE_OPENBIOS_READ OFF CACHE BOOL "" FORCE)'
    if marker not in cmake_text:
        cmake_text = marker + '\n' + cmake_text
        cmake_file.write_text(cmake_text, encoding='utf-8')
    game_file = project / 'game.toml'
    game = game_file.read_text(encoding='utf-8-sig')
    section = re.search(r'(?ms)^\[runtime\][ \t]*\r?\n(.*?)(?=^\[|\Z)', game)
    if section:
        body = section.group(1)
        if re.search(r'(?m)^bios_hle\s*=', body):
            body = re.sub(r'(?m)^bios_hle\s*=.*$', 'bios_hle = false', body, count=1)
        else:
            body = 'bios_hle = false\n' + body
        game = game[:section.start(1)] + body + game[section.end(1):]
    else:
        game += '\n[runtime]\nbios_hle = false\n'
    game_file.write_text(game, encoding='utf-8')
