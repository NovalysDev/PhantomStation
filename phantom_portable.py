"""Make bundled build tools available to PhantomStation and its children."""
import hashlib
import os
import shutil
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
