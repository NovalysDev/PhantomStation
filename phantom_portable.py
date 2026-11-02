"""Make bundled build tools available to PhantomStation and its children."""
import hashlib
import ctypes
import os
import shutil
from contextlib import contextmanager
from pathlib import Path


def configure_portable_tools(base):
    base = Path(base)
    git_root = base / 'outils' / 'git-portable'
    git = git_root / 'cmd' / 'git.exe'
    if git.is_file():
        # cmd/git.exe locates the rest of MinGit relative to its own directory.
        # The bash directory is also needed by the PS1 project setup script.
        paths = [str(git.parent), str(git_root / 'usr' / 'bin')]
        existing = os.environ.get('PATH', '')
        os.environ['PATH'] = os.pathsep.join(paths + ([existing] if existing else []))


@contextmanager
def safe_build_path(path):
    """Expose a shell-safe Windows path for build tools during a single build.

    Ninja may put compiler paths containing parentheses into an unquoted cmd.exe
    command. A temporary DOS device avoids changing or copying the installation.
    Only our exact mapping is removed on exit; existing drives are untouched.
    """
    path = Path(path).resolve(strict=True)
    if os.name != 'nt' or all(32 < ord(ch) < 127 and ch not in '()&|<>^%!"' for ch in str(path)):
        yield path
        return

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.QueryDosDeviceW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
    kernel.QueryDosDeviceW.restype = ctypes.c_uint32
    kernel.DefineDosDeviceW.argtypes = [ctypes.c_uint32, ctypes.c_wchar_p, ctypes.c_wchar_p]
    kernel.DefineDosDeviceW.restype = ctypes.c_int
    # Local DOS devices are allocated backwards to avoid global drive names.
    device = None
    for letter in reversed('DEFGHIJKLMNOPQRSTUVWXYZ'):
        candidate = letter + ':'
        target = ctypes.create_unicode_buffer(32768)
        if kernel.QueryDosDeviceW(candidate, target, len(target)):
            continue
        if ctypes.get_last_error() != 2:  # ERROR_FILE_NOT_FOUND, not a free drive otherwise
            continue
        if kernel.DefineDosDeviceW(0x8, candidate, str(path)):  # DDD_NO_BROADCAST_SYSTEM
            device = candidate
            break
    if device is None:
        raise OSError('Impossible de préparer un chemin de compilation temporaire : aucun lecteur disponible.')
    try:
        yield Path(device + '\\')
    finally:
        flags = 0x8 | 0x2 | 0x4  # NO_BROADCAST, REMOVE_DEFINITION, EXACT_MATCH_ON_REMOVE
        if not kernel.DefineDosDeviceW(flags, device, str(path)):
            raise ctypes.WinError(ctypes.get_last_error())


def local_ps1_dependencies(base, environment):
    """Use bundled Git repositories for PS1 projects when all are present."""
    root = Path(base) / 'outils' / 'dependances-git'
    repositories = {name: root / (name + '.git') for name in
                    ('psxrecomp', 'recomp-ui', 'recomp-net', 'rbengine')}
    if not all((path / 'HEAD').is_file() for path in repositories.values()):
        return None

    try:
        count = int(environment.get('GIT_CONFIG_COUNT', '0'))
    except ValueError as exc:
        raise ValueError('Configuration Git invalide.') from exc
    if count < 0 or count > 1000:
        raise ValueError('Configuration Git invalide.')
    options = [('protocol.file.allow', 'always')]
    for name, upstream in (
        ('recomp-net', 'https://github.com/RetroPortingToolKit/recomp-net.git'),
        ('rbengine', 'https://github.com/RetroPortingToolKit/rbengine.git'),
    ):
        options.append(('url.' + repositories[name].resolve().as_uri() + '.insteadOf', upstream))
    for key, value in options:
        environment[f'GIT_CONFIG_KEY_{count}'] = key
        environment[f'GIT_CONFIG_VALUE_{count}'] = value
        count += 1
    environment['GIT_CONFIG_COUNT'] = str(count)
    return repositories


def stage_ps1_archives(base, project):
    """Verify and copy the pinned PS1 build archives to a generated project."""
    files = (
        ('psxrecomp/third_party/libwebp-4fa21912338357f89e4fd51cf2368325b59e9bd9.tar.gz',
         'psxrecomp/third_party/libwebp-4fa21912338357f89e4fd51cf2368325b59e9bd9.tar.gz',
         '923f3382a47a2af185c3240c954cf004428b237bd7317413a95146d01eb4b94b'),
        ('outils/archives-ps1/python-3.13.1-embed-amd64.zip',
         '.cache/overlay-toolchain/python-3.13.1-embed-amd64.zip',
         '7b7923ff0183a8b8fca90f6047184b419b108cb437f75fc1c002f9d2f8bcec16'),
        ('outils/archives-ps1/tcc-0.9.27-win64-bin.zip',
         '.cache/overlay-toolchain/tcc-0.9.27-win64-bin.zip',
         '34a721949a2583fdff725312da092fa0f5f1f284b702e6f811c6954714faabb2'),
    )
    pending = []
    for source_name, target_name, expected in files:
        source = Path(base) / source_name
        target = Path(project) / target_name
        if not source.is_file():
            raise FileNotFoundError('Archive PS1 intégrée manquante : ' + str(source))
        with source.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != expected:
                raise ValueError('L’archive PS1 intégrée ne correspond pas à la version vérifiée : ' + source.name)
        if target.exists():
            with target.open('rb') as stream:
                if hashlib.file_digest(stream, 'sha256').hexdigest() != expected:
                    raise ValueError('Une archive différente existe déjà dans le projet : ' + target.name)
        else:
            pending.append((source, target))
    for source, target in pending:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
