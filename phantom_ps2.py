"""Construction et emballage expérimental d'un jeu PS2 dans PhantomStation."""
import base64, hashlib, io, json, os, re, shutil, struct, subprocess, tempfile, threading, time, tomllib, urllib.parse, urllib.request, uuid, zipfile
from pathlib import Path
from PIL import Image
from emballer import _decode_cover

BASE = Path(__file__).resolve().parent
ENGINE = BASE / 'ps2-native-recomp'
PYTHON = Path(os.environ.get('WINDIR', r'C:\Windows')) / 'Microsoft.NET' / 'Framework64' / 'v4.0.30319' / 'csc.exe'
WINDOWS_EXE_LIMIT = 1 << 32  # CreateProcess rejects files of 4 GiB or larger.


def _extract_windows_icon(source, destination):
    """Preserve every original icon resolution from a Windows launcher."""
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.LoadLibraryExW.argtypes = [wintypes.LPCWSTR, wintypes.HANDLE, wintypes.DWORD]
    kernel.LoadLibraryExW.restype = wintypes.HMODULE
    kernel.FreeLibrary.argtypes = [wintypes.HMODULE]
    kernel.FindResourceW.argtypes = [wintypes.HMODULE, ctypes.c_void_p, ctypes.c_void_p]
    kernel.FindResourceW.restype = wintypes.HANDLE
    kernel.SizeofResource.argtypes = [wintypes.HMODULE, wintypes.HANDLE]
    kernel.SizeofResource.restype = wintypes.DWORD
    kernel.LoadResource.argtypes = [wintypes.HMODULE, wintypes.HANDLE]
    kernel.LoadResource.restype = wintypes.HANDLE
    kernel.LockResource.argtypes = [wintypes.HANDLE]
    kernel.LockResource.restype = ctypes.c_void_p
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMODULE, ctypes.c_void_p,
                                      ctypes.c_void_p, ctypes.c_ssize_t)
    kernel.EnumResourceNamesW.argtypes = [wintypes.HMODULE, ctypes.c_void_p, callback_type, ctypes.c_ssize_t]
    kernel.EnumResourceNamesW.restype = wintypes.BOOL
    module = kernel.LoadLibraryExW(str(source), None, 0x00000002 | 0x00000020)
    if not module:
        raise OSError(ctypes.get_last_error(), 'Impossible de lire l’icône du jeu.')
    try:
        names = []
        @callback_type
        def collect(_module, _type, name, _param):
            value = int(name or 0)
            names.append(value if value <= 0xffff else ctypes.wstring_at(value))
            return False
        kernel.EnumResourceNamesW(module, ctypes.c_void_p(14), collect, 0)
        if not names:
            raise ValueError('Icône du jeu absente.')

        def resource(kind, name):
            name_buffer = ctypes.create_unicode_buffer(name) if isinstance(name, str) else None
            name_ptr = ctypes.cast(name_buffer, ctypes.c_void_p) if name_buffer is not None else ctypes.c_void_p(name)
            found = kernel.FindResourceW(module, name_ptr, ctypes.c_void_p(kind))
            size = kernel.SizeofResource(module, found) if found else 0
            loaded = kernel.LoadResource(module, found) if size else None
            address = kernel.LockResource(loaded) if loaded else None
            if not address or size > 8 * 1024 * 1024:
                raise ValueError('Icône du jeu invalide.')
            return ctypes.string_at(address, size)

        group = resource(14, names[0])
        if len(group) < 6:
            raise ValueError('Icône du jeu incomplète.')
        reserved, kind, count = struct.unpack_from('<HHH', group)
        if reserved or kind != 1 or not 0 < count <= 64 or len(group) < 6 + 14 * count:
            raise ValueError('Icône du jeu invalide.')
        entries, images, offset = [], [], 6 + 16 * count
        for index in range(count):
            width, height, colors, zero, planes, bits, size, ident = struct.unpack_from('<BBBBHHIH', group, 6 + 14 * index)
            image = resource(3, ident)
            if len(image) != size:
                raise ValueError('Image de l’icône incomplète.')
            entries.append(struct.pack('<BBBBHHII', width, height, colors, zero, planes, bits, size, offset))
            images.append(image)
            offset += size
        Path(destination).write_bytes(struct.pack('<HHH', 0, 1, count) + b''.join(entries) + b''.join(images))
    finally:
        kernel.FreeLibrary(module)


def windows_environment():
    portable = ENGINE / 'tools' / 'portable'
    folders = ['llvm-mingw/bin', 'cmake/bin', 'ninja', 'python', 'mingit/cmd', 'mingit/usr/bin', 'busybox']
    allowed = ('SystemRoot', 'SystemDrive', 'windir', 'ComSpec', 'PATHEXT', 'TEMP', 'TMP', 'USERNAME', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'ProgramData', 'ProgramFiles', 'ProgramFiles(x86)', 'ProgramW6432', 'CommonProgramFiles', 'CommonProgramFiles(x86)', 'CommonProgramW6432', 'ALLUSERSPROFILE', 'PUBLIC', 'HOMEDRIVE', 'HOMEPATH', 'OS', 'NUMBER_OF_PROCESSORS', 'PROCESSOR_ARCHITECTURE', 'PROCESSOR_IDENTIFIER', 'PROCESSOR_LEVEL', 'PROCESSOR_REVISION', 'COMPUTERNAME', 'DriverData')
    env = {key: os.environ[key] for key in allowed if key in os.environ}
    env['PATH'] = os.pathsep.join([str(portable / p) for p in folders] + [str(Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32')])
    env['PS2_JOBS'] = '4'
    env['PS2_AVX2'] = '0'
    return env


def learn_cache_root():
    """What exploring and playing found per disc, kept between compilations (scripts/learn_cache.py in the engine)."""
    local = os.environ.get('LOCALAPPDATA') or str(Path.home() / 'AppData' / 'Local')
    return Path(local) / 'PhantomStation' / 'PS2-apprentissage'


def bash_path(path):
    path = Path(path).resolve()
    if not re.fullmatch(r'[a-zA-Z]:', path.drive): raise ValueError('Le fichier PS2 doit se trouver sur un lecteur local.')
    return '/' + path.drive[0].lower() + '/' + '/'.join(path.parts[1:])


def run_command(command, env, log, progress=None, ninja_log=None):
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    stop = threading.Event()
    def observe():
        pos = 0
        while not stop.wait(.5):
            try:
                if ninja_log and ninja_log.is_file():
                    with ninja_log.open('r', encoding='utf-8', errors='replace') as stream:
                        stream.seek(pos)
                        for line in stream.readlines():
                            m = re.match(r'^\[(\d+)/(\d+)\]', line)
                            if m and progress: progress(int(m[1]), int(m[2]), 'Compilation Windows')
                        pos = stream.tell()
            except (OSError, ValueError): pass
    monitor = threading.Thread(target=observe, daemon=True); monitor.start()
    try:
        proc = subprocess.Popen(command, cwd=ENGINE, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding='utf-8', errors='replace', creationflags=flags)
        for line in proc.stdout: log(line)
        if proc.wait(): raise RuntimeError('La compilation PS2 a échoué ; consulte le journal.')
    finally:
        stop.set(); monitor.join(timeout=2)


def build_ps2(name, slug, iso, log, progress=None):
    iso = Path(iso).resolve(strict=True)
    try:
        import erreurs_connues as apprentissage
        for ligne in apprentissage.preparer('PS2', iso, name):
            log(ligne + '\n')
    except Exception:
        pass
    if iso.suffix.lower() != '.iso' or not iso.is_file(): raise ValueError('Choisis une image ISO PS2 valide.')
    bash = ENGINE / 'tools/portable/mingit/usr/bin/bash.exe'
    portable_setup = ENGINE / 'scripts/portable.ps1'
    if not portable_setup.is_file(): raise RuntimeError('Le moteur de compilation PS2 est absent.')
    probes = [bash, ENGINE/'tools/portable/llvm-mingw/bin/clang++.exe', ENGINE/'tools/portable/cmake/bin/cmake.exe',
              ENGINE/'tools/portable/ninja/ninja.exe', ENGINE/'tools/portable/python/python.exe',
              ENGINE/'tools/portable/busybox/busybox.exe']
    if not all(path.is_file() for path in probes):
        log('Téléchargement des outils PS2 dans le dossier du compilateur (première utilisation)…\n')
        run_command(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(portable_setup), '-ForApp'], os.environ.copy(), log)
    if not all(path.is_file() for path in probes): raise RuntimeError('Les outils PS2 n’ont pas pu être installés.')
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,60}', slug): raise ValueError('Nom de projet PS2 invalide.')
    env = windows_environment()
    # A disc compiled again starts from what was found the last times, and from what playing it met.
    env['PS2_LEARN_CACHE'] = learn_cache_root().as_posix()
    if not (ENGINE / 'build/tools/eerecomp.exe').is_file():
        log('Préparation du moteur et des bibliothèques PS2…\n')
        run_command([str(bash), 'scripts/setup.sh', '--native-only'], env, log)
    game = ENGINE / 'games' / slug
    if game.exists() or (ENGINE / 'build' / ('native-' + slug)).exists():
        raise ValueError('Ce projet PS2 existe déjà ; choisis un autre nom.')
    game.mkdir(parents=True, exist_ok=False)
    token = uuid.uuid4().hex
    (game / '.phantom-temporaire').write_text(token, encoding='ascii')
    log('Recompilation PS2 de ' + name + '…\n')
    run_command([str(bash), 'scripts/port.sh', slug, bash_path(iso), '--native-only'], env, log, progress,
                ENGINE / 'build' / ('native-' + slug) / 'ninja.log')
    runtime = ENGINE / 'build' / ('native-' + slug)
    exe = runtime / 'ps2run.exe'
    cnf = game / 'disc/SYSTEM.CNF'
    if not exe.is_file() or not cnf.is_file(): raise RuntimeError('Le programme PS2 compilé est introuvable.')
    content = cnf.read_text(encoding='ascii', errors='replace')
    match = re.search(r'(?im)^\s*BOOT2\s*=\s*[^\r\n]*\\([^\\/;]+)', content)
    if not match: raise RuntimeError('Le programme de démarrage PS2 est introuvable.')
    elf = game / 'disc' / match.group(1)
    if not elf.is_file(): raise RuntimeError('Le programme PS2 extrait est introuvable.')
    return runtime, elf, match.group(1), token


def cleanup_ps2_build(slug, token, image, log):
    """Supprime uniquement les répertoires de travail créés par cette compilation."""
    game = ENGINE / 'games' / slug
    marker = game / '.phantom-temporaire'
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,60}', slug) or not re.fullmatch(r'[0-9a-f]{32}', token): return
    try:
        if not marker.is_file() or marker.read_text(encoding='ascii') != token: return
        alias = game / 'disc.iso'
        if alias.is_file() and not os.path.samefile(alias, image):
            log('Image de travail conservée : elle ne correspond pas à la source.\n'); return
        allowed_top = {'disc', 'vu1', 'shots', 'overlays', 'modules', 'entries.txt', 'explore.log',
                       'explore_ram.bin', 'explore.ps2', 'disc.iso', 'misses.txt', 'core.elf', '.phantom-temporaire'}
        if any(path.name not in allowed_top for path in game.iterdir()):
            log('Projet PS2 conservé : il contient des fichiers supplémentaires.\n'); return
        root = ENGINE.resolve()
        targets = [game, ENGINE / 'generated' / slug, ENGINE / 'build' / ('native-' + slug)]
        for target in targets:
            if not target.exists(): continue
            if not target.resolve().is_relative_to(root) or target.is_symlink(): raise ValueError('Chemin de nettoyage non sûr.')
            for item in target.rglob('*'):
                if item.is_symlink() or getattr(item.lstat(), 'st_file_attributes', 0) & 0x400:
                    raise ValueError('Lien trouvé dans les fichiers de travail.')
                if target != game and item.is_file() and item.suffix.lower() in {'.iso', '.cue', '.ps2', '.mcd', '.mcr', '.sav', '.state', '.png', '.jpg', '.jpeg'}:
                    raise ValueError('Fichier personnel possible dans les fichiers de travail.')
        for target in targets:
            if target.exists(): shutil.rmtree(target)
        log('Fichiers temporaires PS2 supprimés ; image d’origine conservée.\n')
    except (OSError, ValueError, UnicodeError) as exc:
        log('Fichiers temporaires PS2 conservés : ' + str(exc) + '\n')


def ps2_catalogue_title(serial):
    """Resolve the disc serial using the PCSX2 catalogue, cached locally."""
    compact = re.sub(r'[^A-Z0-9]', '', str(serial).upper())
    if not re.fullmatch(r'[A-Z]{4}\d{5}', compact):
        return None
    key = compact[:4] + '-' + compact[4:]
    cache = Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData/Local')) / 'PhantomStation' / 'Cache' / 'pcsx2-gameindex.yaml'
    try:
        if cache.is_file() and cache.stat().st_size <= 6 * 1024 * 1024 and time.time() - cache.stat().st_mtime < 30 * 86400:
            content = cache.read_text(encoding='utf-8')
        else:
            request = urllib.request.Request('https://raw.githubusercontent.com/PCSX2/pcsx2/master/bin/resources/GameIndex.yaml', headers={'User-Agent': 'PhantomStation/1.0'})
            with urllib.request.urlopen(request, timeout=8) as response:
                data = response.read(6 * 1024 * 1024 + 1)
            if len(data) > 6 * 1024 * 1024:
                return None
            content = data.decode('utf-8')
            cache.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile('wb', dir=cache.parent, delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(data)
            try:
                os.replace(temporary, cache)
            finally:
                temporary.unlink(missing_ok=True)
        section = re.search(r'(?m)^' + re.escape(key) + r':\s*\r?\n((?:[ \t].*\r?\n|\s*\r?\n)*)', content)
        match = re.search(r'(?m)^\s+name:\s*("(?:[^"\\]|\\.)*")\s*$', section.group(1)) if section else None
        return json.loads(match.group(1)) if match else None
    except (OSError, ValueError, UnicodeError):
        return None


def make_icon(game, iso, chosen, work, log, serial=None):
    source = None
    if chosen:
        source = _decode_cover(Path(chosen).read_bytes())
    else:
        canonical = ps2_catalogue_title(serial) if serial else None
        region = 'Europe' if str(serial).upper().startswith(('SLES', 'SCES')) else ('USA' if str(serial).upper().startswith(('SLUS', 'SCUS')) else 'Japan')
        titles = [iso.stem, game]
        if canonical:
            titles.extend([canonical + ' (' + region + ')', canonical])
        for title in dict.fromkeys(titles):
            url = 'https://raw.githubusercontent.com/libretro-thumbnails/Sony_-_PlayStation_2/master/Named_Boxarts/' + urllib.parse.quote(title + '.png')
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent':'PhantomStation/1.0'}), timeout=4) as response:
                    raw = response.read(8 * 1024 * 1024 + 1)
                if len(raw) > 8 * 1024 * 1024: continue
                source = _decode_cover(raw); log('Jaquette PS2 trouvée : ' + title + '.\n'); break
            except (OSError, ValueError): continue
    if source is None:
        source = _decode_cover((BASE / 'assets/phantom-startup.png').read_bytes())
        log('Jaquette PS2 indisponible : image PhantomStation utilisée.\n')
    cover = work / 'boxart.tga'; source.convert('RGBA').save(cover)
    source.convert('RGBA').save(work / 'boxart.png')
    source.thumbnail((256, 256), Image.Resampling.LANCZOS)
    square = Image.new('RGBA', (256, 256), (4, 10, 24, 255))
    square.alpha_composite(source, ((256-source.width)//2, (256-source.height)//2))
    icon = work / 'icone.ico'; square.save(icon, format='ICO', sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])
    return icon, cover


def ps2_crc_and_code_ranges(elf):
    data = Path(elf).read_bytes()
    if data[:6] != b'\x7fELF\x01\x01' or len(data) < 52: raise ValueError('Programme PS2 ELF invalide.')
    crc = 0
    for (word,) in struct.iter_unpack('<I', data[:len(data) & ~3]): crc ^= word
    phoff = struct.unpack_from('<I', data, 28)[0]
    entsize, count = struct.unpack_from('<HH', data, 42)
    if entsize < 32 or count > 1000 or phoff + entsize * count > len(data): raise ValueError('Segments PS2 invalides.')
    ranges = []
    for i in range(count):
        kind, offset, address, _, size, _, flags, _ = struct.unpack_from('<8I', data, phoff + i * entsize)
        if kind == 1 and flags & 1: ranges.append((address & 0x1fffffff, (address & 0x1fffffff) + size))
    return f'{crc:08X}', ranges


def parse_ps2_codes(text, code_ranges):
    """Keep single, unconditional EE data writes; reject executable patches."""
    result = []; heading = ''; group = []; invalid = False
    def flush():
        if not heading or invalid or len(group) != 1: return
        typ, address, size, value = group[0]
        if address + size > 0x02000000 or address % size or any(address < end and address + size > start for start, end in code_ranges): return
        if value >= 1 << (8 * size): return
        label = re.sub(r'[\t\r\n]+', ' ', heading).strip()[:75]
        for eng, fr in (('Infinite Health', 'Vie illimitée'), ('Infinite Ammo', 'Munitions illimitées'), ('Max Money', 'Argent maximum')):
            label = re.sub(re.escape(eng), fr, label, flags=re.I)
        if label: result.append((label + ' (à vérifier)', f'{(typ << 28) | address:08X} {value:08X}'))
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith('[') and line.endswith(']'):
            flush(); heading = line[1:-1].strip(); group = []; invalid = False
        elif line.startswith('//') and not group:
            heading = line[2:].strip()
        elif line.lower().startswith('patch='):
            parts = [x.strip() for x in line[6:].split(',')]
            if len(parts) != 5 or parts[0] != '1' or parts[1].upper() != 'EE' or not re.fullmatch(r'[0-9A-Fa-f]{8}', parts[2]) or not re.fullmatch(r'[0-9A-Fa-f]{8}', parts[4]):
                invalid = True; continue
            first, value = int(parts[2], 16), int(parts[4], 16); mode = parts[3].lower()
            if mode == 'extended': typ = first >> 28
            else: typ = {'byte':0, 'short':1, 'word':2}.get(mode, -1)
            if typ not in (0, 1, 2): invalid = True; continue
            group.append((typ, first & 0x0fffffff, (1,2,4)[typ], value))
        elif line.startswith('//') and group:
            flush(); heading = line[2:].strip(); group = []; invalid = False
    flush()
    return result[:100]


def find_ps2_codes(serial, elf, log):
    normalized = re.sub(r'[^A-Z0-9]', '', serial.upper())
    match = re.fullmatch(r'([A-Z]{4})([0-9]{5})', normalized)
    if not match: return [], []
    crc, ranges = ps2_crc_and_code_ranges(elf)
    label = match.group(1) + '-' + match.group(2)
    urls = [f'https://raw.githubusercontent.com/complicatiion/pcsx2_europe_cheats_collection/main/cheats/{label}_{crc}.pnach',
            f'https://raw.githubusercontent.com/xs1l3n7x/pcsx2_cheats_collection/main/cheats/{crc}.pnach']
    found = []; sources = []
    for url in urls:
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent':'PhantomStation/1.0'}), timeout=5) as response:
                raw = response.read(2 * 1024 * 1024 + 1)
            if len(raw) > 2 * 1024 * 1024: continue
            text = raw.decode('utf-8-sig', errors='replace')
            mentioned = {re.sub(r'[^A-Z0-9]', '', x.upper()) for x in re.findall(r'\b(?:SLES|SLUS|SCES|SCUS|SLPM|SLPS)[-_ ]?\d{3}[. -]?\d{2}\b', '\n'.join(text.splitlines()[:10]), re.I)}
            if mentioned and normalized not in mentioned: continue
            parsed = parse_ps2_codes(text, ranges)
            if parsed: sources.append(url); found.extend(parsed)
        except (OSError, ValueError): continue
    unique = list(dict.fromkeys(found))[:100]
    log(f'{len(unique)} code(s) PS2 simples trouvés pour {label} / {crc} ; effets à vérifier.\n')
    return unique, sources


def build_stub(icon, output, temp, background=None):
    from phantom_intro import visible_ps2_logo
    visible_logo = temp / 'logo-ps2-lisible.png'
    with Image.open(BASE / 'assets/logo-ps2.png') as logo:
        visible_ps2_logo(logo).save(visible_logo)
    ps2_sound = BASE / 'assets/son-demarrage-ps2.wav'
    if not ps2_sound.is_file():
        raise RuntimeError('Son de démarrage PS2 introuvable. Sélectionne un fichier WAV PS2 dans le compilateur.')
    original = (BASE / 'unique_launcher.cs').read_text(encoding='utf-8-sig')
    target = '            string game = task.Result;\n'
    if original.count(target) != 1: raise RuntimeError('Le lanceur PS2 ne correspond plus au lanceur PhantomStation.')
    replacement = target + '            Hide(); var gameWindow = new Ps2GameForm(game); gameWindow.FormClosed += (s, e) => Close(); gameWindow.Show(); return;\n'
    original = original.replace(target, replacement)
    original = original.replace("string name = Path.GetFileNameWithoutExtension(game).Replace('_', ' ').Trim();", "string name = DisplayTitle(PreparedGameTitle).Replace('_', ' ').Trim();")
    # Leave enough time for the full PS2 startup recording to play and fade out.
    if original.count('clock.Elapsed.TotalSeconds >= 7.7') != 1 or original.count('clock.Elapsed.TotalSeconds < 8.4') != 1 or original.count('(t - 7.7f) / 0.7f') != 1:
        raise RuntimeError('La durée de l’introduction PS2 ne peut pas être réglée.')
    original = original.replace('clock.Elapsed.TotalSeconds >= 7.7', 'clock.Elapsed.TotalSeconds >= 9.3')
    original = original.replace('clock.Elapsed.TotalSeconds < 8.4', 'clock.Elapsed.TotalSeconds < 10.0')
    original = original.replace('(t - 7.7f) / 0.7f', '(t - 9.3f) / 0.7f')
    source = temp / 'ps2-combine.cs'; source.write_text(original, encoding='utf-8')
    if not PYTHON.is_file(): raise RuntimeError('Compilateur Windows .NET introuvable.')
    artwork = BASE / 'assets/phantom-startup.png'
    if background:
        artwork = temp / 'fond.png'
        with Image.open(background) as chosen:
            chosen.convert('RGB').save(artwork, format='PNG', optimize=True, compress_level=9)
    cmd = [str(PYTHON), '/nologo', '/target:winexe', '/out:' + str(output), '/reference:System.Windows.Forms.dll', '/reference:System.Drawing.dll',
           '/reference:System.IO.Compression.dll', '/reference:System.IO.Compression.FileSystem.dll', '/win32icon:' + str(icon),
           '/resource:' + str(artwork) + ',art', '/resource:' + str(visible_logo) + ',logo',
           '/resource:' + str(ps2_sound) + ',sound', str(source), str(BASE/'phantom_ps2_launcher.cs'),
           str(BASE/'phantom_ps2_settings.cs'), str(BASE/'phantom_embedded_game.cs')]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors='replace', creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode: raise RuntimeError('Création du lanceur PS2 impossible : ' + result.stdout.strip())


def large_game_launcher(package):
    """Build a small launcher that reads an existing oversized PS2 package in place."""
    package = Path(package).resolve(strict=True)
    from bibliotheque import game_info
    if game_info(package, include_cover=False)['platform'] != 'PS2':
        raise ValueError('Ce jeu dépasse 4 Gio. L’ouverture des anciens fichiers volumineux est disponible pour les jeux PS2.')
    return compatible_game_launcher(package)


def compatible_game_launcher(package, compact=False):
    """Run an existing package with the current launcher, without rewriting it."""
    package = Path(package).resolve(strict=True)
    from bibliotheque import game_info
    platform = game_info(package, include_cover=False)['platform']
    dc_profile = None
    before = package.stat()
    with tempfile.TemporaryDirectory(prefix='phantom-grand-jeu-') as td:
        work = Path(td)
        with zipfile.ZipFile(package) as archive:
            entries = archive.infolist()
            if len({entry.filename for entry in entries}) != len(entries):
                raise ValueError('Archive du jeu invalide : fichiers en double.')
            offset = min(entry.header_offset for entry in entries)
            if not 0 < offset < 64 * 1024 * 1024:
                raise ValueError('Le lanceur de ce jeu est invalide.')
            if platform == 'DC':
                info = archive.getinfo('game.toml')
                if info.file_size > 1024 * 1024:
                    raise ValueError('Configuration du jeu trop volumineuse.')
                config = tomllib.loads(archive.read(info).decode('utf-8-sig'))
                # Builds made before profiles existed were Crazy Taxi only.
                dc_profile = config.get('game', {}).get('profile', 'crazy_taxi_pal')
                if dc_profile not in ('crazy_taxi_pal', 'generic_experimental', 'house_pal_experimental'):
                    dc_profile = 'generic_experimental'
            icon = work / 'icone.ico'
            old_stub = work / 'ancien-lanceur.exe'
            with package.open('rb') as original, old_stub.open('wb') as output:
                left = offset
                while left:
                    block = original.read(min(left, 4 * 1024 * 1024))
                    if not block: raise ValueError('Lanceur de jeu incomplet.')
                    output.write(block); left -= len(block)
        background = work / 'fond.png'
        env = os.environ.copy()
        env['PHANTOM_SOURCE_STUB'] = str(old_stub)
        env['PHANTOM_ART_OUT'] = str(background)
        script = r'''$ErrorActionPreference='Stop'
$a=[Reflection.Assembly]::LoadFile($env:PHANTOM_SOURCE_STUB)
$s=$a.GetManifestResourceStream('art')
if ($null -eq $s) { throw 'Fond du jeu absent.' }
$f=[IO.File]::Create($env:PHANTOM_ART_OUT)
try { $s.CopyTo($f) } finally { $f.Dispose(); $s.Dispose() }'''
        result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                                env=env, capture_output=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode: raise OSError('Impossible de lire le fond du jeu.')
        _extract_windows_icon(old_stub, icon)
        if compact:
            # PNG recompression preserves pixels and avoids rewriting the large
            # archive when replacing an older, smaller launcher prefix.
            from PIL import ImageChops
            candidate = work / 'fond-optimise.png'
            with Image.open(background) as original:
                if original.format == 'PNG':
                    kwargs = {'optimize': True, 'compress_level': 9}
                    if original.info.get('icc_profile'):
                        kwargs['icc_profile'] = original.info['icc_profile']
                    if 'transparency' in original.info:
                        kwargs['transparency'] = original.info['transparency']
                    original.save(candidate, **kwargs)
                    with Image.open(candidate) as optimized:
                        if (candidate.stat().st_size < background.stat().st_size and
                                ImageChops.difference(original.convert('RGBA'), optimized.convert('RGBA')).getbbox() is None):
                            background = candidate
        digest = hashlib.sha256()
        digest.update(b'compact-v1' if compact else b'ordinary-v1')
        if dc_profile is not None: digest.update(dc_profile.encode('ascii'))
        inputs = [old_stub, icon, BASE/'unique_launcher.cs', BASE/'phantom_ps2.py', BASE/'emballer.py']
        if platform == 'PS2':
            inputs += [BASE/'phantom_ps2_launcher.cs', BASE/'phantom_ps2_settings.cs',
                       BASE/'phantom_embedded_game.cs', BASE/'assets/logo-ps2.png', BASE/'assets/son-demarrage-ps2.wav']
        elif platform in ('NES', 'SNES', 'N64'):
            inputs += [BASE/'phantom_nintendo.py', BASE/'phantom_nintendo_launcher.cs', BASE/'phantom_goldeneye_profile.cs', BASE/'phantom_embedded_game.cs',
                       BASE/'assets'/('logo-' + platform.lower() + '.png'), BASE/'assets'/('son-demarrage-' + platform.lower() + '.wav')]
        elif platform == 'DC':
            inputs += [BASE/'phantom_dreamcast.py', BASE/'phantom_dreamcast_launcher.cs', BASE/'phantom_dreamcast_vmu.cs', BASE/'phantom_embedded_game.cs',
                       BASE/'assets/logo-dreamcast.png', BASE/'assets/son-demarrage-dreamcast.wav']
        else:
            inputs += [BASE/'assets/playstation-logo.png', BASE/'assets/son-demarrage.wav']
        for item in inputs:
            with item.open('rb') as stream: digest.update(hashlib.file_digest(stream, 'sha256').digest())
        cache = Path(os.environ.get('LOCALAPPDATA', Path.home()/'AppData/Local')) / 'PhantomStation' / 'Lanceurs-compatibles'
        cache.mkdir(parents=True, exist_ok=True)
        launcher = cache / (platform.lower() + '-' + digest.hexdigest()[:24] + '.exe')
        if not launcher.is_file():
            built = work / 'lanceur.exe'
            if platform == 'PS2':
                build_stub(icon, built, work, background)
            elif platform in ('NES', 'SNES', 'N64'):
                from phantom_nintendo import build_nintendo_stub
                build_nintendo_stub(platform, icon, built, work, background)
            elif platform == 'DC':
                from phantom_dreamcast import build_stub
                build_stub(icon, built, work, background,
                           generic=dc_profile != 'crazy_taxi_pal',
                           house=dc_profile == 'house_pal_experimental')
            else:
                from emballer import _build_stub
                _build_stub(icon, built, background)
            temporary = cache / (launcher.name + '.' + uuid.uuid4().hex + '.tmp')
            try:
                shutil.copyfile(built, temporary)
                try: os.rename(temporary, launcher)
                except FileExistsError: pass
            finally: temporary.unlink(missing_ok=True)
        after = package.stat()
        if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
            raise OSError('Le fichier du jeu a changé pendant sa préparation.')
        return launcher


def package_ps2(name, iso, runtime, elf, serial, output, icon_path=None, log=lambda s: None, progress=None, background=None, package_id=None, players=2):
    if players not in (1, 2): raise ValueError('Le moteur PS2 gère 1 ou 2 joueurs.')
    iso, runtime, elf, output = map(Path, (iso, runtime, elf, output))
    output.mkdir(parents=True, exist_ok=True)
    title = re.sub(r"[^\w. '’-]+", '-', name, flags=re.UNICODE).strip(' .') or 'Jeu PS2'
    destination = output / (title + '.exe'); number = 2
    while destination.exists():
        destination = output / (title + ' (' + str(number) + ').exe'); number += 1
    partial = output / (destination.name + '.' + uuid.uuid4().hex + '.part')
    package_id = uuid.UUID(hex=package_id).hex if package_id else uuid.uuid4().hex
    try:
        with tempfile.TemporaryDirectory(prefix='phantom-ps2-') as td:
            temp = Path(td); icon, cover = make_icon(name, iso, icon_path, temp, log, serial)
            build_stub(icon, partial, temp, background)
            files = [(runtime/'ps2run.exe', 'ps2run.exe'), (runtime/'SDL2.dll', 'SDL2.dll'),
                     (elf, 'ps2-boot.elf'), (iso, 'media/disque.iso'), (cover, 'assets/img/boxart.tga'),
                     (temp/'boxart.png', 'assets/img/boxart.png'),
                     (ENGINE/'LICENSE', 'licences/ps2-native-recomp-MIT.txt'),
                     (ENGINE/'runtime/src/ipu/NOTICE', 'licences/NOTICE-Play-ipu.txt'),
                     (BASE/'assets/carte-ps2-vierge.ps2', 'ps2-carte-vierge.ps2'),
                     (BASE/'assets/LICENSE-mymc-py.txt', 'licences/LICENSE-mymc-py.txt'),
                     (BASE/'assets/NOTICE-mymc-py.txt', 'licences/NOTICE-mymc-py.txt'),
                     (BASE/'assets/LICENSE-SDL2.txt', 'licences/LICENSE-SDL2.txt'),
                     (BASE/'assets/LICENSE-wgpu-MIT.txt', 'licences/LICENSE-wgpu-MIT.txt'),
                     (BASE/'assets/LICENSE-wgpu-APACHE.txt', 'licences/LICENSE-wgpu-APACHE.txt')]
            if any(not path.is_file() for path, _ in files): raise RuntimeError('Un fichier nécessaire au jeu PS2 manque.')
            snapshots = {path: (path.stat().st_size, path.stat().st_mtime_ns, path.stat().st_ino) for path, _ in files}
            with (runtime/'ps2run.exe').open('rb') as stream: digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            manifest = (package_id + '\nps2run.exe\n' + digest + '\n' + base64.b64encode(name.encode()).decode() + '\ndocuments-saves-v1\n').encode()
            config = ('[game]\nname = ' + json.dumps(name, ensure_ascii=False) + '\nid = ' + json.dumps(serial) + '\nplatform = "PS2"\n').encode()
            found, sources = find_ps2_codes(serial, elf, log)
            codes = ''.join('0\t' + name + '\t' + code + '\n' for name, code in found)
            if re.sub(r'[^A-Z0-9]', '', serial.upper()) == 'SLES50306':
                # Source identifies the serial, not an ELF checksum; effects
                # remain unverified and are disabled until the player opts in.
                codes += ('0\tVie illimitée (à vérifier)\t20448F2C 000000C8\n'
                          '0\tTemps de jeu à zéro (à vérifier)\t2044A1D0 00000000\n')
                sources.append('https://gamehacking.org/game/102394 (numéro de série, CRC non indiqué)')
            from detection_doublons import SOURCE_ENTRY, encoded_source
            inline = [('phantom-package.txt', manifest), ('game.toml', config),
                      (SOURCE_ENTRY, encoded_source('PS2', [[iso]])),
                      ('phantom-players.txt', (str(players) + '\n').encode('ascii')),
                      ('ps2-codes-defaut.tsv', codes.encode('utf-8')),
                      ('licences/source-codes-ps2.txt', ('\n'.join(sources) + '\n').encode('utf-8'))]
            total = sum(p.stat().st_size for p, _ in files) + sum(len(data) for _, data in inline)
            compress_disc = partial.stat().st_size + total + 1024 * 1024 >= WINDOWS_EXE_LIMIT
            if compress_disc:
                log('Compression du disque PS2 pour que le fichier unique puisse être lancé sous Windows…\n')
            done = 0; last = -1
            with zipfile.ZipFile(partial, 'a', compression=zipfile.ZIP_DEFLATED, compresslevel=3, allowZip64=True) as archive:
                for filename, data in inline:
                    archive.writestr(filename, data); done += len(data)
                for src, filename in files:
                    info = zipfile.ZipInfo.from_file(src, filename)
                    info.compress_type = zipfile.ZIP_STORED if filename == 'media/disque.iso' and not compress_disc else zipfile.ZIP_DEFLATED
                    info._compresslevel = 3
                    with src.open('rb') as reader, archive.open(info, 'w', force_zip64=True) as writer:
                        while True:
                            block = reader.read(4*1024*1024)
                            if not block: break
                            writer.write(block); done += len(block)
                            pct = done * 100 // total
                            if progress and pct != last: progress(done, total, 'Création du fichier unique'); last = pct
                    state = src.stat()
                    if snapshots[src] != (state.st_size, state.st_mtime_ns, state.st_ino):
                        raise RuntimeError('Un fichier du jeu a changé pendant la création : ' + str(src))
            if partial.stat().st_size >= WINDOWS_EXE_LIMIT:
                log('Le jeu dépasse encore 4 Gio après compression : lance-le depuis la bibliothèque PhantomStation.\n')
            log('Vérification du fichier unique PS2…\n')
            done = 0; last = -1
            with zipfile.ZipFile(partial) as archive:
                for info in archive.infolist():
                    with archive.open(info) as reader:
                        while True:
                            block = reader.read(4*1024*1024)
                            if not block: break
                            done += len(block); pct = done * 100 // total
                            if progress and pct != last: progress(done, total, 'Vérification du fichier unique'); last = pct
                if done != total: raise RuntimeError('Vérification du fichier unique incomplète.')
            # Avoid replacing an existing game if another build won the same name.
            os.rename(partial, destination)
        return destination
    finally:
        partial.unlink(missing_ok=True)
