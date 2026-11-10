import sys
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
ui = types.ModuleType('phantom_ui')
ui.ctk = object()
ui.button = lambda *args, **kwargs: None
ui.tr = lambda text: text
ui.load_language = lambda: 'fr'
ui.BG = ui.MUTED = ui.TEXT = ''
with patch.dict(sys.modules, {'phantom_ui': ui}):
    import phantom_compiler_game as game_ui
import phantom_dc_autolearn as auto


class LearningUiTests(unittest.TestCase):
    def view(self, used=True, trial='trial-hash', changed=True):
        view = game_ui.GameInCompiler.__new__(game_ui.GameInCompiler)
        view.game_info = {'id': 'a'*32, 'platform': 'DC'}
        view.trial_hash, view.trial_log_stamp = trial, 1
        view.trial_token, view.debug_active = 'token', False
        view.report_hotkey = view.after_id = view.previous_protocol = None
        view.overlay = view.job = view.pending_report = None
        view.root = Mock()
        view._trial_was_used = Mock(return_value=used)
        view._journal_stamp = Mock(return_value=2 if changed else 1)
        view._queue_learning = Mock()
        view.on_learning = Mock()
        return view

    def test_used_candidate_is_kept_and_learning_continues_without_prompt(self):
        view = self.view()
        with patch.object(auto, 'decide') as decide, patch.object(game_ui.messagebox, 'askyesnocancel') as prompt:
            view._finish(completed=True)
        decide.assert_called_once_with(view.game_info, 'trial-hash', True)
        view._queue_learning.assert_called_once_with(view.game_info)
        prompt.assert_not_called()

    def test_unused_candidate_is_not_promoted(self):
        view = self.view(used=False)
        with patch.object(auto, 'decide') as decide:
            view._finish(completed=True)
        decide.assert_not_called()
        view._queue_learning.assert_not_called()

    def test_candidate_with_unchanged_log_is_not_promoted(self):
        view = self.view(changed=False)
        with patch.object(auto, 'decide') as decide:
            view._finish(completed=True)
        decide.assert_not_called()
        view._queue_learning.assert_not_called()

    def test_failed_promotion_does_not_queue_rebuild(self):
        view = self.view()
        with patch.object(auto, 'decide', side_effect=ValueError('invalide')):
            view._finish(completed=True)
        view._queue_learning.assert_not_called()
        self.assertIn('invalide', view.on_learning.call_args.args[0])

    def test_normal_session_queues_learning(self):
        view = self.view(trial=None)
        view._finish(completed=True)
        view._queue_learning.assert_called_once_with(view.game_info)


if __name__ == '__main__':
    unittest.main()
