import os
import filecmp
import json
import queue
import re
import subprocess
import struct
import sys
import threading
import uuid
import tempfile
import wave
import tomllib
import tkinter as tk
from pathlib import Path
from phantom_portable import configure_portable_tools, local_ps1_dependencies, stage_ps1_archives
from tkinter import filedialog, messagebox, ttk
from PIL import Image, ImageTk
from phantom_intro import PhantomIntro
from bibliotheque import register_game, discover_games
from detection_doublons import find_duplicates
from phantom_library_ui import LibraryView
from phantom_compiler_game import GameInCompiler
from phantom_ui import ctk, button, panel, HeroBanner, BG, SURFACE, CARD, BORDER, TEXT, MUTED, ACCENT, ACCENT_HOVER
from phantom_window_icon import set_process_identity, set_window_icon
from phantom_report_ui import open_report_dialog
from phantom_update_ui import start_update_check
from phantom_ps2 import build_ps2, package_ps2, cleanup_ps2_build, ENGINE as PS2_ENGINE
from rapport_erreur import report_text, save_report
from emballer import package_game, _cue_files
from installer_bios import install_phantom_bios
from installer_intro import install_intro_hook
from installer_play_button import install_play_button
from installer_disc_menu import install_disc_menu_fr
from installer_disc_hotkey import install_disc_hotkey
from installer_sbi_display import install_sbi_display
from installer_controller_auto import install_controller_auto
from installer_controller_database import install_controller_database
from installer_cheat_search import install_cheat_search
from installer_menu_keyboard import install_menu_keyboard
from installer_window_icon import install_window_icon
from installer_bios_display import install_bios_display_fix
from installer_easy_settings import install_easy_settings
from installer_codes import install_codes
from installer_build_progress import install_build_progress
from installer_save_directory import install_save_directory
from installer_fps import install_fps_setting
from sbi_support import catalogue as sbi_catalogue, identify_disc, resolve_sbi
from installer_theme import install_game_theme

BASE = Path(__file__).resolve().parent
configure_portable_tools(BASE)
FRAMEWORK = BASE / 'psxrecomp'
TOOLCHAIN = BASE / 'toolchain'
PROJECTS = BASE / 'projets'
DEFAULT_OUTPUT = BASE / 'Jeux en un fichier'
DISC_FORMAT_HELP = {
    'PS1': 'PS1 : .cue, .bin ou .iso\nPlusieurs pistes : choisis le .cue et garde tous les .bin à côté.\nArchive .7z : extrais-la d’abord · .chd non pris en charge.',
    'PS2': 'PS2 : une image .iso par jeu\nArchive .7z : extrais-la d’abord pour obtenir l’ISO.\nLe format .chd n’est pas pris en charge.',
    'NES': 'NES : une cartouche .nes par jeu.\nArchive .7z : extrais-la d’abord.\nCompatibilité et réglages à vérifier selon le jeu.',
    'SNES': 'Super Nintendo : une cartouche .sfc ou .smc par jeu.\nArchive .7z : extrais-la d’abord.\nCompatibilité et réglages à vérifier selon le jeu.',
    'N64': 'Nintendo 64 : fichiers .z64, .v64 ou .n64.\nMoteur hybride expérimental : la compatibilité dépend du jeu.',
}
SETTINGS_FILE = Path(os.environ.get('LOCALAPPDATA') or (Path.home() / 'AppData' / 'Local')) / 'PhantomStation' / 'compilateur.json'
NOTICE_FILE = SETTINGS_FILE.parent / 'utilisation-responsable.json'
NOTICE_VERSION = 1
NOTICE_TEXT = (
    'PhantomStation doit être utilisé dans le respect des lois et des droits des auteurs. '
    'Utilise uniquement des jeux, BIOS et autres fichiers que tu es autorisé à utiliser.\n\n'
    'Chaque utilisateur est responsable de ses propres fichiers et de l’usage qu’il fait du logiciel. '
    'Toute utilisation illégale est interdite.\n\n'
    'Dans les limites prévues par la loi, le créateur décline toute responsabilité pour '
    'une utilisation illégale du logiciel par un utilisateur.'
)
SETUP = FRAMEWORK / 'tools' / 'new_project_layout' / 'setup_project.ps1'
# The desktop shortcut uses pythonw.exe for the interface. Build tools need
# the console interpreter so their output and errors reach the build log.
_running_python = Path(sys.executable)
_console_python = _running_python.with_name('python.exe')
PYTHON = _console_python if _running_python.name.lower() == 'pythonw.exe' and _console_python.is_file() else _running_python


def accept_responsible_use(root):
    """Ask once per Windows user and remember only an explicit acceptance."""
    try:
        if json.loads(NOTICE_FILE.read_text(encoding='utf-8')).get('version') == NOTICE_VERSION:
            return True
    except (OSError, ValueError, AttributeError):
        pass

    root.withdraw()
    dialog = tk.Toplevel(root)
    dialog.title('PhantomStation — Utilisation responsable')
    dialog.configure(bg=BG)
    width, height = 660, 455
    x = max(0, (dialog.winfo_screenwidth() - width) // 2)
    y = max(0, (dialog.winfo_screenheight() - height) // 2)
    dialog.geometry(f'{width}x{height}+{x}+{y}')
    dialog.resizable(False, False)
    set_window_icon(dialog)
    outer = ctk.CTkFrame(dialog, fg_color=BG, corner_radius=0)
    outer.pack(fill='both', expand=True, padx=24, pady=20)
    ctk.CTkLabel(outer, text='PHANTOMSTATION  /  DEV BY NOVALYS', text_color='#71deea',
                 font=('Segoe UI', 10, 'bold'), anchor='w').pack(fill='x')
    ctk.CTkLabel(outer, text='Utilisation responsable', text_color=TEXT,
                 font=('Segoe UI', 23, 'bold'), anchor='w').pack(fill='x', pady=(3, 12))
    card = panel(outer)
    card.pack(fill='both', expand=True)
    ctk.CTkLabel(card, text=NOTICE_TEXT, text_color=TEXT, font=('Segoe UI', 12),
                 anchor='nw', justify='left', wraplength=550).pack(fill='both', expand=True, padx=22, pady=20)
    actions = ctk.CTkFrame(outer, fg_color='transparent')
    actions.pack(fill='x', pady=(14, 0))
    accepted = [False]

    def accept():
        temporary = None
        try:
            NOTICE_FILE.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=NOTICE_FILE.parent, delete=False) as stream:
                temporary = Path(stream.name)
                json.dump({'version': NOTICE_VERSION}, stream)
            os.replace(temporary, NOTICE_FILE)
        except OSError:
            messagebox.showerror('Enregistrement impossible',
                                 'Impossible de mémoriser ton choix. Vérifie les droits du dossier PhantomStation.', parent=dialog)
            return
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        accepted[0] = True
        dialog.destroy()

    button(actions, 'Quitter', dialog.destroy, width=130).pack(side='left')
    button(actions, 'J’accepte et je continue', accept, primary=True, width=210).pack(side='right')
    dialog.protocol('WM_DELETE_WINDOW', dialog.destroy)
    dialog.lift()
    dialog.focus_force()
    root.wait_window(dialog)
    if accepted[0]:
        root.deiconify()
    return accepted[0]


def build_environment():
    env = os.environ.copy()
    env['PSXRECOMP_TOOLCHAIN_DIR'] = str(TOOLCHAIN)
    env['PSXRECOMP_PYTHON'] = str(PYTHON)
    env['CMAKE_BUILD_PARALLEL_LEVEL'] = '2'
    env['PATH'] = str(TOOLCHAIN / 'bin') + os.pathsep + str(PYTHON.parent) + os.pathsep + env.get('PATH', '')
    git_bash = Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / 'Git' / 'bin'
    if (git_bash / 'bash.exe').exists(): env['PATH'] += os.pathsep + str(git_bash)
    return env


def create_game_launcher(project):
    """Add a per-game entry point, without replacing unrelated user files."""
    target = project / 'build-release' / 'Jouer avec PhantomStation.cmd'
    marker = 'REM PhantomStation game launcher'
    if target.exists() and marker not in target.read_text(encoding='utf-8', errors='replace'):
        return None
    python = PYTHON.with_name('pythonw.exe') if PYTHON.with_name('pythonw.exe').exists() else PYTHON
    def cmd_literal(path): return str(path).replace('%', '%%')
    content = f'@echo off\r\n{marker}\r\nstart "" "{cmd_literal(python)}" "{cmd_literal(BASE / "lanceur.py")}" --jouer "%~dp0."\r\n'
    target.write_text(content, encoding='utf-8', newline='')
    return target


def launch_game(exe, parent):
    try:
        env = os.environ.copy()
        env['PHANTOM_SPLASH_EXE'] = str(BASE / 'unique_stub.exe')
        try:
            env['PHANTOM_GAME_TITLE'] = str(tomllib.loads((exe.parent / 'game.toml').read_text(encoding='utf-8-sig')).get('game', {}).get('name') or exe.stem.replace('_', ' '))
        except (OSError, ValueError):
            env['PHANTOM_GAME_TITLE'] = exe.stem.replace('_', ' ')
        subprocess.Popen([str(exe)], cwd=str(exe.parent), env=env)
    except OSError as exc:
        messagebox.showerror('Impossible de lancer le jeu', str(exc), parent=parent)


def runtime_exe(project):
    build = project / 'build-release'
    ninja = build / 'build.ninja'
    if not ninja.is_file(): return None
    match = re.search(r'^build psx-runtime: phony ([^\r\n]+\.exe)\s*$', ninja.read_text(encoding='utf-8', errors='replace'), re.MULTILINE)
    if not match: return None
    name = match.group(1).replace('$ ', ' ')
    if Path(name).name != name: return None
    exe = build / name
    return exe if exe.is_file() else None


def set_resolution(config, resolution):
    section = re.search(r'(?ms)^\[video\][ \t]*\r?\n(.*?)(?=^\[|\Z)', config)
    value = 'internal_resolution = "' + resolution + '"'
    if not section:
        return config.rstrip() + '\n\n[video]\n' + value + '\n'
    body = section.group(1)
    if re.search(r'(?m)^internal_resolution\s*=', body):
        body = re.sub(r'(?m)^internal_resolution\s*=.*$', value, body, count=1)
    else:
        body = value + '\n' + body
    return config[:section.start(1)] + body + config[section.end(1):]


def game_name_from_file(filename):
    """Use the first disc's filename, without its extension or disc number."""
    name = Path(filename).stem.strip()
    cleaned = re.sub(r'\s*[\[(](?:disc|disk|disque|cd|track|piste)\s*[-_. ]*\d+[\])]\s*$', '', name, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"^(Tony\s+Hawk['’]s)\s+PS\s+(\d+)$", r'\1 \2', cleaned, flags=re.IGNORECASE)
    return cleaned or name


def disc_selection_key(filename):
    """Treat a BIN and its same-named CUE as the same selected disc."""
    path = Path(filename).expanduser()
    cue = path.with_suffix('.cue')
    if path.suffix.lower() == '.bin' and cue.is_file():
        path = cue
    try:
        path = path.resolve()
    except OSError:
        path = path.absolute()
    return os.path.normcase(str(path))


def detect_iso_platform(filename):
    """Read the small ISO-9660 root and SYSTEM.CNF; never load the whole disc."""
    path = Path(filename)
    if path.suffix.lower() != '.iso':
        return None
    try:
        with path.open('rb') as stream:
            size = path.stat().st_size
            stream.seek(16 * 2048)
            pvd = stream.read(2048)
            if len(pvd) != 2048 or pvd[:6] != b'\x01CD001':
                return None
            extent = struct.unpack_from('<I', pvd, 158)[0]
            length = struct.unpack_from('<I', pvd, 166)[0]
            if not 34 <= length <= 4 * 1024 * 1024 or extent * 2048 + length > size:
                return None
            stream.seek(extent * 2048)
            data = stream.read(length)
            offset = 0
            while offset < len(data):
                record_len = data[offset]
                if not record_len:
                    offset = (offset // 2048 + 1) * 2048
                    continue
                if record_len < 34 or offset + record_len > len(data):
                    return None
                record = data[offset:offset + record_len]
                name_len = record[32]
                if 33 + name_len > record_len:
                    return None
                name = record[33:33 + name_len].split(b';', 1)[0].upper()
                offset += record_len
                if name != b'SYSTEM.CNF' or record[25] & 2:
                    continue
                start = struct.unpack_from('<I', record, 2)[0] * 2048
                count = struct.unpack_from('<I', record, 10)[0]
                if count > 65536 or start + count > size:
                    return None
                stream.seek(start)
                text = stream.read(count).decode('ascii', errors='replace')
                if re.search(r'(?im)^\s*BOOT2\s*=', text):
                    return 'PS2'
                if re.search(r'(?im)^\s*BOOT\s*=', text):
                    return 'PS1'
    except (OSError, ValueError, struct.error):
        return None
    return None


def load_output_directory():
    try:
        value = json.loads(SETTINGS_FILE.read_text(encoding='utf-8')).get('dossier_jeux')
        if isinstance(value, str) and value and Path(value).is_absolute():
            return Path(value)
    except (OSError, ValueError, AttributeError):
        pass
    return DEFAULT_OUTPUT


def save_output_directory(directory):
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=SETTINGS_FILE.parent, delete=False) as f:
            tmp = Path(f.name)
            json.dump({'dossier_jeux': str(directory)}, f, ensure_ascii=False, indent=2)
        os.replace(tmp, SETTINGS_FILE)
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


def create_desktop_shortcut(game, desktop=None):
    """Create a Windows shortcut without replacing an existing desktop item."""
    game = Path(game).resolve()
    if not game.is_file():
        raise OSError('Le fichier du jeu est introuvable : ' + str(game))
    env = os.environ.copy()
    env['PHANTOM_SHORTCUT_TARGET'] = str(game)
    env.pop('PHANTOM_SHORTCUT_DESKTOP', None)
    if desktop is not None:
        env['PHANTOM_SHORTCUT_DESKTOP'] = str(Path(desktop).resolve())
    script = r'''
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$target = $env:PHANTOM_SHORTCUT_TARGET
$desktop = $env:PHANTOM_SHORTCUT_DESKTOP
if (-not $desktop) { $desktop = [Environment]::GetFolderPath('DesktopDirectory') }
if (-not $desktop -or -not [IO.Directory]::Exists($desktop)) { throw 'Dossier du bureau introuvable.' }
if (-not [IO.File]::Exists($target)) { throw 'Fichier du jeu introuvable.' }
$name = [IO.Path]::GetFileNameWithoutExtension($target)
$index = 1
while ($true) {
    $suffix = if ($index -eq 1) { '' } else { ' (' + $index + ')' }
    $link = [IO.Path]::Combine($desktop, $name + $suffix + '.lnk')
    try {
        $file = [IO.File]::Open($link, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
        $file.Close()
        break
    } catch [IO.IOException] {
        if ([IO.File]::Exists($link)) { $index++; continue }
        throw
    }
}
try {
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($link)
    $shortcut.TargetPath = $target
    $shortcut.WorkingDirectory = [IO.Path]::GetDirectoryName($target)
    $shortcut.IconLocation = $target + ',0'
    $shortcut.Save()
} catch {
    if ([IO.File]::Exists($link) -and (Get-Item -LiteralPath $link).Length -eq 0) { [IO.File]::Delete($link) }
    throw
}
[Console]::WriteLine($link)
'''
    try:
        result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                                env=env, capture_output=True, text=True, encoding='utf-8', errors='replace',
                                creationflags=subprocess.CREATE_NO_WINDOW, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise OSError('Impossible de créer le raccourci : ' + str(exc)) from exc
    if result.returncode:
        raise OSError((result.stderr or result.stdout or 'Impossible de créer le raccourci.').strip())
    return Path(result.stdout.strip())


def original_image_snapshot(selected, cues, descriptors):
    """Record the images actually used, excluding our generated CUE files."""
    paths = set()
    for entry in selected:
        original = Path(entry).absolute()
        if original != original.resolve(strict=True):
            return []  # Do not follow a selected link to a different folder.
        paths.add(original)
    selected_folders = {path.parent for path in paths}
    descriptors = Path(descriptors).resolve()
    for cue in cues:
        cue = Path(cue).resolve(strict=True)
        if not cue.is_relative_to(descriptors):
            paths.add(cue)
        _, tracks = _cue_files(cue)
        paths.update(path.resolve(strict=True) for path, _ in tracks)
    if any(path.parent not in selected_folders for path in paths):
        return []  # Tracks outside the selected folders require manual review.
    snapshot = []
    for path in sorted(paths, key=lambda p: str(p).casefold()):
        stat = path.stat()
        snapshot.append((str(path), stat.st_size, stat.st_mtime_ns, stat.st_ino))
    return snapshot


def image_folder_contents(folder, records, protected):
    """List a reviewable folder only when its used images are unchanged."""
    try:
        folder = Path(folder)
        canonical = folder.resolve(strict=True)
        home = Path.home().resolve()
        if folder != canonical or canonical == Path(canonical.anchor) or canonical.parent == Path(canonical.anchor):
            return None
        if canonical == home or canonical.parent == home:
            return None
        if folder.is_symlink() or getattr(folder.lstat(), 'st_file_attributes', 0) & 0x400:
            return None
        for ref in protected:
            ref = Path(ref).resolve()
            if ref == canonical or ref.is_relative_to(canonical):
                return None
        expected = {Path(record[0]) for record in records}
        if not expected or any(path.parent != canonical for path in expected):
            return None
        for path, size, modified, inode in records:
            item = Path(path)
            stat = item.lstat()
            if (item.is_symlink() or getattr(stat, 'st_file_attributes', 0) & 0x400 or not item.is_file()
                or (stat.st_size, stat.st_mtime_ns, stat.st_ino) != (size, modified, inode)):
                return None
        contents = []
        for base, dirs, files in os.walk(canonical, followlinks=False, onerror=lambda e: (_ for _ in ()).throw(e)):
            for name in dirs + files:
                item = Path(base) / name
                stat = item.lstat()
                if item.is_symlink() or getattr(stat, 'st_file_attributes', 0) & 0x400:
                    return None
                if item.is_file() and (item.suffix.lower() in {'.mcd', '.mcr', '.srm', '.sav', '.state'}
                                       or item.name.lower() in {'settings.toml', 'state.toml', 'phantom-codes.tsv'}):
                    return None
                contents.append((str(item.relative_to(canonical)), item.is_dir(), stat.st_size, stat.st_mtime_ns, stat.st_ino))
                if len(contents) > 30:
                    return None  # Keep the complete list readable in the confirmation.
        return sorted(contents, key=lambda item: item[0].casefold())
    except (OSError, ValueError):
        return None


def recycle_image_folder(folder):
    """Move a confirmed folder to the Windows Recycle Bin."""
    folder = Path(folder)
    if folder != folder.resolve(strict=True):
        raise OSError('Le chemin du dossier a changé.')
    env = os.environ.copy()
    env['PHANTOM_RECYCLE_IMAGE_FOLDER'] = str(folder)
    script = r'''
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName Microsoft.VisualBasic
[Microsoft.VisualBasic.FileIO.FileSystem]::DeleteDirectory(
    $env:PHANTOM_RECYCLE_IMAGE_FOLDER,
    [Microsoft.VisualBasic.FileIO.UIOption]::OnlyErrorDialogs,
    [Microsoft.VisualBasic.FileIO.RecycleOption]::SendToRecycleBin)
'''
    try:
        result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                                env=env, capture_output=True, text=True, encoding='utf-8', errors='replace',
                                creationflags=subprocess.CREATE_NO_WINDOW, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise OSError('Impossible de déplacer le dossier dans la Corbeille : ' + str(exc)) from exc
    if result.returncode or folder.exists():
        raise OSError((result.stderr or result.stdout or 'Le dossier n’a pas été déplacé.').strip())


def failed_project_snapshot(project, token, protected, require_marker=True):
    """Identify only a project created by this attempt, without following links."""
    project = Path(project)
    if project.parent.resolve() != PROJECTS.resolve() or project != project.resolve(strict=True):
        raise ValueError('Chemin du projet non sûr.')
    marker = project / '.phantom-build-token'
    if require_marker and (marker.is_symlink() or marker.read_text(encoding='utf-8') != token):
        raise ValueError('Projet non identifié comme appartenant à cette tentative.')
    for ref in protected:
        path = Path(ref).resolve()
        if path == project or path.is_relative_to(project):
            raise ValueError('Le projet contient un fichier ou dossier utilisé par le joueur.')
    snapshot = []
    sensitive = {'.bin', '.cue', '.iso', '.img', '.chd', '.sav', '.mcd', '.mcr', '.srm', '.state'}
    for base, dirs, files in os.walk(project, followlinks=False, onerror=lambda e: (_ for _ in ()).throw(e)):
        for name in dirs + files:
            item = Path(base) / name
            st = item.lstat()
            if item.is_symlink() or getattr(st, 'st_file_attributes', 0) & 0x400:
                raise ValueError('Le projet contient un lien ou une jonction.')
            relative = item.relative_to(project)
            if item.is_file() and (item.suffix.lower() in sensitive or name.lower() in {'settings.toml', 'state.toml', 'phantom-codes.tsv', 'phantom-codes-suggestions.tsv'}):
                abi = name in {'CMakeDetermineCompilerABI_C.bin', 'CMakeDetermineCompilerABI_CXX.bin'} and 'CMakeFiles' in relative.parts
                source = BASE / relative
                if not abi and not (relative.parts[0] == 'psxrecomp' and source.is_file() and filecmp.cmp(item, source, shallow=False)):
                    raise ValueError('Fichier personnel ou image détecté : ' + str(relative))
            snapshot.append((str(relative), st.st_size, st.st_mtime_ns, st.st_ino))
    return sorted(snapshot)


def cleanup_failed_attempt(attempt):
    messages = []
    partial = attempt.get('partial')
    if partial:
        path, identity = partial
        try:
            st = path.lstat()
            if (path.parent.resolve() != attempt['output'].resolve() or attempt['token'] not in path.name or path.is_symlink()
                or getattr(st, 'st_file_attributes', 0) & 0x400 or (st.st_size, st.st_mtime_ns, st.st_ino) != identity):
                raise ValueError('Fichier temporaire modifié ou non identifié.')
            path.unlink()
            messages.append('Fichier incomplet supprimé.')
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as exc:
            messages.append('Fichier incomplet conservé : ' + str(exc))
    if attempt.get('snapshot') is not None:
        try:
            project = attempt['project']
            if failed_project_snapshot(project, attempt['token'], attempt['protected']) != attempt['snapshot']:
                raise ValueError('Le contenu du projet a changé depuis l’erreur.')
            recycle_image_folder(project)
            messages.append('Projet incomplet déplacé dans la Corbeille.')
        except (OSError, ValueError) as exc:
            messages.append('Projet incomplet conservé : ' + str(exc))
    elif attempt.get('reason'):
        messages.append('Projet incomplet conservé : ' + attempt['reason'])
    messages.extend(cleanup_descriptors(attempt.get('descriptors', [])))
    return messages


def cleanup_descriptors(records):
    messages = []
    folders = set()
    for path, identity in records:
        try:
            st = path.lstat()
            if (path.is_symlink() or getattr(st, 'st_file_attributes', 0) & 0x400 or
                (st.st_size, st.st_mtime_ns, st.st_ino) != identity):
                raise ValueError('fichier modifié')
            path.unlink()
            folders.add(path.parent)
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as exc:
            messages.append('Descripteur conservé : ' + str(exc))
    for folder in folders:
        try: folder.rmdir()  # Only removes our directory if it is still empty.
        except OSError: pass
    return messages


class App:
    def __init__(self, root):
        self.root = root
        root.title('PhantomStation — Recompilation de jeux rétro')
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        width, height = min(1220, max(980, sw - 60)), min(860, max(700, sh - 90))
        root.geometry(f'{width}x{height}+{max(0, (sw-width)//2)}+{max(0, (sh-height)//2)}')
        root.minsize(980, 700)
        root.configure(bg=BG)
        set_window_icon(root, default=True)
        # The compilation screen's blue and red progress bars use this theme.
        ttk.Style(root).theme_use('clam')
        self.events = queue.Queue()
        self.game_view = GameInCompiler(root)
        self.progress_window = None
        self.finished_game = None
        self.finished_images = ([], None)
        self.finished_missing_sbi = []
        self.last_failure = None
        self.attempt_log_start = '1.0'
        self.attempt_report_context = ('', [])
        self.failed_attempt = None
        self.discs = []
        self.project = None
        self.icon_path = None
        self.background_path = None
        self.output_dir = load_output_directory()
        self.busy = False
        self.last_ps2_output = None
        self.last_output_by_console = {}
        self.console_active = 'PS1'
        self.console_drafts = {}
        self.players_by_console = {'PS1': '1', 'PS2': '2', 'NES': '2', 'SNES': '2', 'N64': '4'}
        self.current_page = 'creation'

        hero = HeroBanner(root)
        hero.pack(fill='x')
        shell = ctk.CTkFrame(root, fg_color=BG, corner_radius=0)
        shell.pack(fill='both', expand=True, padx=18, pady=(14, 16))
        sidebar = panel(shell, width=190)
        sidebar.pack(side='left', fill='y', padx=(0, 14))
        sidebar.pack_propagate(False)
        ctk.CTkLabel(sidebar, text='TON ESPACE', text_color='#6ddbe9', font=('Segoe UI', 10, 'bold'), anchor='w').pack(fill='x', padx=18, pady=(21, 10))
        self.nav_buttons = {}
        for key, title in [('creation', '◈   Compilation'), ('library', '▦   Jeux'), ('guide', '?   Mode d’emploi')]:
            nav = button(sidebar, title, lambda k=key: self.select_navigation(k), width=152, anchor='w')
            nav.pack(fill='x', padx=12, pady=4)
            self.nav_buttons[key] = nav
        ctk.CTkLabel(sidebar, text='PLAYSTATION  /  NINTENDO', text_color='#6d829b', font=('Segoe UI', 9, 'bold'), anchor='w').pack(side='bottom', fill='x', padx=19, pady=(0, 20))

        self.content = ctk.CTkFrame(shell, fg_color='transparent')
        self.content.pack(side='left', fill='both', expand=True)
        self.pages = {}
        creation = ctk.CTkFrame(self.content, fg_color='transparent')
        self.library = LibraryView(self.content, lambda: self.output_dir, self.select_output_directory, self.open_compiled_game)
        guide = ctk.CTkFrame(self.content, fg_color='transparent')
        self.pages.update(creation=creation, library=self.library, guide=guide)

        title = ctk.CTkFrame(creation, fg_color='transparent')
        title.pack(fill='x', pady=(0, 10))
        self.console = tk.StringVar(value='PS1')
        console_area = ctk.CTkFrame(title, fg_color='transparent')
        console_area.pack(side='right', anchor='ne', padx=(10, 2))
        ctk.CTkLabel(console_area, text='CONSOLE', text_color='#6ddbe9', font=('Segoe UI', 10, 'bold'), anchor='w').pack(fill='x')
        self.console_switch = ctk.CTkSegmentedButton(console_area, values=['PS1', 'PS2', 'NES', 'SNES', 'N64'], variable=self.console,
            command=self.change_console, selected_color=ACCENT, selected_hover_color=ACCENT_HOVER,
            unselected_color='#20334b', unselected_hover_color='#31516b', width=260)
        self.console_switch.pack(pady=(3, 0))
        ctk.CTkLabel(title, text='ATELIER PHANTOMSTATION', text_color='#6ddbe9', font=('Segoe UI', 10, 'bold'), anchor='w', height=17).pack(fill='x')
        ctk.CTkLabel(title, text='Créer un jeu', text_color=TEXT, font=('Segoe UI', 24, 'bold'), anchor='w', height=30).pack(fill='x')
        ctk.CTkLabel(title, text='Choisis tes disques et prépare ton jeu en quelques étapes.', text_color=MUTED, font=('Segoe UI', 11), anchor='w', height=19).pack(fill='x')

        panels = ctk.CTkFrame(creation, fg_color='transparent')
        panels.pack(fill='x', pady=(0, 10))
        panels.grid_columnconfigure(0, weight=1, uniform='setup')
        panels.grid_columnconfigure(1, weight=1, uniform='setup')
        left = panel(panels)
        right = panel(panels)
        left.grid(row=0, column=0, sticky='nsew', padx=(0, 6))
        right.grid(row=0, column=1, sticky='nsew', padx=(6, 0))
        l = ctk.CTkFrame(left, fg_color='transparent'); l.pack(fill='both', expand=True, padx=17, pady=10)
        r = ctk.CTkFrame(right, fg_color='transparent'); r.pack(fill='both', expand=True, padx=17, pady=10)
        ctk.CTkLabel(l, text='01   TON JEU', text_color='#73ddeb', font=('Segoe UI', 10, 'bold'), anchor='w', height=19).pack(fill='x')
        ctk.CTkLabel(l, text='Nom du jeu', text_color=TEXT, font=('Segoe UI', 12, 'bold'), anchor='w', height=20).pack(fill='x', pady=(7, 2))
        self.name = tk.StringVar(value=''); self.suggested_name = ''
        ctk.CTkEntry(l, textvariable=self.name, height=37, corner_radius=10, fg_color='#0b1727', border_color=BORDER, text_color=TEXT).pack(fill='x', pady=(0, 8))
        self.choose = button(l, '＋  Ajouter un ou plusieurs disques', self.select)
        self.choose.pack(fill='x')
        self.disc_text = tk.StringVar(value='Aucun disque sélectionné.')
        ctk.CTkLabel(l, textvariable=self.disc_text, text_color=MUTED, font=('Segoe UI', 10), anchor='w', justify='left', wraplength=350, height=19).pack(fill='x', pady=(4, 4))
        disc_actions = ctk.CTkFrame(l, fg_color='transparent'); disc_actions.pack(fill='x', pady=(0, 3))
        self.disc_remove = button(disc_actions, 'Retirer le dernier', self.remove_last_disc, width=130)
        self.disc_remove.pack(side='left', fill='x', expand=True)
        self.disc_clear = button(disc_actions, 'Tout retirer', self.clear_discs, width=100)
        self.disc_clear.pack(side='left', fill='x', expand=True, padx=(7, 0))
        self.disc_remove.configure(state='disabled'); self.disc_clear.configure(state='disabled')
        self.disc_help = tk.StringVar(value=DISC_FORMAT_HELP['PS1'])
        ctk.CTkLabel(l, textvariable=self.disc_help, text_color=MUTED, font=('Segoe UI', 10), anchor='w', justify='left', wraplength=350, height=19).pack(fill='x', pady=(5, 0))

        ctk.CTkLabel(r, text='02   OPTIONS DE CRÉATION', text_color='#73ddeb', font=('Segoe UI', 10, 'bold'), anchor='w', height=19).pack(fill='x')
        options = ctk.CTkFrame(r, fg_color='transparent'); options.pack(fill='x', pady=(9, 7))
        self.players = tk.StringVar(value='1'); self.resolution = tk.StringVar(value='8x')
        ctk.CTkLabel(options, text='Joueurs', text_color=TEXT, font=('Segoe UI', 11)).pack(side='left')
        self.players_box = ctk.CTkComboBox(options, variable=self.players, values=['1','2','3','4'], state='readonly', width=68, height=34, corner_radius=10, fg_color=CARD, border_color=BORDER, button_color='#28536d'); self.players_box.pack(side='left', padx=(6, 12))
        ctk.CTkLabel(options, text='Résolution', text_color=TEXT, font=('Segoe UI', 11)).pack(side='left')
        self.resolution_box = ctk.CTkComboBox(options, variable=self.resolution, values=['1x','2x','4x','8x','auto'], state='readonly', width=85, height=34, corner_radius=10, fg_color=CARD, border_color=BORDER, button_color='#28536d'); self.resolution_box.pack(side='left', padx=(6, 0))
        self.phantom_bios = tk.BooleanVar(value=True)
        self.bios_box = ctk.CTkCheckBox(r, text='BIOS PhantomStation (expérimental)', variable=self.phantom_bios, text_color=TEXT, font=('Segoe UI', 11), corner_radius=5, fg_color=ACCENT, hover_color=ACCENT_HOVER); self.bios_box.pack(anchor='w', pady=(0, 7))
        self.options_hint = tk.StringVar(value='')
        ctk.CTkLabel(r, textvariable=self.options_hint, text_color=MUTED, font=('Segoe UI', 10), anchor='w', justify='left', wraplength=330).pack(fill='x', pady=(0, 4))
        destination = ctk.CTkFrame(r, fg_color='transparent'); destination.pack(fill='x')
        self.output_button = button(destination, 'Dossier des jeux', self.select_output_directory, width=140)
        self.output_button.pack(side='left', fill='x', expand=True)
        button(destination, 'Ouvrir', self.open_packages, width=70).pack(side='left', padx=(7, 0))
        self.output_text = tk.StringVar(value='Dossier : ' + str(self.output_dir))
        ctk.CTkLabel(r, textvariable=self.output_text, text_color=MUTED, font=('Segoe UI', 10), anchor='w', wraplength=350, justify='left', height=19).pack(fill='x', pady=(4, 4))
        artwork = ctk.CTkFrame(r, fg_color='transparent'); artwork.pack(fill='x')
        self.icon_button = button(artwork, 'Choisir la jaquette', self.select_icon, width=120)
        self.icon_button.pack(side='left', fill='x', expand=True)
        self.background_button = button(artwork, 'Choisir le fond', self.select_background, width=110)
        self.background_button.pack(side='left', fill='x', expand=True, padx=(7, 0))
        self.icon_text = tk.StringVar(value='Jaquette automatique si disponible')
        ctk.CTkLabel(r, textvariable=self.icon_text, text_color=MUTED, font=('Segoe UI', 10), anchor='w', wraplength=350, height=19).pack(fill='x', pady=(4, 0))

        actions = ctk.CTkFrame(creation, fg_color='transparent'); actions.pack(fill='x', pady=(0, 10))
        self.start = button(actions, '✦  CRÉER LE PROGRAMME', self.build, primary=True, width=206)
        self.start.pack(side='left', padx=(0, 8))
        self.open_dir = button(actions, 'Ouvrir le projet', self.open_project, width=120); self.open_dir.pack(side='left', padx=(0, 7))
        self.project_button = button(actions, 'Choisir un projet', self.select_project, width=120); self.project_button.pack(side='left', padx=(0, 7))
        self.launch_button = button(actions, 'Lancer le programme', self.launch, width=145); self.launch_button.pack(side='left')

        footer = ctk.CTkFrame(creation, fg_color='transparent'); footer.pack(side='bottom', fill='x', pady=(8, 0))
        self.status = tk.StringVar(value='En attente')
        ctk.CTkLabel(footer, textvariable=self.status, text_color='#94c2cc', font=('Segoe UI', 10), anchor='w').pack(side='left', fill='x', expand=True)
        log_card = panel(creation); log_card.pack(fill='both', expand=True)
        log_header = ctk.CTkFrame(log_card, fg_color='transparent'); log_header.pack(fill='x', padx=16, pady=(10, 4))
        ctk.CTkLabel(log_header, text='JOURNAL DE COMPILATION', text_color='#73ddeb', font=('Segoe UI', 10, 'bold'), anchor='w').pack(side='left')
        ctk.CTkLabel(log_header, text='Suivi et détails', text_color=MUTED, font=('Segoe UI', 10)).pack(side='right')
        self.report_shortcut = button(log_header, 'Envoyer le rapport', self.show_error_report, width=150)
        self.log = tk.Text(log_card, height=5, wrap='word', state='disabled', bg='#0c192a', fg='#d8eaff', insertbackground='white', relief='flat', highlightthickness=0, padx=10, pady=8, font=('Consolas', 9))
        self.log.pack(fill='both', expand=True, padx=15, pady=(0, 12))
        ctk.CTkLabel(guide, text='BIENVENUE', text_color='#6ddbe9', font=('Segoe UI', 10, 'bold'), anchor='w').pack(fill='x')
        ctk.CTkLabel(guide, text='Mode d’emploi', text_color=TEXT, font=('Segoe UI', 25, 'bold'), anchor='w').pack(fill='x', pady=(0, 12))
        guide_card = panel(guide); guide_card.pack(fill='both', expand=True)
        guide_body = tk.Text(guide_card, wrap='word', bg=SURFACE, fg='#dae8f7', relief='flat', highlightthickness=0, font=('Segoe UI', 11), padx=20, pady=18)
        guide_scroll = ctk.CTkScrollbar(guide_card, command=guide_body.yview)
        guide_body.configure(yscrollcommand=guide_scroll.set)
        guide_scroll.pack(side='right', fill='y', padx=(0, 6), pady=12)
        guide_body.pack(side='left', fill='both', expand=True)
        guide_body.insert('1.0', '''FORMATS DES JEUX

PS1 : choisis un fichier .cue avec tous ses .bin dans le même dossier, ou une image .iso. Un .bin seul peut aussi être sélectionné ; s’il y a plusieurs pistes .bin, choisis le .cue qui les décrit toutes. Pour plusieurs CD, ajoute un .cue ou une image par disque, dans l’ordre.

PS2 : choisis une image .iso par jeu. Les fichiers .bin/.cue ne sont pas acceptés pour la PS2.

NES : sélectionne une cartouche .nes. Super Nintendo : sélectionne une cartouche .sfc ou .smc. Le moteur Nintendo est expérimental : teste chaque jeu, sa région et ses sauvegardes. Les cartouches à matériel spécial ne sont pas toutes prises en charge.

N64 : sélectionne une cartouche .z64, .v64 ou .n64. GoldenEye 007 Europe utilise un portage natif adapté. Les autres cartouches utilisent un moteur commun expérimental : certaines parties restent interprétées et certains jeux peuvent ne pas fonctionner. Les réglages et commandes s'ouvrent avec F10 pendant le jeu.

Pour toutes les consoles : une archive .7z doit d’abord être extraite, par exemple avec 7-Zip ; sélectionne ensuite le fichier de jeu extrait. Le format .chd n’est pas pris en charge par le compilateur.

Dans les jeux NES et Super Nintendo, F2 ouvre les réglages et les commandes, F3 change le filtre d’image, F8 sauvegarde rapidement et F9 recharge. Les codes et les autres options ne sont pas encore identiques à ceux des jeux PlayStation.

Choisis d’abord PS1 ou PS2. En PS2, sélectionne une seule image ISO : le moteur est expérimental et la compatibilité dépend du jeu. Le même dossier de sortie est utilisé. Dans le lanceur PS2, « Réglages » regroupe la vidéo, l’audio, le clavier et les sauvegardes ; « Codes PS2 » permet de cocher les codes disponibles ou d’ajouter des écritures simples. Les codes sont à vérifier selon le jeu ; les formats conditionnels et chiffrés ne sont pas pris en charge. Deux ports de manette sont disponibles. F1 affiche les commandes, F5 et F8 servent aux sauvegardes rapides. Les options propres au moteur PS1 ne sont pas automatiquement disponibles en PS2.

1. Sélectionne le ou les disques : le nom du jeu est rempli automatiquement à partir du premier fichier. Tu peux le modifier.

2. Clique sur « Ajouter un ou plusieurs disques ». Pour un jeu en plusieurs CD, ajoute les fichiers CUE dans l’ordre : CD 1, puis CD 2. Tu peux les sélectionner un par un, même dans des dossiers différents. Utilise « Retirer le dernier » ou « Tout retirer » si nécessaire. Garde chaque fichier BIN à côté de son CUE. Si un disque contient plusieurs fichiers Track BIN et un seul CUE, sélectionne uniquement ce CUE : il décrit toutes les pistes. Un ISO peut aussi être sélectionné.

3. Choisis le nombre de joueurs et la résolution. La valeur 8x est proposée pour une image plus nette ; si le jeu ralentit, essaie 4x ou 2x. Le nombre de joueurs dépend du jeu et des périphériques compatibles.

4. Choisis une jaquette ou laisse PhantomStation la rechercher automatiquement. Tu peux aussi choisir un fond pour le lanceur du jeu.

Si un disque est répertorié comme nécessitant un SBI, PhantomStation le cherche automatiquement. Si la recherche échoue, le jeu est quand même créé et un avertissement apparaît. Dans son lanceur, sélectionne le disque concerné et utilise « Ajouter le fichier SBI » avec le fichier correspondant. Le bouton n’apparaît que lorsque le fichier est manquant. Les fichiers ajoutés sont stockés dans les données extraites du jeu, sans modifier les images d’origine. Selon la version, le format peut être contrôlé sans que la correspondance exacte puisse être garantie.

5. Choisis le dossier où enregistrer tes jeux, puis clique sur « Créer le programme ». L’EXE unique est créé automatiquement. La première compilation peut prendre du temps et nécessiter Internet. Suis sa progression dans le journal.

6. Une fois la création terminée, clique sur « Terminé » : tu peux choisir de créer un raccourci sur le bureau et de déplacer les dossiers d’images d’origine dans la Corbeille. Le contenu des dossiers est affiché avant confirmation. Sinon, clique sur « Ouvrir le dossier des jeux », puis ouvre le fichier EXE créé. Dans le lanceur du jeu, règle si besoin les manettes et la vidéo, puis clique sur « Jouer ». L’introduction apparaît dans la fenêtre du jeu.

L’onglet « Bibliothèque » retrouve les jeux dans le dossier choisi et permet d’en ajouter depuis un autre dossier. Pour chaque jeu, « Sauvegardes » ouvre le dossier des parties et permet de créer ou restaurer une copie de secours. Une copie est créée avant chaque lancement depuis la bibliothèque lorsque des sauvegardes existent. Ferme le jeu avant de copier ou restaurer ses sauvegardes. Les emplacements personnalisés choisis dans le lanceur du jeu ne sont pas suivis automatiquement.

Une manette compatible détectée est proposée automatiquement au joueur 1. Tu peux choisir une autre manette ou le clavier et modifier les touches ; les autres joueurs gardent leurs propres affectations. PhantomStation inclut des profils de manettes, mais la manette doit aussi être reconnue par Windows.

Pendant une partie à plusieurs disques, appuie sur F4 quand le jeu demande le disque suivant : les disques fournis sont sélectionnés dans l’ordre. Tu peux aussi ouvrir le menu du jeu avec F10 ou Ctrl+L, choisir « Disque », puis « Insérer le disque et reprendre ». Le changement ne ferme pas le jeu ; il n’est pas automatique. La compatibilité de la transition dépend du jeu.

Dans le lanceur du jeu, ouvre « Réglages » pour ajuster la qualité de l’image avec les curseurs, activer le lissage, la correction 3D, la synchronisation verticale (VSync) et l’affichage des FPS. La touche F permet aussi d’activer ou désactiver les FPS en jeu. Les options supplémentaires restent accessibles dans « Mods ». Leur effet et leur compatibilité dépendent du jeu : essaie-les une par une et désactive celles qui causent un problème.

Le bouton « Codes » du lanceur du jeu affiche les codes disponibles pour le numéro exact du disque. Ils sont désactivés au départ. Tu peux aussi ajouter un code manuellement ; certains formats ne sont pas pris en charge et la compatibilité dépend du jeu.

Recherche de pistes en jeu : appuie sur F10, ouvre « Recherche de codes » et choisis vie, munitions, carte ou arme équipée. Utilise les flèches ou Z/S pour naviguer, Entrée pour choisir et Échap pour revenir. Reprends la partie et fais changer cette valeur ; reviens dans le menu pour indiquer si elle a baissé, augmenté ou changé. Répète pour réduire les résultats. Quand il reste au maximum dix pistes, appuie plusieurs fois sur « Proposer les pistes restantes » pour les voir en jeu. Elles apparaîtront aussi dans « Codes » au prochain lancement, désactivées et non vérifiées. Si tu retrouves la même adresse pour la même recherche et le même disque après avoir relancé le jeu, le nombre de sessions augmente. Ce n'est pas une garantie : une piste d'arme ou de carte ne débloque pas automatiquement de contenu.

Après vérification de l’EXE, les principaux fichiers intermédiaires de compilation sont supprimés automatiquement pour économiser de la place. Les images d’origine, les sauvegardes, les réglages et la configuration du projet sont conservés.

Le fichier EXE contient les disques. Au premier lancement, ses données sont extraites dans le dossier local PhantomStation : elles occupent donc aussi de la place. Pour les nouveaux jeux, les sauvegardes sont dans Documents/PhantomStation/Sauvegardes, dans un dossier propre à chaque jeu. Les réglages restent dans le dossier local.

La recompilation et le BIOS PhantomStation sont expérimentaux : teste chaque jeu avant de déplacer tes fichiers d’origine. La langue dépend de ta version du jeu ; PhantomStation ne traduit pas les jeux.

UTILISATION RESPONSABLE

''' + NOTICE_TEXT + '''

MISES À JOUR

Au démarrage, PhantomStation recherche une nouvelle version signée. Si elle est disponible, il la télécharge, l’installe et redémarre. Les jeux, les projets, les sauvegardes et les réglages sont conservés. Sans connexion, le compilateur reste utilisable.''')
        guide_body.configure(state='disabled')

        self.show_page('creation')
        exemple = BASE / 'bios-phantom-test' / 'essai-jeu'
        if not (exemple / 'build-release' / 'game.toml').is_file(): exemple = PROJECTS / 'parasite-eve-2-fr-essai'
        if (exemple / 'game.toml').is_file():
            self.project = exemple
            self.status.set('Projet existant : Parasite Eve II France')
        root.after(100, self.poll)

    def select_navigation(self, key):
        self.show_page(key)

    def update_navigation(self):
        selected = self.current_page
        for name, nav in self.nav_buttons.items():
            nav.configure(fg_color='#1b799d' if name == selected else '#20334b')

    def show_page(self, key):
        self.current_page = key
        for page in self.pages.values(): page.pack_forget()
        self.pages[key].pack(fill='both', expand=True)
        self.update_navigation()
        if key == 'library': self.library.refresh()


    def change_console(self, value, update_status=True):
        if value != self.console_active:
            if self.console_active in self.players_by_console:
                self.players_by_console[self.console_active] = self.players.get()
            self.console_drafts[self.console_active] = (list(self.discs), self.name.get(), self.suggested_name,
                                                         self.icon_path, self.background_path)
            self.discs, name, self.suggested_name, self.icon_path, self.background_path = self.console_drafts.get(
                value, ([], '', '', None, None))
            self.discs = list(self.discs)
            self.name.set(name)
            self.console_active = value
            self.players.set(self.players_by_console.get(value, '—'))
            self.icon_text.set('Jaquette automatique si disponible' if not self.icon_path else 'Image choisie : ' + Path(self.icon_path).name)
            self.refresh_discs()
        is_ps1 = value == 'PS1'
        limit = 5 if value == 'SNES' else 4 if value in ('PS1', 'N64') else 2
        self.players_box.configure(values=[str(n) for n in range(1, limit + 1)])
        self.disc_help.set(DISC_FORMAT_HELP.get(value, DISC_FORMAT_HELP['PS1']))
        self.players_box.configure(state='readonly')
        self.resolution_box.configure(state='readonly' if is_ps1 else 'disabled')
        self.bios_box.configure(state='normal' if is_ps1 else 'disabled')
        self.options_hint.set('Nombre maximal de manettes ; le jeu choisit son mode multijoueur.' if value != 'PS1' else '')
        if update_status:
            self.status.set('PS1 sélectionnée.' if is_ps1 else value + ' expérimental : réglages disponibles selon le jeu.')
        self.update_navigation()

    def select(self):
        if self.busy: return
        platform = self.console.get()
        formats = {'PS1': ('Images PS1', '*.cue *.iso *.bin'), 'PS2': ('Image PS2', '*.iso'),
                   'NES': ('Cartouche NES', '*.nes'), 'SNES': ('Cartouche Super Nintendo', '*.sfc *.smc'),
                   'N64': ('Cartouche Nintendo 64', '*.z64 *.v64 *.n64')}
        options = {'title': 'Choisir le jeu', 'filetypes': [formats[platform], ('Tous les fichiers', '*.*')]}
        if self.discs:
            options['initialdir'] = str(Path(self.discs[-1]).parent)
        files = filedialog.askopenfilenames(**options)
        if not files: return
        if len(files) == 1 and not self.discs:
            detected = detect_iso_platform(files[0])
            if detected == 'PS2' and self.console.get() == 'PS1':
                if not self.switch_detected_ps2():
                    return
            elif detected == 'PS1' and self.console.get() == 'PS2':
                messagebox.showwarning('Jeu PS1 détecté', 'Cette image est un jeu PS1. Sélectionne PS1 pour l’ajouter.')
                return
        known = {disc_selection_key(path) for path in self.discs}
        new = []
        for path in files:
            key = disc_selection_key(path)
            if key not in known:
                new.append(path)
                known.add(key)
        if not new:
            self.status.set('Ces disques sont déjà dans la sélection.')
            return
        if not self.discs:
            previous_name = self.name.get().strip()
            new_suggestion = game_name_from_file(new[0])
            if not previous_name or previous_name == self.suggested_name:
                self.name.set(new_suggestion)
            self.suggested_name = new_suggestion
        self.discs.extend(new)
        self.refresh_discs()

    def switch_detected_ps2(self):
        draft = self.console_drafts.get('PS2')
        if draft and (draft[0] or draft[1] or draft[3] or draft[4]):
            messagebox.showwarning('Jeu PS2 détecté',
                'Cette image est un jeu PS2. Sélectionne PS2 : une sélection PS2 existe déjà et a été conservée.')
            return False
        # The current selection belongs to PS2; keep its title and artwork.
        self.players_by_console[self.console_active] = self.players.get()
        self.console.set('PS2')
        self.console_active = 'PS2'
        self.players.set(self.players_by_console['PS2'])
        self.change_console('PS2', update_status=False)
        self.status.set('Jeu PS2 détecté : console PS2 sélectionnée automatiquement.')
        return True

    def refresh_discs(self):
        label = 'Cartouche' if self.console.get() in ('NES', 'SNES', 'N64') else 'CD'
        self.disc_text.set('\n'.join(f'{label} {i + 1} : {Path(p).name}' for i, p in enumerate(self.discs))
                           if self.discs else 'Aucun disque sélectionné.')
        state = 'normal' if self.discs and not self.busy else 'disabled'
        self.disc_remove.configure(state=state)
        self.disc_clear.configure(state=state)

    def remove_last_disc(self):
        if self.busy or not self.discs: return
        self.discs.pop()
        if not self.discs:
            if self.name.get().strip() == self.suggested_name:
                self.name.set('')
            self.suggested_name = ''
        self.refresh_discs()

    def clear_discs(self):
        if self.busy: return
        self.discs.clear()
        if self.name.get().strip() == self.suggested_name:
            self.name.set('')
        self.suggested_name = ''
        self.refresh_discs()

    def select_icon(self):
        selected = filedialog.askopenfilename(title='Choisir une jaquette ou image', filetypes=[('Images', '*.png *.jpg *.jpeg *.webp *.ico'), ('Tous les fichiers', '*.*')])
        if selected:
            self.icon_path = selected
            self.icon_text.set('Image choisie : ' + Path(selected).name)

    def append(self, text):
        self.log.configure(state='normal')
        self.log.insert('end', text)
        self.log.see('end')
        self.log.configure(state='disabled')

    def poll(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == 'log': self.append(value)
                elif kind == 'phase':
                    if self.progress_window:
                        self.progress_window.set_phase(value)
                elif kind == 'progress':
                    if self.progress_window:
                        self.progress_window.set_progress(*value)
                elif kind == 'failed_attempt':
                    self.failed_attempt = value
                elif kind == 'missing_sbi':
                    self.finished_missing_sbi = value
                elif kind == 'done':
                    success, message, game, images, game_identity = value
                    if not success:
                        name, discs = self.attempt_report_context
                        journal = self.log.get(self.attempt_log_start, 'end-1c')
                        text = report_text(name, discs, journal)
                        self.last_failure = {'game': name, 'text': text, 'path': None}
                        try:
                            self.last_failure['path'] = save_report(text)
                        except OSError as exc:
                            self.append('Rapport non enregistré : ' + str(exc) + '\n')
                        self.report_shortcut.pack(side='right', padx=(0, 8))
                    self.finished_game = Path(game) if success and game else None
                    self.finished_images = (images, game_identity) if success else ([], None)
                    self.status.set(message)
                    if self.progress_window:
                        self.progress_window.complete(success, message)
                    else:
                        self.operation_finished()
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    def operation_finished(self):
        self.progress_window = None
        self.busy = False
        self.root.protocol('WM_DELETE_WINDOW', self.root.destroy)
        self.root.deiconify()
        attempt, self.failed_attempt = self.failed_attempt, None
        if attempt:
            for line in cleanup_failed_attempt(attempt):
                self.append(line + '\n')
            self.status.set('Échec de la compilation — détails dans le journal.')
        for button in (self.start, self.choose, self.launch_button, self.project_button,
                       self.output_button, self.icon_button, self.background_button):
            button.configure(state='normal')
        self.console_switch.configure(state='normal')
        self.change_console(self.console.get(), update_status=False)
        self.refresh_discs()
        game, self.finished_game = self.finished_game, None
        missing, self.finished_missing_sbi = self.finished_missing_sbi, []
        if game and missing:
            messagebox.showwarning('Fichier SBI manquant',
                'Le jeu a été créé, mais un fichier SBI nécessaire n’a pas été trouvé pour :\n\n'
                + '\n'.join('• ' + item for item in missing)
                + '\n\nDans le lanceur du jeu, utilise « Ajouter le fichier SBI » pour sélectionner le fichier correspondant.', parent=self.root)
        if game:
            self.library.refresh()
        images, game_identity = self.finished_images
        self.finished_images = ([], None)
        self.finished_missing_sbi = []
        if game and messagebox.askyesno('Raccourci sur le bureau',
                                       f'Veux-tu créer un raccourci pour « {game.stem} » sur ton bureau ?', parent=self.root):
            try:
                link = create_desktop_shortcut(game)
                self.status.set('Raccourci créé sur le bureau : ' + link.name)
            except OSError as exc:
                messagebox.showerror('Raccourci impossible', str(exc), parent=self.root)
        if game and images:
            self.offer_image_cleanup(game, game_identity, images)

    def offer_image_cleanup(self, game, game_identity, images):
        groups = {}
        for record in images:
            groups.setdefault(Path(record[0]).parent, []).append(record)
        protected = [BASE, PROJECTS, game, self.output_dir, Path.home()]
        for var in ('USERPROFILE', 'LOCALAPPDATA', 'APPDATA', 'WINDIR', 'ProgramFiles', 'ProgramData'):
            if os.environ.get(var):
                protected.append(Path(os.environ[var]))
        skipped = []
        for folder, records in groups.items():
            contents = image_folder_contents(folder, records, protected)
            if contents is None:
                skipped.append(str(folder))
                continue
            listing = '\n'.join('• ' + name + ('/' if is_dir else '') for name, is_dir, *_ in contents)
            question = ('Le jeu a été créé et son fichier vérifié. Veux-tu déplacer ce dossier et tout son contenu dans la Corbeille ?\n\n'
                        + str(folder) + '\n\n' + listing
                        + '\n\nLe fonctionnement du jeu doit encore être testé. Tu peux répondre Non pour garder les originaux.')
            if not messagebox.askyesno('Images du jeu d’origine', question, parent=self.root, default='no'):
                continue
            try:
                stat = game.stat()
                if (stat.st_size, stat.st_mtime_ns, stat.st_ino) != game_identity:
                    raise OSError('Le fichier du jeu a changé depuis sa vérification.')
                if image_folder_contents(folder, records, protected) != contents:
                    raise OSError('Le contenu ou le chemin du dossier a changé ; il a été conservé.')
                recycle_image_folder(folder)
                self.status.set('Images d’origine déplacées dans la Corbeille : ' + str(folder))
            except OSError as exc:
                messagebox.showerror('Dossier conservé', str(exc), parent=self.root)
        if skipped:
            messagebox.showinfo('Dossier conservé',
                'Ces dossiers ont été conservés : leur contenu a changé, ils contiennent des sauvegardes ou des liens, sont trop volumineux à afficher ou correspondent à un emplacement partagé :\n\n' + '\n'.join(skipped), parent=self.root)

    def show_progress(self, message):
        self.finished_game = None
        self.finished_images = ([], None)
        self.finished_missing_sbi = []
        self.last_failure = None
        self.attempt_log_start = self.log.index('end-1c')
        self.attempt_report_context = (self.name.get().strip(), list(self.discs))
        self.report_shortcut.pack_forget()
        self.failed_attempt = None
        self.root.protocol('WM_DELETE_WINDOW', self.hide_build_windows)
        self.root.withdraw()
        try:
            console = self.console.get()
            console_sound = {'PS2': 'son-demarrage-ps2.wav', 'NES': 'son-demarrage-nes.wav',
                             'SNES': 'son-demarrage-snes.wav', 'N64': 'son-demarrage-n64.wav'}
            console_logo = {'PS2': 'logo-ps2.png', 'NES': 'logo-nes.png',
                            'SNES': 'logo-snes.png', 'N64': 'logo-n64.png'}
            self.progress_window = PhantomIntro(self.root, message, on_finish=self.operation_finished, progress=True,
                                                on_hide=lambda: self.status.set('Compilation en cours : fenêtre de progression réduite.'),
                                                on_report=self.show_error_report,
                                                startup_sound=(BASE / 'assets' / console_sound[console]) if console in console_sound else None,
                                                logo_path=(BASE / 'assets' / console_logo[console]) if console in console_logo else None)
        except Exception:
            self.root.deiconify()
            raise

    def show_error_report(self):
        if not self.last_failure:
            return
        parent = self.progress_window.window if self.progress_window and not self.progress_window.closed else self.root
        open_report_dialog(parent, self.last_failure)

    def hide_build_windows(self):
        if self.progress_window and self.progress_window.completed:
            self.progress_window.finish()
            self.root.destroy()
        elif self.progress_window:
            self.progress_window.request_close()
            self.root.iconify()

    def open_project(self):
        target = self.project if self.project and self.project.exists() else PROJECTS
        target.mkdir(parents=True, exist_ok=True)
        os.startfile(str(target))

    def select_background(self):
        selected = filedialog.askopenfilename(title='Choisir une image de fond pour le jeu', filetypes=[('Images', '*.png *.jpg *.jpeg *.webp'), ('Tous les fichiers', '*.*')])
        if selected:
            self.background_path = selected
            self.icon_text.set('Fond choisi : ' + Path(selected).name)

    def launch(self):
        last = self.last_output_by_console.get(self.console.get())
        if last and last.is_file():
            self.open_compiled_game(last)
            return
        exe = runtime_exe(self.project) if self.project else None
        if not exe:
            messagebox.showinfo('Programme indisponible', 'Ouvre le fichier EXE dans le dossier des jeux. Les fichiers de compilation peuvent avoir été nettoyés après sa création.')
            return
        env = os.environ.copy()
        env['PHANTOM_SPLASH_EXE'] = str(BASE / 'unique_stub.exe')
        try:
            env['PHANTOM_GAME_TITLE'] = str(tomllib.loads((exe.parent / 'game.toml').read_text(encoding='utf-8-sig')).get('game', {}).get('name') or exe.stem.replace('_', ' '))
        except (OSError, ValueError):
            env['PHANTOM_GAME_TITLE'] = exe.stem.replace('_', ' ')
        self.open_compiled_game(exe, env)

    def open_compiled_game(self, path, env=None):
        try:
            self.game_view.start(Path(path), env=env)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            messagebox.showerror('Impossible de lancer le jeu', str(exc), parent=self.root)

    def select_project(self):
        folder = filedialog.askdirectory(title='Choisir un projet déjà créé', initialdir=str(PROJECTS))
        if folder:
            self.project = Path(folder)
            self.icon_path = None
            self.background_path = None
            self.icon_text.set('Icône du projet ou jaquette automatique')
            self.status.set('Projet sélectionné : ' + self.project.name)

    def select_output_directory(self):
        initial = self.output_dir if self.output_dir.is_dir() else BASE
        folder = filedialog.askdirectory(title='Choisir le dossier des jeux compilés', initialdir=str(initial), mustexist=True)
        if not folder:
            return
        self.output_dir = Path(folder).resolve()
        self.output_text.set('Dossier des jeux : ' + str(self.output_dir))
        self.library.refresh()
        try:
            save_output_directory(self.output_dir)
        except OSError as exc:
            messagebox.showwarning('Dossier non mémorisé', 'Le dossier est utilisé pour cette session, mais n’a pas pu être mémorisé :\n' + str(exc))

    def open_packages(self):
        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            os.startfile(str(self.output_dir))
        except OSError as exc:
            messagebox.showerror('Dossier inaccessible', str(exc))

    def confirm_existing_game(self, console, name):
        games = discover_games(self.output_dir, include_cover=False)
        matches = find_duplicates(console, name, self.discs, games)
        if not matches:
            return True
        lines = []
        for game, reason in matches[:5]:
            description = 'image probablement identique' if reason == 'source' else 'nom similaire'
            lines.append('• ' + game['name'] + ' — ' + description + '\n  ' + str(game['path']))
        if len(matches) > 5:
            lines.append('… et ' + str(len(matches) - 5) + ' autre(s) jeu(x).')
        return messagebox.askyesno(
            'Jeu déjà présent',
            'Un jeu qui pourrait déjà correspondre existe dans ta bibliothèque :\n\n' +
            '\n'.join(lines) +
            '\n\nVérifie la version et la console. Veux-tu quand même créer un autre fichier ?',
            parent=self.root, default=messagebox.NO)

    def build(self):
        if self.busy: return
        if self.console.get() == 'PS1' and len(self.discs) == 1 and detect_iso_platform(self.discs[0]) == 'PS2':
            if not self.switch_detected_ps2():
                return
        if self.console.get() == 'PS2':
            self.start_ps2_build()
            return
        if self.console.get() in ('NES', 'SNES', 'N64'):
            self.start_nintendo_build()
            return
        if not self.discs:
            messagebox.showwarning('Disques', 'Sélectionne au moins un disque.')
            return
        if not SETUP.is_file() or not (TOOLCHAIN / 'bin' / 'cmake.exe').exists():
            messagebox.showerror('Outils manquants', 'Le dossier PSXRecomp ou le compilateur est manquant.')
            return
        name = self.name.get().strip()
        if not name:
            messagebox.showwarning('Nom', 'Indique le nom du jeu.')
            return
        if not self.confirm_existing_game('PS1', name):
            return
        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryFile(dir=self.output_dir):
                pass
        except OSError as exc:
            messagebox.showerror('Dossier inaccessible', 'Impossible d’écrire dans le dossier des jeux :\n' + str(exc))
            return
        slug = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')[:45] or 'jeu-ps1'
        PROJECTS.mkdir(parents=True, exist_ok=True)
        target = PROJECTS / slug
        base_slug = slug
        index = 2
        while target.exists():
            slug = f'{base_slug}-{index}'
            target = PROJECTS / slug
            index += 1
        self.project = target
        self.busy = True
        self.start.configure(state='disabled')
        self.choose.configure(state='disabled')
        self.disc_remove.configure(state='disabled')
        self.disc_clear.configure(state='disabled')
        self.launch_button.configure(state='disabled')
        self.project_button.configure(state='disabled')
        self.output_button.configure(state='disabled')
        self.icon_button.configure(state='disabled')
        self.background_button.configure(state='disabled')
        self.console_switch.configure(state='disabled')
        self.status.set('Compilation en cours…')
        self.show_progress('Préparation du projet…')
        threading.Thread(target=self.worker, args=(name, slug, list(self.discs), self.players.get(), self.resolution.get(), self.icon_path, self.phantom_bios.get(), self.background_path, self.output_dir), daemon=True).start()

    def start_ps2_build(self):
        if len(self.discs) != 1 or Path(self.discs[0]).suffix.lower() != '.iso':
            messagebox.showwarning('Disque PS2', 'Sélectionne une seule image ISO PS2 pour ce jeu.')
            return
        name = self.name.get().strip()
        if not name:
            messagebox.showwarning('Nom', 'Indique le nom du jeu.')
            return
        if not self.confirm_existing_game('PS2', name):
            return
        ps2_sound = BASE / 'assets/son-demarrage-ps2.wav'
        if not ps2_sound.is_file():
            selected = filedialog.askopenfilename(title='Choisir le son de démarrage PS2', filetypes=[('Son WAV', '*.wav')])
            if not selected:
                messagebox.showinfo('Son PS2 requis', 'Sélectionne un son de démarrage PS2 au format WAV pour continuer.')
                return
            try:
                source = Path(selected)
                if source.stat().st_size > 10 * 1024 * 1024:
                    raise ValueError('Le fichier dépasse 10 Mo.')
                with wave.open(str(source), 'rb') as sound:
                    if sound.getcomptype() != 'NONE' or sound.getnchannels() not in (1, 2) or sound.getsampwidth() not in (1, 2):
                        raise ValueError('Le fichier doit être un WAV PCM mono ou stéréo.')
                data = source.read_bytes()
                with ps2_sound.open('xb') as dest:
                    dest.write(data)
            except (OSError, ValueError, EOFError, wave.Error) as exc:
                messagebox.showerror('Son PS2 invalide', 'Impossible d’utiliser ce fichier WAV :\n' + str(exc))
                return
        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryFile(dir=self.output_dir): pass
        except OSError as exc:
            messagebox.showerror('Dossier inaccessible', 'Impossible d’écrire dans le dossier des jeux :\n' + str(exc))
            return
        slug = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')[:45] or 'jeu-ps2'
        base_slug = slug; index = 2
        while (PS2_ENGINE / 'games' / slug).exists() or (PS2_ENGINE / 'build' / ('native-' + slug)).exists():
            slug = f'{base_slug}-{index}'; index += 1
        self.busy = True
        for control in (self.start, self.choose, self.disc_remove, self.disc_clear, self.launch_button,
                        self.project_button, self.output_button, self.icon_button, self.background_button, self.console_switch):
            control.configure(state='disabled')
        self.status.set('Compilation PS2 en cours…')
        self.show_progress('Préparation du projet PS2…')
        threading.Thread(target=self.worker_ps2, args=(name, slug, self.discs[0], self.icon_path, self.output_dir, self.background_path, self.players.get()), daemon=True).start()

    def start_nintendo_build(self):
        console = self.console.get()
        extensions = {'NES': {'.nes'}, 'SNES': {'.sfc', '.smc'}, 'N64': {'.z64', '.v64', '.n64'}}
        if len(self.discs) != 1 or Path(self.discs[0]).suffix.lower() not in extensions[console]:
            messagebox.showwarning('Cartouche Nintendo', 'Sélectionne une seule cartouche au format indiqué pour cette console.')
            return
        name = self.name.get().strip()
        if not name:
            messagebox.showwarning('Nom', 'Indique le nom du jeu.')
            return
        if not self.confirm_existing_game(console, name):
            return
        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryFile(dir=self.output_dir): pass
        except OSError as exc:
            messagebox.showerror('Dossier inaccessible', 'Impossible d’écrire dans le dossier des jeux :\n' + str(exc))
            return
        self.busy = True
        for control in (self.start, self.choose, self.disc_remove, self.disc_clear, self.launch_button,
                        self.project_button, self.output_button, self.icon_button, self.background_button, self.console_switch):
            control.configure(state='disabled')
        self.status.set('Compilation ' + console + ' en cours…')
        self.show_progress('Préparation du jeu ' + console + '…')
        threading.Thread(target=self.worker_nintendo, args=(console, name, self.discs[0], self.icon_path, self.output_dir, self.background_path, self.players.get()), daemon=True).start()

    def worker_nintendo(self, console, name, image, icon_path, output, background, players=None):
        try:
            self.events.put(('phase', 'Création et génération ' + console + '…'))
            command = [sys.executable, '-u', str(BASE / 'phantom_nintendo.py'), '--console', console,
                       '--rom', image, '--nom', name, '--sortie', str(output)]
            command += ['--joueurs', str(players)]
            if icon_path: command += ['--icone', str(icon_path)]
            if background: command += ['--fond', str(background)]
            env = os.environ.copy()
            env['PYTHONUTF8'] = '1'
            proc = subprocess.Popen(command, cwd=str(BASE), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, encoding='utf-8', errors='replace', env=env, creationflags=subprocess.CREATE_NO_WINDOW)
            result = None
            for line in proc.stdout:
                if line.startswith('PHANTOM_NINTENDO_RESULT='):
                    result = Path(line.split('=', 1)[1].strip())
                else:
                    self.events.put(('log', line))
                    if 'NES native source cache:' in line or 'SPC700 sound:' in line:
                        self.events.put(('progress', (1, 1, 'Création et génération ' + console)))
                    elif 'NES boot:' in line or 'Super Nintendo demo:' in line:
                        self.events.put(('progress', (1, 1, 'Compilation Windows')))
                    if console == 'N64' and 'Compilation du programme N64' in line:
                        self.events.put(('progress', (1, 1, 'Création et génération N64')))
                        self.events.put(('phase', 'Compilation Windows…'))
                    count = re.match(r'^\s*\[(\d+)\s*/\s*(\d+)\]', line)
                    if console == 'N64' and count:
                        self.events.put(('progress', (int(count.group(1)), int(count.group(2)), 'Compilation Windows')))
                    if 'Création et vérification du fichier unique' in line:
                        self.events.put(('phase', 'Création du fichier unique…'))
            if proc.wait() or not result or not result.is_file():
                raise RuntimeError('La compilation Nintendo a échoué ; consulte le journal.')
            from bibliotheque import game_info
            if game_info(result)['platform'] != console:
                raise RuntimeError('La vérification du jeu Nintendo a échoué.')
            self.last_ps2_output = result
            self.last_output_by_console[console] = result
            try: register_game(result)
            except OSError as exc: self.events.put(('log', 'Bibliothèque non mise à jour : ' + str(exc) + '\n'))
            state = result.stat()
            self.events.put(('done', (True, 'Jeu créé — compatibilité à vérifier en partie.', str(result), [],
                                      (state.st_size, state.st_mtime_ns, state.st_ino))))
        except Exception as exc:
            self.events.put(('log', '\nERREUR Nintendo : ' + str(exc) + '\n'))
            self.events.put(('done', (False, 'Échec de la compilation Nintendo — voir le journal.', None, [], None)))

    def worker_ps2(self, name, slug, image, icon_path, output, background, players='2'):
        try:
            self.events.put(('phase', 'Création et génération PS2…'))
            log = lambda line: self.events.put(('log', line))
            progress = lambda completed, total, label: self.events.put(('progress', (completed, total, label)))
            runtime, elf, serial, token = build_ps2(name, slug, image, log, progress)
            self.events.put(('phase', 'Création du fichier unique…'))
            result = package_ps2(name, image, runtime, elf, serial, output, icon_path, log, progress, background, players=int(players))
            cleanup_ps2_build(slug, token, image, log)
            self.last_ps2_output = result
            self.last_output_by_console['PS2'] = result
            try: register_game(result)
            except OSError as exc: log('Bibliothèque non mise à jour : ' + str(exc) + '\n')
            try: images = original_image_snapshot([image], [], BASE / 'descripteurs')
            except (OSError, ValueError): images = []
            stat = result.stat()
            self.events.put(('done', (True, 'Programme PS2 créé — compatibilité à vérifier.', str(result), images,
                                      (stat.st_size, stat.st_mtime_ns, stat.st_ino))))
        except Exception as exc:
            self.events.put(('log', '\nERREUR PS2 : ' + str(exc) + '\n'))
            self.events.put(('done', (False, 'Échec de la compilation PS2 — voir le journal.', None, [], None)))

    def run_step(self, cmd, env, cwd, label):
        self.events.put(('phase', label + '…'))
        self.events.put(('log', '\n--- ' + label + ' ---\n'))
        proc = subprocess.Popen(cmd, cwd=str(cwd), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace', creationflags=subprocess.CREATE_NO_WINDOW)
        output = []
        in_runtime_build = False
        for line in proc.stdout:
            output.append(line)
            self.events.put(('log', line))
            if '--build' in line and '--target psx-runtime' in line:
                in_runtime_build = True
            count = re.match(r'^\s*\[(\d+)\s*/\s*(\d+)\]', line)
            if count and int(count.group(2)) > 2 and (label != 'Compilation Windows' or in_runtime_build):
                self.events.put(('progress', (int(count.group(1)), int(count.group(2)), label)))
        code = proc.wait()
        if code != 0:
            raise RuntimeError(f'{label} a échoué (code {code}).')
        self.events.put(('progress', (1, 1, label)))
        return ''.join(output)

    def worker(self, name, slug, selected, players, resolution, icon_path, use_phantom_bios, background_path, output_dir):
        token = uuid.uuid4().hex
        partial_path = []
        descriptor_records = []
        protected = list(selected) + [output_dir]
        protected += [p for p in (icon_path, background_path) if p]
        try:
            env = build_environment()
            env['PHANTOM_BUILD_TOKEN'] = token
            cues = []
            descriptors = BASE / 'descripteurs' / (slug + '-' + token)
            for n, entry in enumerate(selected, 1):
                path = Path(entry).resolve()
                if not path.is_file(): raise RuntimeError(f'Fichier introuvable : {path}')
                if path.suffix.lower() == '.cue':
                    cues.append(path)
                elif path.suffix.lower() == '.bin' and path.with_suffix('.cue').is_file():
                    cues.append(path.with_suffix('.cue'))
                elif path.suffix.lower() in ('.bin', '.iso'):
                    descriptors.mkdir(parents=True, exist_ok=True)
                    cue = descriptors / f'disque-{n}.cue'
                    mode = 'MODE1/2048' if path.suffix.lower() == '.iso' else 'MODE2/2352'
                    cue.write_text(f'FILE "{path.as_posix()}" BINARY\n  TRACK 01 {mode}\n    INDEX 01 00:00:00\n', encoding='utf-8')
                    st = cue.stat()
                    descriptor_records.append((cue, (st.st_size, st.st_mtime_ns, st.st_ino)))
                    cues.append(cue)
                    self.events.put(('log', f'Descripteur une piste créé : {cue}\n'))
                else: raise RuntimeError(f'Format non pris en charge : {path}')
            if len(set(cues)) != len(cues): raise RuntimeError('Le même disque a été sélectionné plusieurs fois.')
            self.events.put(('phase', 'Vérification des disques et fichiers SBI…'))
            index = sbi_catalogue()
            serials = [identify_disc(cue, PYTHON, env) for cue in cues]
            sbi_payloads = []
            missing_sbi = []
            for cue, serial in zip(cues, serials):
                try:
                    data = resolve_sbi(cue, serial, [], len(cues), index, lambda line: self.events.put(('log', line)))
                except (OSError, ValueError, subprocess.SubprocessError) as exc:
                    data = None
                    self.events.put(('log', 'SBI non obtenu pour ' + (serial or str(cue)) + ' : ' + str(exc) + '\n'))
                if data is None and serial in index:
                    missing_sbi.append(serial + ' — ' + Path(cue).name)
                sbi_payloads.append(data)
            bundled = local_ps1_dependencies(BASE, env)
            psx_url = bundled['psxrecomp'].resolve().as_uri() if bundled else 'https://github.com/RetroPortingToolKit/psxrecomp.git'
            cmd = ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(SETUP), '-Disc', str(cues[0]), '-Dir', str(PROJECTS), '-Name', name, '-GithubRepo', slug, '-Players', players, '-Region', 'Non définie', '-Yes', '-EnableRecompUi', '-NoNetplay', '-NoFetchBoxart', '-NoGithub', '-Generate', '-NoBuild', '-PsxrecompUrl', psx_url]
            if bundled:
                cmd += ['-RecompUiUrl', bundled['recomp-ui'].resolve().as_uri()]
            out = self.run_step(cmd, env, BASE, 'Création et génération')
            if 'Generate failed' in out or 'generate OK' not in out:
                raise RuntimeError('La génération n’a pas confirmé sa réussite. Consulte le journal.')
            if bundled:
                stage_ps1_archives(BASE, self.project)
            if len(cues) > 1:
                cmd = [str(PYTHON), str(self.project / 'psxrecomp' / 'tools' / 'new_project_layout' / 'update_disc_set.py'), '--game-toml', str(self.project / 'game.toml')] + [str(p) for p in cues]
                self.run_step(cmd, env, self.project, 'Enregistrement des disques')
            game_config = self.project / 'game.toml'
            config = game_config.read_text(encoding='utf-8-sig')
            config = set_resolution(config, resolution)
            game_config.write_text(config, encoding='utf-8')
            self.events.put(('log', f'Options demandées : {players} joueur(s), résolution {resolution}.\n'))
            install_intro_hook(self.project)
            install_game_theme(self.project)
            install_play_button(self.project)
            install_disc_menu_fr(self.project)
            install_disc_hotkey(self.project)
            install_sbi_display(self.project, required_serials=[serial for serial in serials if serial in index])
            install_controller_auto(self.project)
            install_controller_database(self.project)
            install_menu_keyboard(self.project)
            install_window_icon(self.project)
            install_easy_settings(self.project)
            install_bios_display_fix(self.project)
            install_codes(self.project)
            install_cheat_search(self.project)
            install_save_directory(self.project)
            install_fps_setting(self.project)
            try:
                install_build_progress(self.project)
            except (OSError, ValueError) as exc:
                self.events.put(('log', 'Progression détaillée indisponible pour ce moteur : ' + str(exc) + '\n'))
            if use_phantom_bios:
                self.events.put(('log', 'Intégration du BIOS PhantomStation expérimental…\n'))
                install_phantom_bios(self.project, env)
            cmd = [str(PYTHON), str(self.project / 'psxrecomp' / 'psxrecomp_cli.py'), 'rebuild', '--config', 'game.toml', '--project-root', '.', '--build-dir', 'build-release', '--no-pgo']
            self.run_step(cmd, env, self.project, 'Compilation Windows')
            exe = runtime_exe(self.project)
            if not exe: raise RuntimeError('Le programme compilé est introuvable dans build-release.')
            self.events.put(('log', f'\nProgramme créé : {exe}\nTeste les disques en jeu.\n'))
            self.events.put(('log', '\nPréparation du fichier unique…\n'))
            self.events.put(('phase', 'Création du fichier unique…'))
            try:
                images = original_image_snapshot(selected, cues, descriptors)
                if not images:
                    self.events.put(('log', 'Dossiers d’images conservés : chemin ou pistes à vérifier manuellement.\n'))
            except (OSError, ValueError) as exc:
                images = []
                self.events.put(('log', 'Dossiers d’images conservés : ' + str(exc) + '\n'))
            result = package_game(self.project, lambda line: self.events.put(('log', line)), icon_path=icon_path, background_path=background_path,
                         progress=lambda done, total, phase: self.events.put(('progress', (done, total, phase))), destination_dir=output_dir, sbi_payloads=sbi_payloads,
                         on_partial=partial_path.append, attempt_token=token)
            self.last_output_by_console['PS1'] = result
            try:
                register_game(result)
            except OSError as exc:
                self.events.put(('log', 'Jeu créé, mais bibliothèque non mise à jour : ' + str(exc) + '\n'))
            marker = self.project / '.phantom-build-token'
            if marker.is_file() and marker.read_text(encoding='utf-8') == token:
                marker.unlink()
            for line in cleanup_descriptors(descriptor_records):
                self.events.put(('log', line + '\n'))
            stat = result.stat()
            identity = (stat.st_size, stat.st_mtime_ns, stat.st_ino)
            if missing_sbi:
                self.events.put(('missing_sbi', missing_sbi))
            self.events.put(('done', (True, 'Fichier unique créé — SBI manquant.' if missing_sbi else 'Fichier unique créé — compatibilité en jeu à vérifier.', str(result), images, identity)))
        except Exception as exc:
            self.events.put(('log', '\nERREUR : ' + str(exc) + '\n'))
            attempt = {'project': self.project, 'token': token, 'protected': protected, 'output': Path(output_dir), 'snapshot': None,
                       'descriptors': descriptor_records}
            if partial_path:
                try:
                    st = partial_path[0].lstat()
                    attempt['partial'] = (partial_path[0], (st.st_size, st.st_mtime_ns, st.st_ino))
                except OSError:
                    pass
            if self.project.exists():
                try:
                    attempt['snapshot'] = failed_project_snapshot(self.project, token, protected)
                except (OSError, ValueError) as problem:
                    attempt['reason'] = str(problem)
            self.events.put(('failed_attempt', attempt))
            self.events.put(('done', (False, 'Échec ou préparation incomplète — voir le journal.', None, [], None)))


if __name__ == '__main__':
    set_process_identity()
    app = tk.Tk()
    if len(sys.argv) == 3 and sys.argv[1] == '--jouer':
        app.withdraw()
        project = Path(sys.argv[2]).resolve()
        # The per-game shortcut lives in build-release.
        exe = runtime_exe(project.parent if project.name == 'build-release' else project)
        if exe:
            launch_game(exe, app)
            app.destroy()
        else:
            messagebox.showerror('Programme indisponible', 'Impossible de retrouver le jeu recompilé.')
            app.destroy()
    elif accept_responsible_use(app):
        compiler = App(app)
        app.after(1200, lambda: start_update_check(app, compiler))
    else:
        app.destroy()
        sys.exit(0)
    app.mainloop()
