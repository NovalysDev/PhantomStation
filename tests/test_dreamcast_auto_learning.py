import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import bibliotheque
import phantom_dc_autolearn as auto


class AutomaticLearningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        env = patch.dict(os.environ, {'LOCALAPPDATA': str(self.root)})
        env.start(); self.addCleanup(env.stop)
        running = patch.object(bibliotheque, 'game_running', return_value=False)
        running.start(); self.addCleanup(running.stop)
        self.local = self.root / 'PhantomStation'
        self.game = {'id': 'a' * 32, 'platform': 'DC', 'runtime': 'dreamcast.exe', 'path': self.root / 'game.exe'}
        self.cache = self.local / 'Jeux' / self.game['id']
        self.cache.mkdir(parents=True)
        (self.cache / 'dreamcast.exe').write_bytes(b'stable')
        (self.cache / '1ST_READ.BIN').write_bytes(b'boot')
        (self.cache / 'disque.chd').write_bytes(b'disc')
        boot_id = auto._boot_id(self.cache / '1ST_READ.BIN')
        self.profile = self.local / 'Dreamcast-apprentissage' / boot_id
        session = self.profile / 'partie-20261109-190000'
        session.mkdir(parents=True)
        (session / 'bloc-000.bin').write_bytes(bytes.fromhex('e62fd62f224ff07f') + bytes(64))
        (session / 'appels.txt').write_text('bloc 0x8C400000 0x8C010010 0x8C400000 0x00000048 bloc-000.bin fenetre\n')
        self.builds = 0

    def builder(self, disc, destination, log):
        self.builds += 1
        destination.mkdir()
        (destination / 'Crazy Taxi - prototype.exe').write_bytes(b'trial')
        work = Path(os.environ['PHANTOM_DC_KEEP_WORK']) / 'game/src/game'
        work.mkdir(parents=True)
        sandbox = Path(os.environ['LOCALAPPDATA']) / 'PhantomStation/Dreamcast-apprentissage'
        learned = auto._learn_module().Learned(sandbox / auto._boot_id(disc.parent / '1ST_READ.BIN'))
        block = learned.blocks[0]; address = next(iter(block['targets']))
        (work / 'learned_table.c').write_text(f"0x{address:08X}u, L{block['file'][5:-4]}_func_{address:08X}")
        (work / 'dispatch_table.c').write_text('')
        return destination

    def test_trial_is_kept_only_after_confirmation(self):
        self.assertTrue(auto.prepare(self.game, self.builder, lambda *args: None))
        trial = auto.candidate(self.game)
        self.assertIsNotNone(trial)
        self.assertFalse((self.profile / 'profil.json').exists())
        self.assertFalse((self.cache / 'dreamcast-correction.exe').exists())
        auto.decide(self.game, trial['candidat'], True)
        self.assertEqual((self.cache / 'dreamcast-correction.exe').read_bytes(), b'trial')
        self.assertEqual((self.cache / 'dreamcast.exe').read_bytes(), b'stable')
        self.assertTrue((self.profile / 'profil.json').is_file())
        self.assertIsNone(auto.candidate(self.game))

    def test_rejected_trial_is_not_rebuilt_without_new_code(self):
        self.assertTrue(auto.prepare(self.game, self.builder, lambda *args: None))
        trial = auto.candidate(self.game)
        auto.decide(self.game, trial['candidat'], False)
        self.assertFalse((self.profile / 'profil.json').exists())
        self.assertFalse((self.cache / 'dreamcast-correction.exe').exists())
        self.assertFalse(auto.prepare(self.game, self.builder, lambda *args: None))
        self.assertEqual(self.builds, 1)

    def test_failed_test_leaves_the_stable_engine_untouched(self):
        def fail(*args): raise ValueError('test failed')
        with self.assertRaises(ValueError):
            auto.prepare(self.game, self.builder, fail)
        self.assertEqual((self.cache / 'dreamcast.exe').read_bytes(), b'stable')
        self.assertFalse((self.profile / 'profil.json').exists())
        self.assertIsNone(auto.candidate(self.game))
        self.assertEqual(list((self.local / 'Apprentissage-temp').iterdir()), [])

    def test_changed_snapshot_is_never_promoted(self):
        self.assertTrue(auto.prepare(self.game, self.builder, lambda *args: None))
        trial = auto.candidate(self.game)
        snapshot = self.local / 'Apprentissage-candidats' / self.game['id']
        block = next(snapshot.glob('bloc-*.bin'))
        block.write_bytes(b'changed')
        self.assertIsNone(auto.candidate(self.game))
        with self.assertRaises(ValueError):
            auto.decide(self.game, trial['candidat'], True)
        self.assertEqual((self.cache / 'dreamcast.exe').read_bytes(), b'stable')
        self.assertFalse((self.cache / 'dreamcast-correction.exe').exists())
        self.assertFalse((self.profile / 'profil.json').exists())

    def test_running_game_is_never_changed(self):
        with patch.object(bibliotheque, 'game_running', return_value=True):
            self.assertFalse(auto.prepare(self.game, self.builder, lambda *args: None))
        self.assertEqual(self.builds, 0)
        self.assertEqual((self.cache / 'dreamcast.exe').read_bytes(), b'stable')

    def test_strict_engine_check_rejects_a_crash(self):
        learning = auto._learn_module()
        out = self.root / 'hidden-run'
        with patch.dict(os.environ, {'PHANTOM_DC_STRICT_LEARN': '1'}), \
             patch.object(learning.subprocess, 'run', return_value=SimpleNamespace(returncode=7)):
            with self.assertRaises(RuntimeError):
                learning.learning_run(self.cache / 'dreamcast.exe', self.cache / '1ST_READ.BIN',
                                      self.cache / 'disque.chd', out, seconds=1)


if __name__ == '__main__':
    unittest.main()
