"""Utilise le logo PhantomStation pour les fenêtres natives de jeu."""
from pathlib import Path
import shutil

BASE = Path(__file__).resolve().parent


def install_window_icon(project):
    project = Path(project)
    target = project / 'assets'
    target.mkdir(parents=True, exist_ok=True)
    for source, name in ((BASE / 'assets/phantomstation.ico', 'psxrecomp.ico'),
                         (BASE / 'assets/phantomstation-symboles.png', 'psxrecomp.png')):
        if not source.is_file():
            raise ValueError('Logo PhantomStation introuvable : ' + str(source))
        shutil.copyfile(source, target / name)
    # Les builds existants doivent recompiler la ressource Windows de l'icône.
    resource = project / 'build-release/psx-runtime_app_icon.rc'
    if resource.is_file():
        resource.touch()
