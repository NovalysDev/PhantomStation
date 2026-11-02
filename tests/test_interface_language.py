"""Language selection preserves the current build form and the user's settings."""
import sys
import os
import subprocess
import tempfile
import tkinter as tk
from pathlib import Path
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
import phantom_ui
import lanceur
from phantom_intro import CompilationProgress

with tempfile.TemporaryDirectory(prefix='langue-') as tmp:
    folder = Path(tmp)
    old_file, old_language = phantom_ui.LANGUAGE_FILE, phantom_ui.LANGUAGE
    try:
        phantom_ui.LANGUAGE_FILE = folder/'profile'/'language.txt'
        phantom_ui.set_language('fr')
        root = tk.Tk(); root.withdraw()
        with patch('phantom_library_ui.discover_games', return_value=[]):
            app = lanceur.App(root); root.update()
            app.name.set('Mon jeu')
            app.discs = [str(folder/'jeu.cue')]
            app.output_dir = folder
            app.language_choice.set('EN'); app.change_language('EN'); root.update()
            assert phantom_ui.load_language() == 'en'
            assert app.name.get() == 'Mon jeu' and app.discs == [str(folder/'jeu.cue')]
            assert app.output_dir == folder
            environment = os.environ.copy(); environment['LOCALAPPDATA'] = str(folder/'profile-root')
            saved_profile = phantom_ui.LANGUAGE_FILE
            phantom_ui.LANGUAGE_FILE = Path(environment['LOCALAPPDATA'])/'PhantomStation'/'language.txt'
            phantom_ui.set_language('en')
            check = subprocess.run([sys.executable, '-c', 'import phantom_ui; print(phantom_ui.load_language())'], cwd=BASE,
                                   env=environment, capture_output=True, text=True, check=True)
            assert check.stdout.strip() == 'en'
            phantom_ui.LANGUAGE_FILE = saved_profile
            assert app.start.cget('text') == '✦  CREATE PROGRAM'
            assert app.nav_buttons['library'].cget('text') == '▦   Games'
            assert app.library.library_title.get() == 'Library PS1'
            assert 'GAME FORMATS' in app.guide_body.get('1.0','end')
            progress = CompilationProgress(); progress.advance(1, 2, 'Création et génération N64')
            assert 'Creation and generation N64' in progress.text()
            assert progress.value == 15.0
            assert phantom_ui.tr(lanceur.NOTICE_TEXT).startswith('PhantomStation must be used')
            assert phantom_ui.tr('Dossier : C:\\Jeux') == 'Folder: C:\\Jeux'
            app.busy = True
            app.language_choice.set('FR')
            with patch.object(lanceur.messagebox, 'showinfo') as warning: app.change_language('FR')
            assert warning.called and phantom_ui.load_language() == 'en'
            app.busy = False
            app.language_choice.set('FR'); app.change_language('FR'); root.update()
            assert app.start.cget('text') == '✦  CRÉER LE PROGRAMME'
            assert app.name.get() == 'Mon jeu'
            root.destroy()
    finally:
        phantom_ui.LANGUAGE_FILE, phantom_ui.LANGUAGE = old_file, old_language
print('OK : choix FR/EN, conservation du formulaire et mémorisation')
