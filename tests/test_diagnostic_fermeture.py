import hashlib
import os
import sys
import tempfile
import unittest
import zipfile
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bibliotheque
import diagnostic_jeu
import phantom_compiler_game as compiler
import phantom_report_ui


class Root:
    def __init__(self): self.callbacks = []
    def after_idle(self, callback): self.callbacks.append(callback)
    def wait_window(self, dialog): pass
    def deiconify(self): pass
    def lift(self): pass
    def focus_force(self): pass


class DiagnosticClosureTests(unittest.TestCase):
    def test_f10_closes_before_collecting_and_sending(self):
        game = {'id': '1' * 32, 'name': 'Jeu', 'platform': 'DC'}
        view = compiler.GameInCompiler.__new__(compiler.GameInCompiler)
        view.root = Root(); view.game_info = game; view.report_open = False
        view.pending_report = None; view.save_selection = {}; view.process = object()
        view.trial_hash = view.trial_token = view.trial_log_stamp = None
        view.debug_active = False; view.pending_review = None
        view._queue_learning = lambda info: None
        view.closed = False; view.close_game = lambda: setattr(view, 'closed', True)
        view.report_hotkey = view.after_id = view.previous_protocol = view.overlay = view.job = view.child = None
        view.windows = []; view.closing = False
        with patch.object(compiler.simpledialog, 'askstring', return_value='boss invisible'), \
             patch.object(compiler.messagebox, 'askyesnocancel', side_effect=[True, True]), \
             patch.object(compiler.messagebox, 'askyesno', return_value=True):
            view.report_game()
        self.assertTrue(view.closed)
        self.assertTrue(view.pending_report)
        self.assertEqual(view.root.callbacks, [])
        view._finish(completed=True)
        self.assertEqual(len(view.root.callbacks), 1)
        with patch.object(bibliotheque, 'game_running', return_value=False), \
             patch.object(diagnostic_jeu, 'game_save_files', return_value=[]), \
             patch.object(diagnostic_jeu, 'create_game_diagnostic', return_value=(Path('report.zip'), 'text')) as create, \
             patch('rapport_erreur._delivery_status'), \
             patch.object(phantom_report_ui, 'open_report_dialog', return_value=None) as dialog:
            view.root.callbacks.pop()()
        self.assertTrue(create.call_args.kwargs['include_blocks'])
        self.assertTrue(dialog.call_args.args[1]['auto_send'])

    def test_trial_waits_for_the_players_confirmation(self):
        view = compiler.GameInCompiler.__new__(compiler.GameInCompiler)
        view.root = Root()
        game = {'id': 'a' * 32, 'platform': 'DC'}
        view.pending_review = (game, 'b' * 64)
        view._queue_learning = lambda info: None
        with patch.object(compiler.messagebox, 'askyesnocancel', return_value=None), \
             patch('phantom_dc_autolearn.decide') as decide:
            view._review_learning()
            decide.assert_not_called()
        view.pending_review = (game, 'b' * 64)
        with patch.object(compiler.messagebox, 'askyesnocancel', return_value=True), \
             patch.object(compiler.messagebox, 'showinfo'), \
             patch('phantom_dc_autolearn.decide') as decide:
            view._review_learning()
            decide.assert_called_once_with(game, 'b' * 64, True)

    def test_dreamcast_blocks_belong_to_last_session(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {'LOCALAPPDATA': temp}):
            local = Path(temp) / 'PhantomStation'; ident = 'a' * 32
            boot = local / 'Jeux' / ident / '1ST_READ.BIN'; boot.parent.mkdir(parents=True)
            boot.write_bytes(b'disc'); digest = hashlib.sha1(b'disc').hexdigest()
            now = datetime.now(); journal = local / 'journal.log'
            journal.write_text('Jeu : Test | ' + now.strftime('%Y-%m-%d %H:%M:%S') + ' | essai 1\n', encoding='utf-8')
            folder = local / 'Dreamcast-apprentissage' / digest / ('partie-' + now.strftime('%Y%m%d-%H%M%S'))
            folder.mkdir(parents=True)
            (folder / 'appels.txt').write_text('test', encoding='utf-8')
            (folder / 'bloc-000.bin').write_bytes(b'code')
            (folder / 'autre.bin').write_bytes(b'ignore')
            files = diagnostic_jeu.game_learned_blocks({'id': ident, 'platform': 'DC'}, journal)
            self.assertEqual({name for _, name in files}, {'blocs-dreamcast/appels.txt', 'blocs-dreamcast/bloc-000.bin'})


if __name__ == '__main__': unittest.main()
