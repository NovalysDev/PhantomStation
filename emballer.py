"""Assemble un jeu recompilé et ses images dans un exécutable Windows unique."""
import os
import io
import base64
import hashlib
import json
import re
import shutil
import subprocess
import tomllib
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
import zlib
from pathlib import Path
from PIL import Image, ImageOps
from sbi_support import validate_sbi

BASE = Path(__file__).resolve().parent
DESTINATION = BASE / 'Jeux en un fichier'


def _package_identity(project, requested=None):
    """Reuse the same game cache across rebuilds of the same project."""
    identity = project / 'phantom-package-id.txt'
    if requested is not None:
        value = uuid.UUID(hex=requested).hex
        if identity.exists() and identity.read_text(encoding='ascii').strip() != value:
            raise ValueError('Ce projet possède déjà une identité de sauvegarde différente.')
    elif identity.exists():
        try:
            return uuid.UUID(hex=identity.read_text(encoding='ascii').strip()).hex
        except ValueError as exc:
            raise ValueError('Identité de sauvegarde du projet invalide ; rien n’a été écrasé.') from exc
    else:
        value = uuid.uuid4().hex
    try:
        with identity.open('x', encoding='ascii') as f:
            f.write(value + '\n')
    except FileExistsError:
        existing = uuid.UUID(hex=identity.read_text(encoding='ascii').strip()).hex
        if existing != value:
            raise ValueError('L’identité du projet a changé pendant la création.')
    return value


def _decode_cover(data):
    """Read an image; recover PNGs with damaged nonessential metadata only."""
    def decode(raw):
        with Image.open(io.BytesIO(raw)) as image:
            return image.convert('RGBA')
    try:
        return decode(data)
    except (OSError, ValueError, SyntaxError):
        if not data.startswith(b'\x89PNG\r\n\x1a\n'):
            raise
        # PNG metadata such as colour profiles can be corrupt while the image
        # is intact. Retain critical chunks and transparency, checking CRCs.
        clean = bytearray(data[:8])
        offset = 8
        ended = False
        while offset + 12 <= len(data):
            size = int.from_bytes(data[offset:offset + 4], 'big')
            end = offset + 12 + size
            if end > len(data):
                raise ValueError('Image PNG incomplète.')
            kind = data[offset + 4:offset + 8]
            if len(kind) != 4:
                raise ValueError('Image PNG invalide.')
            if not (kind[0] & 0x20) or kind == b'tRNS':
                expected = int.from_bytes(data[end - 4:end], 'big')
                if zlib.crc32(data[offset + 4:end - 4]) != expected:
                    raise ValueError('Données principales de la jaquette endommagées.')
                clean.extend(data[offset:end])
            offset = end
            if kind == b'IEND':
                ended = True
                break
        if not ended:
            raise ValueError('Image PNG incomplète.')
        return decode(clean)


def _make_icon(project, game_name, cue, chosen, log):
    saved = project / 'phantom-icone.ico'
    source = None
    if chosen:
        try:
            source = _decode_cover(Path(chosen).read_bytes())
        except (OSError, ValueError, SyntaxError, Image.DecompressionBombError) as exc:
            raise ValueError('La jaquette choisie est illisible. Choisis une autre image.') from exc
        log('Utilisation de l’image choisie pour l’icône.\n')
    elif saved.is_file():
        try:
            with Image.open(saved) as image:
                image.load()
            return saved
        except (OSError, ValueError, SyntaxError):
            log('Icône enregistrée illisible : nouvelle recherche.\n')
    if source is None:
        names = [cue.stem, re.sub(r'\s*\(Disc\s+\d+\)', '', cue.stem, flags=re.I), game_name]
        for candidate in dict.fromkeys(names):
            if not candidate: continue
            url = 'https://raw.githubusercontent.com/libretro-thumbnails/Sony_-_PlayStation/master/Named_Boxarts/' + urllib.parse.quote(candidate + '.png')
            try:
                request = urllib.request.Request(url, headers={'User-Agent': 'PhantomStation/1.0'})
                with urllib.request.urlopen(request, timeout=5) as response:
                    image_data = response.read(8*1024*1024 + 1)
                if len(image_data) > 8*1024*1024: raise ValueError('Image trop volumineuse')
                source = _decode_cover(image_data)
                log(f'Jaquette trouvée pour {candidate}.\n')
                (project / 'phantom-jaquette-source.txt').write_text(url + '\n', encoding='utf-8')
                break
            except (urllib.error.URLError, OSError, ValueError, SyntaxError, Image.DecompressionBombError):
                source = None
        if source is None:
            log('Jaquette introuvable : utilisation de l’image PhantomStation.\n')
            source = _decode_cover((BASE / 'assets' / 'phantom-startup.png').read_bytes())
    source.save(project / 'phantom-jaquette.png', format='PNG')
    source.thumbnail((256, 256), Image.Resampling.LANCZOS)
    square = Image.new('RGBA', (256,256), (4, 10, 24, 255))
    square.alpha_composite(source, ((256-source.width)//2, (256-source.height)//2))
    square.save(saved, format='ICO', sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])
    return saved


def _make_launch_art(project, runtime, background_path, log):
    cover = project / 'phantom-jaquette.png'
    if not cover.is_file():
        cover = project / 'phantom-icone.ico'
    if not cover.is_file(): cover = BASE / 'assets' / 'phantom-startup.png'
    target = runtime / 'assets' / 'img'
    target.mkdir(parents=True, exist_ok=True)
    with Image.open(cover) as source:
        source.convert('RGBA').save(target / 'boxart.tga')
    stored_background = project / 'phantom-fond.png'
    if background_path:
        with Image.open(background_path) as chosen:
            chosen.convert('RGB').save(stored_background, format='PNG')
    background = stored_background if stored_background.is_file() else BASE / 'assets' / 'phantom-startup.png'
    with Image.open(background) as source:
        image = ImageOps.fit(source.convert('RGB'), (1600,900), Image.Resampling.LANCZOS,
                             centering=(0.5, 0.35))
        shade = Image.new('RGB', image.size, (3, 8, 20))
        image = Image.blend(image, shade, .40).convert('RGBA')
        image.save(target / 'phantom-background.tga')
    log('Jaquette et fond du jeu préparés.\n')


def _use_game_resolution_default(runtime):
    """Show the compiler's quality choice as the mod's fresh-profile default."""
    manifest = runtime / 'mods/bundled/psx.enhancement.internal-resolution/1.0.0/manifest.toml'
    if not manifest.is_file():
        raise ValueError('Le réglage de résolution du jeu est introuvable.')
    content = manifest.read_text(encoding='utf-8')
    data = tomllib.loads(content)
    option = next((o for o in data.get('option', []) if o.get('id') == 'resolution'), None)
    if not option:
        raise ValueError('Option de résolution introuvable dans le jeu.')
    choices = {choice.get('value') for choice in option.get('choice', [])}
    config = tomllib.loads((runtime / 'game.toml').read_text(encoding='utf-8-sig'))
    chosen = str(config.get('video', {}).get('internal_resolution', 'default')).lower()
    if chosen not in choices:
        chosen = 'default'
    pattern = r'(?ms)(\[\[option\]\]\s*\n(?:(?!\[\[).)*?^id\s*=\s*"resolution"(?:(?!\[\[).)*?^default\s*=\s*)"[^"]+"'
    content, count = re.subn(pattern, lambda match: match.group(1) + '"' + chosen + '"', content, count=1)
    if count != 1:
        raise ValueError('Impossible de régler la résolution par défaut du jeu.')
    manifest.write_text(content, encoding='utf-8', newline='\n')


def _prepare_cheat_catalog(runtime, game, log):
    """Package matching FR serials only; keep user codes in a separate file."""
    catalog = BASE / 'assets' / 'codes-fr.json'
    if not catalog.is_file():
        raise ValueError('Catalogue local des codes français introuvable.')
    data = json.loads(catalog.read_text(encoding='utf-8'))
    serials = [game.get('id', '')] + list(game.get('disc_serials') or [])
    serials = [re.sub(r'^([A-Z]{4})[-_. ]?(\d{3})[._ -]?(\d{2})$', r'\1-\2\3', str(s).upper()) for s in serials]
    paths = list(dict.fromkeys(name for serial in serials for name in data.get('index', {}).get(serial, [])))
    entries, seen = [], set()
    labels = {'Invincibility (No Hit)': 'Invincibilité (aucun coup reçu)',
              'Infinite Health': 'Vie infinie', 'Infinite HP': 'PV infinis',
              'Infinite MP': 'PM infinis', 'Infinite Ammo': 'Munitions infinies',
              'Infinite Money': 'Argent infini'}
    for filename in paths:
        text = data.get('files', {}).get(filename, '')
        current = None
        def finish():
            if not current or not current['lines'] or current['type'].lower() != 'gameshark' or current['activation'].lower() != 'endframe' or current['options']:
                return
            name = labels.get(current['name'], current['name'])
            name = re.sub(r'[\t\r\n|]', ' ', name).strip()[:110]
            lines = [line.strip().upper() for line in current['lines']]
            key = (name, tuple(lines))
            if name and key not in seen and len(entries) < 200:
                seen.add(key)
                entries.append('0\t' + name + '\t' + '|'.join(lines))
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith((';', '#')): continue
            heading = re.fullmatch(r'\[(.+)\]', line)
            if heading:
                finish()
                current = {'name': heading.group(1), 'type': 'GameShark', 'activation': 'EndFrame', 'options': False, 'lines': []}
            elif current:
                meta = re.match(r'^(\w+)\s*=\s*(.*)$', line)
                if meta:
                    key, val = meta.group(1).lower(), meta.group(2).strip()
                    if key in ('type', 'activation'): current[key] = val
                    elif key in ('option', 'optionrange') or key == 'ignore' and val.lower() == 'true': current['options'] = True
                else:
                    current['lines'].append(line)
        finish()
    extra = BASE / 'assets' / 'codes-libretro-fr.json'
    if extra.is_file():
        for collection in json.loads(extra.read_text(encoding='utf-8')):
            if not set(serials).intersection(collection.get('serials', [])):
                continue
            for item in collection.get('entries', []):
                pieces = item.get('code', '').split('+')
                if len(pieces) % 2 == 0 and all(re.fullmatch(r'[0-9A-Fa-f]{8}', pieces[i]) and re.fullmatch(r'[0-9A-Fa-f]{4}|[0-9A-Fa-f]{8}', pieces[i+1]) for i in range(0, len(pieces), 2)):
                    lines = [pieces[i].upper() + ' ' + pieces[i+1].upper() for i in range(0, len(pieces), 2)]
                else:
                    lines = [item.get('code', '').upper()]
                name = re.sub(r'[\t\r\n|]', ' ', item.get('desc', 'Code')).strip()[:90] + ' (Libretro)'
                key = (name, tuple(lines))
                if key not in seen and len(entries) < 200:
                    seen.add(key)
                    entries.append('0\t' + name + '\t' + '|'.join(lines))
    target = runtime / 'assets' / 'phantom-codes-default.tsv'
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('# Codes CHTDB pour ' + ', '.join(serials) + '\n' + '\n'.join(entries) + '\n', encoding='utf-8')
    log(f'{len(entries)} code(s) proposé(s) pour la référence du jeu ; compatibilité à vérifier.\n')


def _build_stub(icon, output):
    compiler = Path(os.environ.get('WINDIR', r'C:\Windows')) / 'Microsoft.NET' / 'Framework64' / 'v4.0.30319' / 'csc.exe'
    if not compiler.is_file(): raise ValueError('Compilateur Windows .NET introuvable pour créer l’icône.')
    cmd = [str(compiler), '/nologo', '/target:winexe', '/out:' + str(output),
           '/reference:System.Windows.Forms.dll', '/reference:System.Drawing.dll',
           '/reference:System.IO.Compression.dll', '/reference:System.IO.Compression.FileSystem.dll',
           '/win32icon:' + str(icon), '/resource:' + str(BASE/'assets'/'phantom-startup.png') + ',art',
           '/resource:' + str(BASE/'assets'/'playstation-logo.png') + ',logo',
           '/resource:' + str(BASE/'assets'/'son-demarrage.wav') + ',sound', str(BASE/'unique_launcher.cs')]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors='replace', creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode: raise ValueError('Impossible de créer le programme avec son icône : ' + result.stdout.strip())


def _cue_files(cue):
    raw = cue.read_text(encoding='utf-8-sig', errors='replace')
    refs = []
    pattern = re.compile(r'^(\s*FILE\s+)(?:"([^"]+)"|(\S+))(\s+\S+.*)$', re.I | re.M)
    def replace(match):
        name = match.group(2) or match.group(3)
        source = Path(name.replace('\\', '/'))
        if not source.is_absolute(): source = cue.parent / source
        source = source.resolve()
        if not source.is_file(): raise ValueError(f'Fichier de piste introuvable : {source}')
        for original, alias in refs:
            if original == source: return f'{match.group(1)}"{alias}"{match.group(4)}'
        alias = f'piste-{len(refs)+1:02d}{source.suffix.lower() or ".bin"}'
        refs.append((source, alias))
        return f'{match.group(1)}"{alias}"{match.group(4)}'
    rewritten = pattern.sub(replace, raw)
    if not refs: raise ValueError(f'Aucune piste trouvée dans {cue.name}.')
    return rewritten, refs


def _cleanup_build_artifacts(project, runtime_executable, protected, log, progress=None, names=None):
    """Remove known build outputs, never game images, saves or user settings."""
    project = project.resolve()
    protected = [Path(p).resolve() for p in protected]
    if names is None:
        names = ('generated', 'build-recompiler', '.cache', 'build-release/CMakeFiles',
                 'build-release/_deps', 'build-release/cache', 'build-release/native-cpu-spans',
                 'build-release/native-idle-wait', 'build-release/psx_execution',
                 'build-release/overlay_toolchain', 'build-release/' + runtime_executable,
                 'build-release/Jouer avec PhantomStation.cmd')
    sensitive_ext = {'.cue', '.bin', '.iso', '.img', '.chd', '.mcd', '.mcr', '.srm', '.sav', '.state'}
    removed, skipped = 0, []
    for index, name in enumerate(names, 1):
        path = project / name
        if progress: progress(index - 1, len(names), 'Nettoyage des fichiers temporaires')
        try:
            if not path.exists(): continue
            resolved = path.resolve()
            if not resolved.is_relative_to(project) or any(p == resolved or p.is_relative_to(resolved) for p in protected):
                skipped.append(name); continue
            if path.is_symlink() or getattr(path.lstat(), 'st_file_attributes', 0) & 0x400:
                skipped.append(name); continue
            if path.name == 'Jouer avec PhantomStation.cmd' and 'REM PhantomStation game launcher' not in path.read_text(encoding='utf-8', errors='replace'):
                skipped.append(name); continue
            if path.is_dir():
                safe = True
                for base, dirs, files in os.walk(path, followlinks=False):
                    for child in dirs + files:
                        item = Path(base) / child
                        # CMake's compiler probe executables use .bin too; they
                        # are build artifacts, not PlayStation disc images.
                        cmake_probe = (item.name in ('CMakeDetermineCompilerABI_C.bin', 'CMakeDetermineCompilerABI_CXX.bin')
                                       and item.parent.parent.name == 'CMakeFiles'
                                       and re.fullmatch(r'\d+(?:\.\d+)+(?:[-.][A-Za-z0-9.]+)?', item.parent.name))
                        if (item.is_symlink() or getattr(item.lstat(), 'st_file_attributes', 0) & 0x400 or
                            item.is_file() and ((item.suffix.lower() in sensitive_ext and not cmake_probe)
                                                or item.name.lower() in ('settings.toml', 'state.toml', 'phantom-codes.tsv'))):
                            safe = False
                            break
                    if not safe: break
                if not safe:
                    skipped.append(name); continue
                shutil.rmtree(path)
            elif path.is_file():
                path.unlink()
            removed += 1
        except OSError as exc:
            skipped.append(name)
            log(f'Nettoyage impossible pour {name} : {exc}\n')
    if progress: progress(len(names), len(names), 'Nettoyage des fichiers temporaires')
    log(f'Nettoyage : {removed} élément(s) de compilation supprimé(s).\n')
    if skipped:
        log('Éléments conservés pour protéger des données ou parce qu’ils sont inaccessibles : ' + ', '.join(skipped) + '\n')


def package_game(project, log=lambda msg: None, icon_path=None, package_id=None, background_path=None, progress=None, destination_dir=None, sbi_payloads=None, on_partial=None, attempt_token=None):
    project = Path(project).resolve()
    package_id = _package_identity(project, package_id)
    runtime = project / 'build-release'
    config_path = runtime / 'game.toml'
    if not config_path.is_file() or not (BASE / 'unique_launcher.cs').is_file():
        raise ValueError('Jeu compilé ou composant de création manquant.')
    config = config_path.read_text(encoding='utf-8-sig')
    data = tomllib.loads(config)
    game = data.get('game', {})
    discs = game.get('discs') or [game.get('disc')]
    if not discs or any(not isinstance(p, str) or not p for p in discs):
        raise ValueError('Aucun disque trouvé dans le projet.')
    if sbi_payloads is not None and len(sbi_payloads) != len(discs):
        raise ValueError('Le nombre de fichiers SBI ne correspond pas aux disques.')
    descriptor = []
    for number, original in enumerate(discs, 1):
        cue = Path(original)
        if not cue.is_absolute(): cue = (runtime / cue).resolve()
        if cue.suffix.lower() != '.cue' or not cue.is_file():
            raise ValueError(f'Le fichier CUE du disque {number} est introuvable : {cue}')
        text, tracks = _cue_files(cue)
        # PSXRecomp indexes multi-disc identities by the CUE's filename stem.
        # Each disc therefore needs a distinct name, even in separate folders.
        descriptor.append((f'media/cd{number:02d}/disque-{number:02d}.cue', text, tracks))
    ninja = (runtime / 'build.ninja').read_text(encoding='utf-8', errors='replace')
    match = re.search(r'^build psx-runtime: phony ([^\r\n]+\.exe)\s*$', ninja, re.M)
    if not match: raise ValueError('Impossible de retrouver le programme du jeu.')
    exe = match.group(1).replace('$ ', ' ')
    if Path(exe).name != exe or not (runtime / exe).is_file(): raise ValueError('Programme du jeu introuvable.')
    game_match = re.search(r'(?ms)^\[game\]\s*\n(.*?)(?=^\[|\Z)', config)
    if not game_match: raise ValueError('Configuration du jeu invalide.')
    body = game_match.group(1)
    disc_lines = 'discs = [\n' + ''.join(f'    "{d[0]}",\n' for d in descriptor) + ']'
    body, count = re.subn(r'(?ms)^discs\s*=\s*\[.*?\]|^disc\s*=\s*[^\r\n]+', lambda _: disc_lines, body, count=1)
    if count != 1: raise ValueError('Impossible de préparer les chemins des disques.')
    config = config[:game_match.start(1)] + body + config[game_match.end(1):]
    first_cue = Path(discs[0])
    if not first_cue.is_absolute(): first_cue = (runtime / first_cue).resolve()
    icon = _make_icon(project, str(game.get('name') or ''), first_cue, icon_path, log)
    _make_launch_art(project, runtime, background_path, log)
    _use_game_resolution_default(runtime)
    _prepare_cheat_catalog(runtime, game, log)
    output_dir = Path(destination_dir).expanduser().resolve() if destination_dir is not None else DESTINATION
    output_dir.mkdir(parents=True, exist_ok=True)
    name = re.sub(r'[^\w. -]+', '-', str(game.get('name') or project.name), flags=re.UNICODE).strip(' .') or 'Jeu PS1'
    destination = output_dir / f'{name}.exe'
    index = 2
    while destination.exists() or destination.with_suffix('.exe.part').exists():
        destination = output_dir / f'{name} ({index}).exe'; index += 1
    partial = destination.with_suffix('.exe.part') if not attempt_token else output_dir / (destination.name + '.' + attempt_token + '.part')
    if attempt_token:
        with partial.open('xb'):
            pass
        if on_partial: on_partial(partial)
    log('Création du fichier unique ; les images originales restent en place.\n')
    _build_stub(icon, partial)
    intro_stub = partial.read_bytes()
    try:
        files = [(runtime / exe, exe)]
        for path in ('game_options.toml', exe.removesuffix('.exe') + '.execution.json'):
            if (runtime / path).is_file(): files.append((runtime / path, path))
        for folder in ('assets', 'bios', 'mods', 'overlay_toolchain'):
            base = runtime / folder
            if base.is_dir():
                files.extend((file, file.relative_to(runtime).as_posix()) for file in base.rglob('*') if file.is_file())
        controller_db = BASE / 'assets' / 'gamecontrollerdb.txt'
        if controller_db.is_file() and not any(name == 'assets/gamecontrollerdb.txt' for _, name in files):
            files.append((controller_db, 'assets/gamecontrollerdb.txt'))
        boot = game.get('exe')
        if boot:
            boot_file = project / boot
            if boot_file.is_file(): files.append((boot_file, Path(boot).as_posix()))
        for index, (cue_path, text, tracks) in enumerate(descriptor, 1):
            for source, alias in tracks:
                files.append((source, f'media/cd{index:02d}/{alias}'))
        for name in ('LICENSE', 'THIRD_PARTY_ATTRIBUTION.md'):
            p = project / 'psxrecomp' / name
            if p.is_file(): files.append((p, f'licences/{name}'))
        for source, name in ((BASE / 'bios-personnalise' / 'LICENSE-OpenBIOS.txt', 'LICENSE-OpenBIOS.txt'),
                             (BASE / 'assets' / 'LICENSE-CHTDB.txt', 'LICENSE-CHTDB.txt'),
                             (BASE / 'assets' / 'LICENSE-LIBRETRO.txt', 'LICENSE-LIBRETRO.txt'),
                             (BASE / 'assets' / 'LICENSE-SDL-GameControllerDB.txt', 'LICENSE-SDL-GameControllerDB.txt')):
            if source.is_file(): files.append((source, 'licences/' + name))
        with (runtime / exe).open('rb') as game_file:
            game_hash = hashlib.file_digest(game_file, 'sha256').hexdigest()
        title = str(game.get('name') or project.name)
        title_encoded = base64.b64encode(title.encode('utf-8')).decode('ascii')
        main_source = project / 'psxrecomp/runtime/src/main.cpp'
        has_documents_saves = main_source.is_file() and 'PHANTOM_DOCUMENT_SAVES_V1' in main_source.read_text(encoding='utf-8')
        flags = 'documents-saves-v1' if has_documents_saves else ''
        from detection_doublons import SOURCE_ENTRY, encoded_source
        inline = [('phantom-package.txt', (package_id + '\n' + exe + '\n' + game_hash + '\n' + title_encoded + '\n' + flags + '\n').encode('utf-8')),
                  (SOURCE_ENTRY, encoded_source('PS1', [[source for source, _ in tracks] for _, _, tracks in descriptor])),
                  ('phantom-intro.exe', intro_stub), ('game.toml', config.encode('utf-8'))]
        inline.extend((cue_path, text.encode('utf-8')) for cue_path, text, tracks in descriptor)
        if sbi_payloads is not None:
            for index, data in enumerate(sbi_payloads, 1):
                if data is not None:
                    inline.append((f'media/cd{index:02d}/disque-{index:02d}.sbi', validate_sbi(data)))
        total = sum(path.stat().st_size for path, name in files) + sum(len(data) for name, data in inline)
        done = 0
        last_percent = -1
        def report(amount, phase):
            nonlocal done, last_percent
            done += amount
            percent = int(100 * done / total) if total else 100
            if progress and (percent != last_percent or done == total):
                progress(done, total, phase)
                last_percent = percent

        if progress: progress(0, total, 'Création du fichier unique')
        with zipfile.ZipFile(partial, 'a', compression=zipfile.ZIP_DEFLATED, compresslevel=3, allowZip64=True) as archive:
            for name, data in inline:
                archive.writestr(name, data)
                report(len(data), 'Création du fichier unique')
            for source, name in files:
                if name.startswith('media/'): log(f'Ajout du disque : {source.name}\n')
                info = zipfile.ZipInfo.from_file(source, name)
                info.compress_type = archive.compression
                info._compresslevel = archive.compresslevel
                with source.open('rb') as reader, archive.open(info, 'w', force_zip64=info.file_size >= zipfile.ZIP64_LIMIT) as writer:
                    while True:
                        block = reader.read(1024 * 1024)
                        if not block: break
                        writer.write(block)
                        report(len(block), 'Création du fichier unique')
        with zipfile.ZipFile(partial) as check:
            if check.getinfo(exe).file_size != (runtime / exe).stat().st_size:
                raise ValueError('Vérification du programme impossible.')
            checked = 0
            last_percent = -1
            if progress: progress(0, total, 'Vérification du fichier unique')
            for info in check.infolist():
                digest = hashlib.sha256() if info.filename == exe else None
                with check.open(info) as reader:
                    while True:
                        block = reader.read(1024 * 1024)
                        if not block: break
                        if digest is not None: digest.update(block)
                        checked += len(block)
                        percent = int(100 * checked / total) if total else 100
                        if progress and (percent != last_percent or checked == total):
                            progress(checked, total, 'Vérification du fichier unique')
                            last_percent = percent
                if digest is not None and digest.hexdigest() != game_hash:
                    raise ValueError('Le programme a changé pendant la création du fichier unique.')
            if checked != total:
                raise ValueError('Vérification incomplète du fichier unique.')
        os.rename(partial, destination)
    except Exception:
        # The caller removes its uniquely marked incomplete archive after an error.
        raise
    log(f'Fichier unique créé et vérifié : {destination}\n')
    protected = [Path(path) if Path(path).is_absolute() else runtime / path for path in discs]
    protected.extend(source for _, _, tracks in descriptor for source, _ in tracks)
    protected.append(destination)
    _cleanup_build_artifacts(project, exe, protected, log, progress)
    return destination
