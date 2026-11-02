import json
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch
from types import SimpleNamespace

from detection_doublons import encoded_source, find_duplicates, fingerprint_selection, normalized_title
from lanceur import App

with tempfile.TemporaryDirectory(prefix='phantom-test-doublon-') as folder:
    root = Path(folder)
    data = root / 'Mon jeu (France) (Disc 1).bin'
    data.write_bytes(b'jeu original' * 10000)
    cue = root / 'Mon jeu (France) (Disc 1).cue'
    cue.write_text('FILE "' + data.name + '" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n')
    digest = fingerprint_selection('PS1', [cue])
    assert digest == json.loads(encoded_source('PS1', [[data]]))
    assert fingerprint_selection('PS1', [data]) == digest
    same_image = {'platform': 'PS1', 'name': 'Nom modifié', 'original_name': 'Autre nom',
                  'path': root/'Nom modifié.exe', 'source': digest}
    same_title = {'platform': 'PS1', 'name': 'Mon jeu', 'original_name': 'Mon jeu',
                  'path': root/'Mon jeu.exe', 'source': None}
    other_console = dict(same_title, platform='PS2')
    matches = find_duplicates('PS1', 'Mon jeu', [cue], [same_image, same_title, other_console])
    assert [reason for _, reason in matches] == ['source', 'titre'], matches
    assert not find_duplicates('N64', 'Mon jeu', [data], [same_title])
    assert normalized_title('Dino Crisis 2 (France) (Disc 1).cue') == 'dino crisis 2'
    assert normalized_title('Dino Crisis (France).cue') != normalized_title('Dino Crisis 2 (France).cue')
    app = object.__new__(App)
    app.output_dir = root
    app.discs = [str(cue)]
    app.root = object()
    with patch('lanceur.discover_games', return_value=[same_title]), patch('lanceur.messagebox.askyesno', return_value=False) as ask:
        assert not app.confirm_existing_game('PS1', 'Mon jeu')
        assert ask.call_args.kwargs['default'] == 'no'
        assert 'Mon jeu.exe' in ask.call_args.args[1] and 'nom similaire' in ask.call_args.args[1]
    with patch('lanceur.discover_games', return_value=[same_title]), patch('lanceur.messagebox.askyesno', return_value=True):
        assert app.confirm_existing_game('PS1', 'Mon jeu')
    with patch('lanceur.discover_games', return_value=[]), patch('lanceur.messagebox.askyesno') as ask:
        assert app.confirm_existing_game('PS1', 'Mon jeu')
        ask.assert_not_called()
    toolchain = root/'outils'
    (toolchain/'bin').mkdir(parents=True)
    (toolchain/'bin'/'cmake.exe').touch()
    setup = root/'setup.ps1'
    setup.touch()
    for console, file in [('PS1', 'jeu.cue'), ('PS2', 'jeu.iso'), ('NES', 'jeu.nes'),
                          ('SNES', 'jeu.sfc'), ('N64', 'jeu.z64')]:
        check = Mock(return_value=False)
        fake = SimpleNamespace(busy=False, console=SimpleNamespace(get=lambda c=console: c),
                               name=SimpleNamespace(get=lambda: 'Mon jeu'), discs=[str(root/file)],
                               confirm_existing_game=check)
        fake.start_ps2_build = lambda: App.start_ps2_build(fake)
        fake.start_nintendo_build = lambda: App.start_nintendo_build(fake)
        with patch('lanceur.SETUP', setup), patch('lanceur.TOOLCHAIN', toolchain), patch('lanceur.detect_iso_platform', return_value=None):
            App.build(fake)
        check.assert_called_once_with(console, 'Mon jeu')
print('Doublons : image, ancien jeu, console, titre et choix du joueur OK')
