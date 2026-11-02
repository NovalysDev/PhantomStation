"""Recompilation expérimentale NES/SNES et emballage PhantomStation."""
import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unicodedata
import urllib.parse
import urllib.request
import uuid
import wave
import zipfile
from pathlib import Path
from phantom_portable import configure_portable_tools, safe_build_path

from PIL import Image
from emballer import _decode_cover
from phantom_n64_rom import N64_EXTENSIONS, read_n64_rom

BASE = Path(__file__).resolve().parent
configure_portable_tools(BASE)
ENGINE = BASE / 'nintendo-recomp'
TOOLBIN = BASE / 'toolchain' / 'bin'
CSC = Path(os.environ.get('WINDIR', r'C:\Windows')) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
N64_ENGINE = BASE / 'n64-engine' / 'goldeneye-pal'
N64_COMMON = BASE / 'n64-engine' / 'generique'
N64_SOURCE = BASE / 'n64-recomp-lab' / 'moteur-n64-commun'
GOLDENEYE_PAL_SHA1 = '167c3c433dec1f1eb921736f7d53fac8cb45ee31'
N64_HASH_CATALOGUE = 'https://raw.githubusercontent.com/RetroAchievements/RASuite/master/bizhawk/output/gamedb/gamedb_n64.txt'
N64_CODES_BASE = 'https://raw.githubusercontent.com/libretro/libretro-database/master/cht/Nintendo%20-%20Nintendo%2064/'


def configure_engine():
    if not (ENGINE / 'retrorecomp/console16.py').is_file():
        raise RuntimeError('Le moteur Nintendo est absent du dossier PhantomStation.')
    if not all((TOOLBIN / name).is_file() for name in ('cmake.exe', 'ninja.exe', 'clang.exe', 'clang++.exe', 'llvm-windres.exe')):
        raise RuntimeError('Les outils de compilation Nintendo sont absents.')
    sys.path.insert(0, str(ENGINE))
    os.environ['PATH'] = str(TOOLBIN) + os.pathsep + os.environ.get('PATH', '')
    from retrorecomp import toolchain
    toolchain.select = lambda: toolchain.Selection(toolchain.LLVM, TOOLBIN / 'cmake.exe', 'Ninja')
    toolchain.cmake_arguments = lambda: [
        '-DCMAKE_BUILD_TYPE=Release', '-DCMAKE_C_COMPILER=' + (TOOLBIN / 'clang.exe').as_posix(),
        '-DCMAKE_CXX_COMPILER=' + (TOOLBIN / 'clang++.exe').as_posix(),
        '-DCMAKE_RC_COMPILER=' + (TOOLBIN / 'llvm-windres.exe').as_posix(),
        '-DCMAKE_MAKE_PROGRAM=' + (TOOLBIN / 'ninja.exe').as_posix(), '-DCMAKE_EXE_LINKER_FLAGS=-static -s']
    from retrorecomp import nes
    from retrorecomp.nes_codegen import prepare_compiler
    from retrorecomp.platforms import sdl_install
    engine = ENGINE / '.deps/nesrecomp-phantom'
    if not (engine / 'common/nes_cart.h').is_file() or not (sdl_install() / 'lib/libSDL2.a').is_file():
        raise RuntimeError('Une dépendance Nintendo est absente ; le moteur doit être réinstallé.')
    # The host supplies PAL timing; the pinned generator otherwise rejects NES 2 PAL headers.
    header = (engine / 'common/nes_cart.h').read_bytes()
    if hashlib.sha256(header).hexdigest() != 'cbe0b7f4b67510f2d8cb6483cd2068991d0c430eecf8033b512e078ffe188f92':
        raise RuntimeError('Le correctif PAL du moteur NES est absent.')
    revision = subprocess.run(['git', '-C', str(engine), 'rev-parse', 'HEAD'], capture_output=True,
                              text=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if revision.returncode or revision.stdout.strip() != '1b0c621a927db17afa9723bf456a89ad15809907':
        raise RuntimeError('La version du moteur NES ne correspond pas à celle vérifiée.')
    def dependencies(emit):
        compiler = prepare_compiler(engine, TOOLBIN / 'cmake.exe', 'Ninja', emit)
        return engine, compiler, sdl_install(), TOOLBIN / 'cmake.exe', 'Ninja'
    nes._dependencies = dependencies


def cover_catalogue_matches(system, names):
    """Find exact game titles when the catalogue adds a region or revision."""
    def key(value):
        value = re.sub(r'\s*[\(\[].*?[\)\]]', '', value).casefold()
        value = unicodedata.normalize('NFKD', value)
        return ''.join(ch for ch in value if ch.isalnum())

    wanted = {key(value) for value in names if key(value)}
    if not wanted:
        return []
    api = 'https://api.github.com/repos/libretro-thumbnails/' + system + '/git/trees/'
    def get_json(url):
        request = urllib.request.Request(url, headers={'User-Agent': 'PhantomStation/1.0', 'Accept': 'application/vnd.github+json'})
        with urllib.request.urlopen(request, timeout=10) as response:
            raw = response.read(8 * 1024 * 1024 + 1)
        if len(raw) > 8 * 1024 * 1024:
            raise ValueError('Catalogue de jaquettes trop volumineux.')
        return json.loads(raw)
    try:
        root = get_json(api + 'master')
        boxarts = next(entry['sha'] for entry in root['tree'] if entry['path'] == 'Named_Boxarts' and entry['type'] == 'tree')
        tree = get_json(api + boxarts)
        if tree.get('truncated'):
            return []
        titles = [Path(entry['path']).stem for entry in tree['tree']
                  if entry.get('type') == 'blob' and entry['path'].lower().endswith('.png')
                  and key(Path(entry['path']).stem) in wanted]
        def rank(title):
            lower = title.casefold()
            return (0 if '(europe)' in lower else 1 if '(world)' in lower else 2,
                    'virtual console' in lower, len(title), lower)
        return sorted(titles, key=rank)[:5]
    except (OSError, ValueError, KeyError, TypeError, StopIteration):
        return []


def make_art(platform, rom, name, chosen, work, log):
    source = _decode_cover(Path(chosen).read_bytes()) if chosen else None
    if source is None:
        system = {'NES': 'Nintendo_-_Nintendo_Entertainment_System',
                  'SNES': 'Nintendo_-_Super_Nintendo_Entertainment_System',
                  'N64': 'Nintendo_-_Nintendo_64'}[platform]
        base = 'https://raw.githubusercontent.com/libretro-thumbnails/' + system + '/master/Named_Boxarts/'
        stripped = re.sub(r'\s*\([^)]*\)', '', rom.stem).strip()
        tried = set()
        def fetch(title):
            nonlocal source
            if title in tried:
                return False
            tried.add(title)
            try:
                with urllib.request.urlopen(urllib.request.Request(base + urllib.parse.quote(title + '.png'), headers={'User-Agent':'PhantomStation/1.0'}), timeout=4) as response:
                    raw = response.read(8 * 1024 * 1024 + 1)
                if len(raw) > 8 * 1024 * 1024:
                    return False
                source = _decode_cover(raw)
                log('Jaquette trouvée : ' + title)
                return True
            except (OSError, ValueError):
                return False
        for title in (rom.stem, stripped + ' (Europe)', stripped, name + ' (Europe)', name):
            if fetch(title):
                break
        if source is None:
            for title in cover_catalogue_matches(system, (rom.stem, name)):
                if fetch(title):
                    break
    if source is None:
        source = _decode_cover((BASE / 'assets/phantom-startup.png').read_bytes())
        log('Jaquette introuvable ou téléchargement impossible : image PhantomStation utilisée. Tu peux choisir une jaquette manuellement.')
    source.convert('RGBA').save(work / 'boxart.png')
    source.convert('RGBA').save(work / 'boxart.tga')
    source.thumbnail((256, 256), Image.Resampling.LANCZOS)
    square = Image.new('RGBA', (256, 256), (4, 10, 24, 255))
    square.alpha_composite(source, ((256-source.width)//2, (256-source.height)//2))
    square.save(work / 'icone.ico', format='ICO', sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])


def n64_default_codes(cartridge, work, log):
    """Only offer direct codes from an exact SHA-1 catalogue match, disabled by default."""
    def download(url, maximum):
        request = urllib.request.Request(url, headers={'User-Agent': 'PhantomStation/1.0'})
        with urllib.request.urlopen(request, timeout=5) as response:
            data = response.read(maximum + 1)
        if len(data) > maximum:
            raise ValueError('Catalogue trop volumineux.')
        return data.decode('utf-8-sig', errors='replace')
    try:
        database = download(N64_HASH_CATALOGUE, 2 * 1024 * 1024)
        names = set()
        for line in database.splitlines():
            parts = line.split('\t')
            if len(parts) >= 4 and parts[0].lower() == cartridge.sha1 and parts[3] == 'N64':
                names.add(parts[2].strip())
        if len(names) != 1:
            log('Codes N64 : version exacte non trouvée, saisie manuelle disponible.')
            return None
        title = names.pop()
        text = download(N64_CODES_BASE + urllib.parse.quote(title + '.cht', safe=''), 1024 * 1024)
        rows = {}
        for line in text.splitlines():
            match = re.fullmatch(r'\s*cheat(\d+)_(desc|code)\s*=\s*"([^"\r\n]*)"\s*', line)
            if match:
                rows.setdefault(int(match.group(1)), {})[match.group(2)] = match.group(3)
        found = []
        for row in rows.values():
            code = re.fullmatch(r'\s*(80|81)([0-9a-fA-F]{6})\s+([0-9a-fA-F]{4})\s*', row.get('code', ''), re.I)
            if not code: continue
            address, value = int(code.group(2), 16), int(code.group(3), 16)
            if address >= 0x800000 or (code.group(1) == '80' and value > 255) or (code.group(1) == '81' and address % 2): continue
            label = re.sub(r'[\t\r\n]', ' ', row.get('desc', 'Code N64')).strip()[:100] or 'Code N64'
            found.append('0\t' + label + '\t' + (code.group(1) + code.group(2) + ' ' + code.group(3)).upper())
        if not found:
            log('Codes N64 : aucun code direct compatible trouvé pour cette version ; saisie manuelle disponible.')
            return None
        path = work / 'codes-n64-defaut.tsv'
        path.write_text('\n'.join(found) + '\n', encoding='utf-8')
        log(f'Codes N64 : {len(found)} code(s) direct(s) proposé(s), désactivés et à vérifier.')
        return path
    except (OSError, ValueError, UnicodeError) as exc:
        log('Codes N64 : recherche indisponible ; saisie manuelle possible (' + str(exc) + ').')
        return None


def build_nintendo_stub(platform, icon, output, work, background=None):
    assets = BASE / 'assets'
    if platform not in ('NES', 'SNES', 'N64'):
        raise ValueError('Console Nintendo inconnue : ' + str(platform))
    logo = assets / ('logo-' + platform.lower() + '.png')
    sound = assets / ('son-demarrage-' + platform.lower() + '.wav')
    if not logo.is_file() or not sound.is_file():
        raise RuntimeError('Logo ou musique Nintendo manquant pour ' + platform + '.')
    original = (BASE / 'unique_launcher.cs').read_text(encoding='utf-8-sig')
    target = '            string game = task.Result;\n'
    if original.count(target) != 1:
        raise RuntimeError('Le lanceur Nintendo ne correspond plus à PhantomStation.')
    original = original.replace(target, target + '            Hide(); var gameWindow = new NintendoGameForm(game); gameWindow.FormClosed += (s, e) => Close(); gameWindow.Show(); return;\n')
    original = original.replace("string name = Path.GetFileNameWithoutExtension(game).Replace('_', ' ').Trim();", "string name = DisplayTitle(PreparedGameTitle).Replace('_', ' ').Trim();")
    source = work / 'lanceur.cs'
    source.write_text(original, encoding='utf-8')
    artwork = Path(background) if background else assets / 'phantom-startup.png'
    command = [str(CSC), '/nologo', '/target:winexe', '/out:' + str(output),
               '/reference:System.Windows.Forms.dll', '/reference:System.Drawing.dll',
               '/reference:System.IO.Compression.dll', '/reference:System.IO.Compression.FileSystem.dll',
               '/win32icon:' + str(icon), '/resource:' + str(artwork) + ',art',
               '/resource:' + str(logo) + ',logo', '/resource:' + str(sound) + ',sound',
               str(source), str(BASE/'phantom_nintendo_launcher.cs'), str(BASE/'phantom_embedded_game.cs')]
    result = subprocess.run(command, capture_output=True, text=True, errors='replace',
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise RuntimeError('Création du lanceur Nintendo impossible : ' + result.stdout.strip())


def package_game(platform, rom, name, runtime, output, icon=None, background=None, log=print, package_id=None, extra_files=None):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    filename = re.sub(r"[^\w. '’-]+", '-', name, flags=re.UNICODE).strip(' .') or 'Jeu Nintendo'
    destination = output / (filename + '.exe')
    number = 2
    while destination.exists():
        destination = output / (filename + f' ({number}).exe')
        number += 1
    partial = output / (destination.name + '.' + uuid.uuid4().hex + '.part')
    try:
        with tempfile.TemporaryDirectory(prefix='phantom-nintendo-') as folder:
            work = Path(folder)
            make_art(platform, rom, name, icon, work, log)
            build_nintendo_stub(platform, work/'icone.ico', partial, work, background)
            game_id = uuid.UUID(hex=package_id).hex if package_id else uuid.uuid4().hex
            with Path(runtime).open('rb') as stream:
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            manifest = game_id + '\nnintendo.exe\n' + digest + '\n' + base64.b64encode(name.encode()).decode() + '\ndocuments-saves-v1\n'
            config = '[game]\nname = ' + json.dumps(name, ensure_ascii=False) + '\nplatform = ' + json.dumps(platform) + '\n'
            with zipfile.ZipFile(partial, 'a', allowZip64=True) as archive:
                archive.writestr('phantom-package.txt', manifest.encode('utf-8'))
                archive.writestr('game.toml', config.encode('utf-8'))
                from detection_doublons import SOURCE_ENTRY, encoded_source
                archive.writestr(SOURCE_ENTRY, encoded_source(platform, [[rom]]))
                archive.write(runtime, 'nintendo.exe', compress_type=zipfile.ZIP_DEFLATED)
                archive.write(work/'boxart.png', 'assets/img/boxart.png', compress_type=zipfile.ZIP_DEFLATED)
                archive.write(work/'boxart.tga', 'assets/img/boxart.tga', compress_type=zipfile.ZIP_DEFLATED)
                for path, archive_name in extra_files or []:
                    archive.write(path, archive_name, compress_type=zipfile.ZIP_DEFLATED)
            with zipfile.ZipFile(partial) as archive:
                if archive.testzip() or hashlib.sha256(archive.read('nintendo.exe')).hexdigest() != digest:
                    raise RuntimeError('La vérification du jeu Nintendo a échoué.')
            os.rename(partial, destination)
        return destination
    finally:
        partial.unlink(missing_ok=True)


def build(platform, rom, name, output, icon=None, background=None, log=print, players=None):
    rom = Path(rom).resolve(strict=True)
    if players is None:
        players = 4 if platform == 'N64' else 2
    if platform == 'N64':
        if rom.suffix.lower() not in N64_EXTENSIONS:
            raise ValueError('Choisis une cartouche N64 au format .z64, .v64 ou .n64.')
        cartridge = read_n64_rom(rom)
        if players not in (1, 2, 3, 4):
            raise ValueError('Choisis entre 1 et 4 joueurs pour la N64.')
        if cartridge.sha1 == GOLDENEYE_PAL_SHA1:
            if players != 4:
                raise ValueError('GoldenEye Europe utilise un portage particulier : choisis 4 joueurs ici, puis le nombre de participants dans le menu du jeu.')
            return build_goldeneye_pal(rom, name, output, icon, background, log)
        return build_generic_n64(rom, cartridge, name, output, icon, background, log, players=players)
    extensions = {'NES': {'.nes'}, 'SNES': {'.sfc', '.smc'}}
    if platform not in extensions or rom.suffix.lower() not in extensions[platform]:
        raise ValueError('Choisis une ROM NES .nes ou Super Nintendo .sfc/.smc correspondant à la console.')
    if players < 1 or players > (5 if platform == 'SNES' else 2):
        raise ValueError('Nombre de joueurs indisponible pour cette console.')
    configure_engine()
    from retrorecomp.conversion_common import convert_single
    with tempfile.TemporaryDirectory(prefix='phantom-nintendo-resultat-') as folder:
        log('Recompilation ' + platform + ' et vérification de la cartouche…')
        runtime = convert_single(rom, Path(folder), title=name, cover=None,
            identify_arguments=('nes' if platform == 'NES' else 'snes',),
            passes=1, frames=600, language='fr', online_cover=False, use_cover=False,
            scan_level='quick', emit=log)
        log('Création et vérification du fichier unique…')
        marker = Path(folder) / 'phantom-players.txt'
        marker.write_text(str(players) + '\n', encoding='utf-8')
        return package_game(platform, rom, name, runtime, output, icon, background, log,
                            extra_files=[(marker, 'phantom-players.txt')])


def build_generic_n64(rom, cartridge, name, output, icon=None, background=None, log=print, package_id=None, players=4):
    """Generate a hybrid native/interpreted N64 executable; compatibility is experimental."""
    with safe_build_path(BASE) as build_root, safe_build_path(Path(tempfile.gettempdir())) as temp_root:
        return _build_generic_n64(rom, cartridge, name, output, icon, background, log,
                                  package_id, players, build_root, temp_root)


def _fix_ninja_cmake_path(build_dir, toolbin):
    """Keep CMake's own Ninja commands on the temporary shell-safe path."""
    safe_cmake = toolbin / 'cmake.exe'
    actual_cmake = safe_cmake.resolve(strict=True)
    if actual_cmake == safe_cmake:
        return
    substitutions = 0
    for ninja in (build_dir / 'build.ninja', build_dir / 'CMakeFiles' / 'rules.ninja'):
        text = ninja.read_text(encoding='utf-8')
        updated = text
        for original in {str(actual_cmake), actual_cmake.as_posix()}:
            updated, count = re.subn(re.escape(original), lambda match: str(safe_cmake),
                                     updated, flags=re.IGNORECASE)
            substitutions += count
        if updated != text:
            ninja.write_text(updated, encoding='utf-8', newline='\n')
    if substitutions == 0:
        raise RuntimeError('Impossible de préparer le chemin CMake temporaire pour la compilation N64.')


def _build_generic_n64(rom, cartridge, name, output, icon, background, log, package_id, players, build_root, temp_root):
    if players not in (1, 2, 3, 4):
        raise ValueError('Choisis entre 1 et 4 joueurs pour la N64.')
    toolbin = build_root / 'toolchain' / 'bin'
    n64_source = build_root / 'n64-recomp-lab' / 'moteur-n64-commun'
    n64_common = build_root / 'n64-engine' / 'generique'
    compiler = n64_common / 'n64recomp.exe'
    sdl = n64_common / 'SDL2'
    runtime_sources = n64_source / 'runtimes' / 'runtime-n64'
    required = [compiler, sdl / 'bin/SDL2.dll', sdl / 'LICENSE.txt', sdl / 'lib/cmake/SDL2/sdl2-config.cmake',
                runtime_sources / 'src/n64_recomp.c', n64_source / 'LICENSE',
                n64_source / 'runtimes/runtime-gb/vendor/imgui/LICENSE.txt']
    required += [toolbin / item for item in ('cmake.exe', 'ninja.exe', 'clang.exe', 'clang++.exe', 'llvm-windres.exe')]
    if not all(path.is_file() for path in required):
        raise RuntimeError('Le moteur N64 commun ou ses outils sont incomplets ; réinstalle PhantomStation.')
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    environment = os.environ.copy()
    environment['PATH'] = str(toolbin) + os.pathsep + environment.get('PATH', '')

    def run(command, label, cwd=None, timeout=1200):
        try:
            result = subprocess.run([str(arg) for arg in command], cwd=cwd, env=environment,
                                    capture_output=True, text=True, encoding='utf-8', errors='replace',
                                    creationflags=flags, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(label + ' : délai dépassé.') from exc
        if result.returncode:
            detail = (result.stdout + '\n' + result.stderr).strip()[-4000:]
            raise RuntimeError(label + ' :\n' + detail)
        return result

    def compile_project(command):
        lines = []
        errors = []
        process = subprocess.Popen([str(arg) for arg in command], env=environment, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace',
                                   creationflags=flags)
        expired = threading.Event()
        def stop():
            expired.set()
            process.kill()
        timer = threading.Timer(1200, stop)
        timer.daemon = True
        timer.start()
        try:
            for line in process.stdout:
                lines.append(line)
                if len(lines) > 200:
                    del lines[0]
                if 'error:' in line.lower() or line.startswith('FAILED:'):
                    errors.append(line)
                if re.match(r'^\s*\[\d+\s*/\s*\d+\]', line):
                    log(line.rstrip())
            code = process.wait()
        finally:
            timer.cancel()
            process.stdout.close()
        if expired.is_set():
            raise RuntimeError('Compilation N64 impossible : délai dépassé.')
        if code:
            raise RuntimeError('Compilation N64 impossible :\n' + ''.join(errors[-20:]) + '\n' + ''.join(lines)[-3000:])

    with tempfile.TemporaryDirectory(prefix='phantom-n64-', dir=str(temp_root)) as folder:
        work = Path(folder)
        normalized = work / 'cartouche.z64'
        normalized.write_bytes(cartridge.data)
        project = work / 'projet'
        log('Analyse automatique de ' + cartridge.title + ' (' + cartridge.country + ')…')
        log('Recompilation N64 hybride expérimentale : la compatibilité dépend du jeu.')
        run([compiler, normalized, '-o', project, '--name', 'jeu_n64', '--runtime', runtime_sources,
             '--profile', work / 'observation.trace', '--profile-frames', '600'], 'Analyse N64 impossible', timeout=600)
        log('Compilation du programme N64 et de son interface…')
        build_dir = work / 'build'
        run([toolbin / 'cmake.exe', '-S', project, '-B', build_dir, '-G', 'Ninja',
             '-DCMAKE_COMMAND=' + (toolbin / 'cmake.exe').as_posix(),
             '-DCMAKE_BUILD_TYPE=Release', '-DCMAKE_C_COMPILER=' + (toolbin / 'clang.exe').as_posix(),
             '-DCMAKE_CXX_COMPILER=' + (toolbin / 'clang++.exe').as_posix(),
             '-DCMAKE_RC_COMPILER=' + (toolbin / 'llvm-windres.exe').as_posix(),
             '-DCMAKE_MAKE_PROGRAM=' + (toolbin / 'ninja.exe').as_posix(),
             '-DSDL2_DIR=' + (sdl / 'lib/cmake/SDL2').as_posix(), '-DCMAKE_EXE_LINKER_FLAGS=-static'],
            'Configuration du programme N64 impossible')
        if 'n64_sdl.cpp' not in (build_dir / 'build.ninja').read_text(encoding='utf-8'):
            raise RuntimeError('L’interface vidéo N64 n’a pas pu être configurée.')
        _fix_ninja_cmake_path(build_dir, toolbin)
        compile_project([toolbin / 'cmake.exe', '--build', build_dir, '--parallel', '2'])
        executable = build_dir / 'jeu_n64.exe'
        if not executable.is_file():
            raise RuntimeError('La compilation N64 n’a pas produit de programme.')
        log('Vérification du démarrage du programme N64…')
        test = run([executable, '--frames', '60', '--save-dir', work / 'test-saves'],
                   'Le programme N64 ne démarre pas', cwd=build_dir, timeout=120)
        if 'embedded' not in (test.stdout + test.stderr).lower():
            raise RuntimeError('Impossible de vérifier que la cartouche est intégrée au programme.')
        marker = work / 'n64-generic.flag'
        marker.write_text('native-console-hybride-v1\n' + str(players) + '\n', encoding='utf-8')
        extra = [(marker, 'n64-generic.flag'), (sdl / 'bin/SDL2.dll', 'SDL2.dll'),
                 (n64_source / 'LICENSE', 'licences/native-console-LICENSE.txt'),
                 (n64_source / 'THIRD_PARTY.md', 'licences/native-console-THIRD-PARTY.md'),
                 (n64_source / 'runtimes/runtime-gb/vendor/imgui/LICENSE.txt', 'licences/imgui-LICENSE.txt'),
                 (sdl / 'LICENSE.txt', 'licences/SDL2-LICENSE.txt')]
        defaults = n64_default_codes(cartridge, work, log)
        if defaults is not None:
            extra.append((defaults, 'codes-n64-defaut.tsv'))
        log('Création et vérification du fichier unique…')
        return package_game('N64', rom, name, executable, output, icon, background, log, package_id=package_id, extra_files=extra)


def build_goldeneye_pal(rom, name, output, icon=None, background=None, log=print):
    if rom.suffix.lower() not in N64_EXTENSIONS:
        raise ValueError('Choisis une cartouche N64 au format .z64, .v64 ou .n64.')
    cartridge = read_n64_rom(rom)
    if cartridge.sha1 != GOLDENEYE_PAL_SHA1:
        raise ValueError('Jeu détecté : ' + cartridge.title + ' (' + cartridge.country + '). '
                         'Cette version N64 n’est pas encore prise en charge ; '
                         'seul GoldenEye 007 Europe est adapté actuellement.')
    runtime = N64_ENGINE / 'runtime' / 'ge007.pal.x86_64.exe'
    if not runtime.is_file():
        raise RuntimeError('Le moteur GoldenEye PAL est absent du dossier PhantomStation.')
    with tempfile.TemporaryDirectory(prefix='phantom-goldeneye-') as temporary:
        work = Path(temporary)
        for folder in ('assets', 'scripts', 'tools_pc'):
            shutil.copytree(N64_ENGINE / folder, work / folder)
        (work / 'data').mkdir()
        copy_rom = work / 'data' / 'ge007.pal-final.z64'
        copy_rom.write_bytes(cartridge.data)
        for script, args, message in (
            ('d43_emit.py', ['pal-final'], 'Préparation des modèles du jeu européen…'),
            ('d69_emit.py', ['pal-final'], 'Préparation des décors et niveaux…'),
            ('d88_emit.py', ['pal-final', '--regen'], 'Préparation des missions…'),
        ):
            log(message)
            result = subprocess.run([sys.executable, str(work / 'tools_pc' / script), *args], cwd=work,
                                    capture_output=True, text=True, encoding='utf-8', errors='replace',
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if result.returncode or 'ALL CHECKS PASSED' not in result.stdout:
                raise RuntimeError('Préparation des données N64 impossible :\n' + (result.stderr or result.stdout)[-2500:])
        extra = [(copy_rom, 'data/ge007.pal-final.z64')]
        for folder in ('pcmodels-pal-final', 'pccg-pal-final'):
            for file in (work / 'data' / folder).iterdir():
                if file.is_file():
                    extra.append((file, 'data/' + folder + '/' + file.name))
        for name_dll in ('SDL2.dll', 'libgcc_s_seh-1.dll', 'libwinpthread-1.dll'):
            extra.append((N64_ENGINE / 'runtime' / name_dll, name_dll))
        for license_file in (N64_ENGINE / 'licences').iterdir():
            if license_file.is_file():
                extra.append((license_file, 'licences/' + license_file.name))
        log('Création et vérification du fichier unique…')
        return package_game('N64', rom, name, runtime, output, icon, background, log, extra_files=extra)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--console', choices=['NES', 'SNES', 'N64'], required=True)
    parser.add_argument('--rom', required=True)
    parser.add_argument('--nom', required=True)
    parser.add_argument('--sortie', required=True)
    parser.add_argument('--icone')
    parser.add_argument('--fond')
    parser.add_argument('--joueurs', type=int, choices=(1, 2, 3, 4, 5))
    args = parser.parse_args()
    try:
        result = build(args.console, args.rom, args.nom, args.sortie, args.icone, args.fond,
                       log=lambda message: print(message, flush=True), players=args.joueurs)
        print('PHANTOM_NINTENDO_RESULT=' + str(result), flush=True)
    except Exception as exc:
        print('ERREUR : ' + str(exc), flush=True)
        sys.exit(1)
