"""Mises à jour signées ; les données personnelles sont exclues."""
import base64, ctypes, hashlib, hmac, io, json, os, re, shutil, stat, subprocess, sys, tempfile, urllib.request, zipfile
from pathlib import Path, PurePosixPath

BASE = Path(__file__).resolve().parent
VERSION_FILE = BASE / 'phantom_version.json'
MANIFEST_URL = 'https://gears.novalysdev.fr/phantomstation-updates/manifest.json'
DOWNLOAD_BASE = 'https://gears.novalysdev.fr/phantomstation-updates/'
PUBLIC_N = int('ab68241af72136ab8e658e56e4252d40a7cd178d2a5067148e544da2c28f5acc77329524ec2050444bf38cc529f8f39c004f8103ec6a741824230f3954cfb179c6986451e5a58bc42e43fc7518205f565b509ce48e3e0b6495301f188047acaf15b029ec1b1d7a4f3c04c618ade19331c328bc6a927ec5434034b3c0bc040a070b90d4f27029211c34851bcfb49c65341fc0163e985a7747b8274b64d6d0f8a3da2b9b1bfb0981bdafd8e57ff8da95321cc112992fa35720238cc9253b4ef28df3f0b4a1203ec606389932a97f0ff78ddc270f56c0f21d648702d8de19621b2e63c204ca496f87367d9b01f6cb8b8f7dc60d4c6ff1178e80aa649daba9d86bd7455c86fd17f81d50b851c4b357e65b48238a2f9f0ccd0659333717249cdd6cbc34672c0d575460ab20a0ab98f182a1e70b5ebb65041704a3e4dbe8d5d117897e533e17d730f96e395998dc42c49e029ba5a18dfe1b2d102fe48caf1cd6ae3d2e2e037b78940142701f5a22b7b5a494dd92c5043d97c733d9686a7cafcb97f11f', 16)
MAX_ARCHIVE = 100 * 1024 * 1024
MAX_EXTRACTED = 250 * 1024 * 1024
ROOT_FILES = frozenset('''lanceur.py bibliotheque.py gestion_jeux.py cartes_memoire.py detection_doublons.py emballer.py diagnostic_jeu.py phantom_report_hotkey.py phantom_dc_autolearn.py phantom_ui.py phantom_intro.py phantom_library_ui.py phantom_report_ui.py rapport_erreur.py phantom_window_icon.py phantom_updates.py phantom_update_ui.py phantom_portable.py phantom_compiler_game.py phantom_embedded_game.cs phantom_n64_rom.py phantom_dreamcast.py phantom_dreamcast_launcher.cs phantom_dreamcast_vmu.cs phantom_nintendo.py phantom_nintendo_launcher.cs phantom_goldeneye_profile.cs phantom_ps2.py phantom_ps2_launcher.cs phantom_ps2_settings.cs ps2-source-manifest.txt sbi_support.py unique_launcher.cs unique_stub.exe phantom_compiler.cs PhantomStation.exe phantom_cheat_search_runtime.hpp phantom_cheat_search.hpp phantom_codes.hpp phantom_controller_auto.hpp phantom_sbi_support.hpp LISEZ-MOI.md installer_bios.py installer_bios_display.py installer_build_progress.py installer_cheat_search.py installer_codes.py installer_controller_auto.py installer_controller_database.py installer_disc_hotkey.py installer_disc_menu.py installer_easy_settings.py installer_fps.py installer_intro.py installer_menu_keyboard.py installer_play_button.py installer_save_directory.py installer_sbi_display.py installer_theme.py installer_window_icon.py'''.split())
EXTRA_FILES = frozenset({'psxrecomp/tools/new_project_layout/setup_project.ps1'} | {
    'assets/' + name for name in '''codes-fr.json codes-libretro-fr.json gamecontrollerdb.txt LICENSE-CHTDB.txt LICENSE-LIBRETRO.txt LICENSE-SDL-GameControllerDB.txt phantom-startup.png phantomstation-symboles.png phantomstation.ico phantomstation.png playstation-logo.png playstation-logo.svg sbi-index.json son-demarrage.wav son-erreur-ps2.wav SOURCE-son-erreur-ps2.txt carte-ps2-vierge.ps2 LICENSE-mymc-py.txt NOTICE-mymc-py.txt LICENSE-SDL2.txt LICENSE-wgpu-MIT.txt LICENSE-wgpu-APACHE.txt'''.split()
})
try:
    PS2_ENGINE_FILES = frozenset('ps2-native-recomp/' + line for line in
        (BASE / 'ps2-source-manifest.txt').read_text(encoding='utf-8-sig').splitlines()
        if line and not line.startswith('.') and '..' not in PurePosixPath(line).parts)
except OSError:
    PS2_ENGINE_FILES = frozenset()

class UpdateError(Exception): pass

def parse_version(value):
    if not isinstance(value, str) or not re.fullmatch(r'(?:0|[1-9][0-9]{0,3})\.(?:0|[1-9][0-9]{0,3})\.(?:0|[1-9][0-9]{0,3})', value):
        raise UpdateError('Numéro de version invalide.')
    return tuple(map(int, value.split('.')))

def local_version():
    try: return parse_version(json.loads(VERSION_FILE.read_text(encoding='utf-8-sig'))['version'])
    except (OSError, KeyError, TypeError, ValueError, UpdateError): return (1, 0, 0)

def verify_signature(data, signature):
    size = (PUBLIC_N.bit_length() + 7) // 8
    if len(signature) != size or int.from_bytes(signature, 'big') >= PUBLIC_N: return False
    actual = pow(int.from_bytes(signature, 'big'), 65537, PUBLIC_N).to_bytes(size, 'big')
    digest_info = bytes.fromhex('3031300d060960864801650304020105000420') + hashlib.sha256(data).digest()
    expected = b'\x00\x01' + b'\xff' * (size - len(digest_info) - 3) + b'\x00' + digest_info
    return hmac.compare_digest(actual, expected)

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl): return None

def fetch(url, maximum, progress=None):
    request = urllib.request.Request(url, headers={'User-Agent': 'PhantomStation-Update/1', 'Accept': 'application/octet-stream'})
    with urllib.request.build_opener(NoRedirect()).open(request, timeout=12) as response:
        chunks = []; received = 0
        while True:
            chunk = response.read(min(256 * 1024, maximum + 1 - received))
            if not chunk: break
            chunks.append(chunk); received += len(chunk)
            if received > maximum: raise UpdateError('La mise à jour dépasse la taille autorisée.')
            if progress is not None: progress(received)
    return b''.join(chunks)

def check_update():
    try:
        envelope = json.loads(fetch(MANIFEST_URL, 65536))
        content = base64.b64decode(envelope['contenu'], validate=True)
        signature = base64.b64decode(envelope['signature'], validate=True)
        if not verify_signature(content, signature): raise UpdateError('La signature de la mise à jour est invalide.')
        manifest = json.loads(content)
        version = parse_version(manifest['version'])
        if version <= local_version(): return None
        filename, digest, size = manifest['fichier'], manifest['sha256'], manifest['taille']
        if not isinstance(filename, str) or filename != 'PhantomStation-' + manifest['version'] + '.zip':
            raise UpdateError('Nom de fichier de mise à jour invalide.')
        if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest): raise UpdateError('Empreinte invalide.')
        if type(size) is not int or not 0 < size <= MAX_ARCHIVE: raise UpdateError('Taille invalide.')
        return manifest
    except UpdateError: raise
    except (OSError, ValueError, TypeError, KeyError) as exc: raise UpdateError('Impossible de vérifier les mises à jour.') from exc

def allowed(name):
    if not isinstance(name, str) or '\\' in name or '\x00' in name: return False
    p = PurePosixPath(name)
    if p.is_absolute() or name != p.as_posix() or any(x in ('', '.', '..') or ':' in x for x in p.parts): return False
    return name in ROOT_FILES or name in EXTRA_FILES or name in PS2_ENGINE_FILES

def download_update(manifest, progress=None):
    data = fetch(DOWNLOAD_BASE + manifest['fichier'], min(MAX_ARCHIVE, manifest['taille']), progress)
    if len(data) != manifest['taille'] or not hmac.compare_digest(hashlib.sha256(data).hexdigest(), manifest['sha256']):
        raise UpdateError('Le téléchargement est incomplet ou son empreinte est incorrecte.')
    return data

def install_update(manifest, data, base=BASE):
    base = Path(base).resolve()
    parse_version(manifest['version'])
    if len(data) != manifest['taille'] or not hmac.compare_digest(hashlib.sha256(data).hexdigest(), manifest['sha256']): raise UpdateError('Empreinte incorrecte.')
    changed = []
    temp = Path(tempfile.mkdtemp(prefix='.phantom-maj-', dir=base))
    preserve_backup = False
    try:
        staged = temp / 'nouveau'; backups = temp / 'precedent'
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                infos = archive.infolist()
                if not infos or len(infos) > 1000: raise UpdateError('Contenu de mise à jour invalide.')
                names = set(); total = 0
                for info in infos:
                    name = info.filename; key = name.casefold()
                    if info.is_dir() or not allowed(name) or key in names or info.flag_bits & 1 or stat.S_ISLNK(info.external_attr >> 16): raise UpdateError('Fichier non autorisé dans la mise à jour.')
                    names.add(key); total += info.file_size
                    if total > MAX_EXTRACTED: raise UpdateError('La mise à jour est trop volumineuse.')
                    target = staged.joinpath(*PurePosixPath(name).parts)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(info) as source, target.open('xb') as out: shutil.copyfileobj(source, out, 1024 * 1024)
            (staged / 'phantom_version.json').write_text(json.dumps({'version': manifest['version']}), encoding='utf-8')
            for name in [info.filename for info in infos] + ['phantom_version.json']:
                parts = PurePosixPath(name).parts; dest = base.joinpath(*parts); parent = base
                for part in parts[:-1]:
                    parent = parent / part
                    if parent.is_symlink(): raise UpdateError('Dossier de mise à jour non sécurisé.')
                    parent.mkdir(exist_ok=True)
                if dest.is_symlink() or (dest.exists() and not dest.is_file()): raise UpdateError('Fichier du programme non remplaçable.')
                existed = dest.is_file()
                if existed:
                    backup = backups.joinpath(*parts); backup.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(dest, backup)
                os.replace(staged.joinpath(*parts), dest)
                changed.append((dest, backups.joinpath(*parts) if existed else None))
        except Exception as exc:
            errors = []
            for dest, backup in reversed(changed):
                try:
                    if backup is None: dest.unlink(missing_ok=True)
                    else: os.replace(backup, dest)
                except OSError: errors.append(str(dest))
            if errors:
                preserve_backup = True
                raise UpdateError('Restauration incomplète. Fichiers précédents conservés dans : ' + str(backups)) from exc
            if isinstance(exc, UpdateError): raise
            raise UpdateError('Installation impossible ; les fichiers précédents ont été restaurés.') from exc
    finally:
        if not preserve_backup:
            shutil.rmtree(temp, ignore_errors=True)


def _remove_deferred_files(folder):
    """Remove only the files created for this deferred update."""
    folder = Path(folder)
    for name in ('manifest.json', 'archive.zip'):
        try:
            (folder / name).unlink(missing_ok=True)
        except OSError:
            pass
    try:
        folder.rmdir()
    except OSError:
        pass


def _wait_for_parent(pid, timeout_ms=120000):
    """Wait until the running launcher releases its executable on Windows."""
    if os.name != 'nt':
        raise UpdateError('Le redémarrage différé nécessite Windows.')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel.WaitForSingleObject.restype = ctypes.c_uint32
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
    if not handle:
        error = ctypes.get_last_error()
        if error == 87:  # The parent has already exited.
            return
        raise ctypes.WinError(error)
    try:
        result = kernel.WaitForSingleObject(handle, timeout_ms)
        if result != 0:  # WAIT_OBJECT_0
            raise UpdateError('La fermeture du compilateur a pris trop de temps.')
    finally:
        kernel.CloseHandle(handle)


def apply_deferred_update(base, folder, parent_pid):
    """Apply a verified download after the launcher exits, then reopen it."""
    base, folder = Path(base).resolve(), Path(folder).resolve()
    try:
        _wait_for_parent(int(parent_pid))
        manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
        data = (folder / 'archive.zip').read_bytes()
        install_update(manifest, data, base)
        subprocess.Popen([str(base / 'PhantomStation.exe')], cwd=str(base), close_fds=True)
    finally:
        _remove_deferred_files(folder)


def defer_update(manifest, data, base=BASE, parent_pid=None, python=None):
    """Prepare an update and start a helper which waits for this process."""
    base = Path(base).resolve()
    if len(data) != manifest['taille'] or not hmac.compare_digest(hashlib.sha256(data).hexdigest(), manifest['sha256']):
        raise UpdateError('Empreinte incorrecte.')
    folder = Path(tempfile.mkdtemp(prefix='phantom-update-'))
    try:
        (folder / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
        (folder / 'archive.zip').write_bytes(data)
        code = (
            "import ctypes,sys;from pathlib import Path;"
            "sys.path.insert(0,sys.argv[1]);import phantom_updates as u;"
            "u.run_deferred_update(sys.argv[1],sys.argv[2],int(sys.argv[3]))"
        )
        return subprocess.Popen([str(python or sys.executable), '-c', code, str(base), str(folder),
                                 str(parent_pid if parent_pid is not None else os.getpid())],
                                cwd=str(base), close_fds=True,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except Exception:
        _remove_deferred_files(folder)
        raise


def run_deferred_update(base, folder, parent_pid):
    try:
        apply_deferred_update(base, folder, parent_pid)
    except Exception as exc:
        message = 'La mise à jour n’a pas pu être installée. Rouvre PhantomStation.\n\n' + str(exc)
        if os.name == 'nt':
            ctypes.windll.user32.MessageBoxW(None, message, 'PhantomStation — mise à jour', 0x10)
        else:
            raise
