import errno
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import bibliotheque
import gestion_jeux as storage


def game_file(path, platform, game_id):
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('phantom-package.txt', game_id + '\nruntime.exe\n')
        archive.writestr('game.toml', f'[game]\nname="{path.stem}"\nplatform="{platform}"\n')
        archive.writestr('disc.dat', b'game data')
    return bibliotheque.game_info(path, include_cover=False)


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.old, self.new = self.root / 'ancien', self.root / 'nouveau'
        self.old.mkdir(); self.new.mkdir()
        self.registry = patch.object(bibliotheque, 'LIBRARY_FILE', self.root / 'library.json')
        self.registry.start(); self.addCleanup(self.registry.stop)
        self.running = patch.object(bibliotheque, 'game_running', return_value=False)
        self.running.start(); self.addCleanup(self.running.stop)

    def test_move_registered_and_legacy_game_into_console_folders(self):
        ps1 = game_file(self.old / 'one.exe', 'PS1', 'a' * 32)
        dc = game_file(self.old / 'DC' / 'two.exe', 'DC', 'b' * 32)
        outside = game_file(self.root / 'autre' / 'outside.exe', 'NES', 'c' * 32)
        bibliotheque.register_game(ps1['path']); bibliotheque.register_game(outside['path'])
        found = storage.games_in_directory(self.old)
        self.assertEqual({x['id'] for x in found}, {ps1['id'], dc['id']})
        storage.move_games(found, self.old, self.new)
        self.assertTrue((self.new / 'PS1' / 'one.exe').is_file())
        self.assertTrue((self.new / 'DC' / 'two.exe').is_file())
        self.assertFalse(ps1['path'].exists()); self.assertFalse(dc['path'].exists())
        self.assertTrue(outside['path'].exists())
        self.assertEqual({p.resolve() for p in bibliotheque.registered_games()},
                         {(self.new / 'PS1' / 'one.exe').resolve(), (self.new / 'DC' / 'two.exe').resolve(), outside['path']})

    def test_collision_preflight_moves_nothing(self):
        a = game_file(self.old / 'a.exe', 'PS1', 'a' * 32)
        b = game_file(self.old / 'b.exe', 'DC', 'b' * 32)
        other = self.new / 'DC' / 'b.exe'; other.parent.mkdir(); other.write_bytes(b'existing')
        with self.assertRaises(FileExistsError): storage.move_games([a, b], self.old, self.new)
        self.assertTrue(a['path'].exists()); self.assertTrue(b['path'].exists())
        self.assertEqual(other.read_bytes(), b'existing')

    def test_cross_volume_copy_is_verified_and_registered(self):
        game = game_file(self.old / 'a.exe', 'PS2', 'a' * 32)
        bibliotheque.register_game(game['path'])
        original = os.rename
        def cross_volume(src, dst):
            if Path(src) == game['path']: raise OSError(errno.EXDEV, 'cross-device')
            return original(src, dst)
        with patch.object(storage.os, 'rename', side_effect=cross_volume):
            storage.move_games([game], self.old, self.new)
        destination = self.new / 'PS2' / 'a.exe'
        self.assertFalse(game['path'].exists())
        self.assertEqual(bibliotheque.game_info(destination, False)['id'], game['id'])
        self.assertEqual(bibliotheque.registered_games(), [destination])

    def test_running_game_is_not_moved(self):
        game = game_file(self.old / 'a.exe', 'PS1', 'a' * 32)
        with patch.object(bibliotheque, 'game_running', return_value=True):
            with self.assertRaises(ValueError): storage.move_games([game], self.old, self.new)
        self.assertTrue(game['path'].exists())

    def test_failed_cross_volume_verification_keeps_original(self):
        game = game_file(self.old / 'a.exe', 'PS1', 'a' * 32)
        bibliotheque.register_game(game['path'])
        original = os.rename
        def cross_volume(src, dst):
            if Path(src) == game['path']: raise OSError(errno.EXDEV, 'cross-device')
            return original(src, dst)
        class Digest:
            def __init__(self, value): self.value = value
            def digest(self): return self.value
        with patch.object(storage.os, 'rename', side_effect=cross_volume), \
             patch.object(storage.hashlib, 'file_digest', side_effect=[Digest(b'one'), Digest(b'two')]):
            with self.assertRaises(OSError): storage.move_games([game], self.old, self.new)
        self.assertTrue(game['path'].is_file())
        self.assertFalse((self.new / 'PS1' / 'a.exe').exists())
        self.assertEqual(bibliotheque.registered_games(), [game['path']])

    def test_delete_checks_identity_and_moves_only_executable(self):
        game = game_file(self.old / 'a.exe', 'PS1', 'a' * 32)
        save = self.root / 'save.vmu'; save.write_bytes(b'saved')
        bibliotheque.register_game(game['path'])
        def recycle(*args, **kwargs):
            self.assertEqual(Path(kwargs['env']['PHANTOM_RECYCLE_GAME']), game['path'])
            game['path'].unlink()
            return subprocess_result
        class Result:
            returncode = 0; stderr = ''; stdout = ''
        subprocess_result = Result()
        with patch.object(storage.subprocess, 'run', side_effect=recycle): storage.recycle_game(game)
        self.assertFalse(game['path'].exists()); self.assertEqual(save.read_bytes(), b'saved')
        self.assertEqual(bibliotheque.registered_games(), [])

    def test_new_folder_inside_old_folder_moves_games_safely(self):
        game = game_file(self.old / 'a.exe', 'PS1', 'a' * 32)
        storage.move_games([game], self.old, self.old / 'new')
        self.assertFalse(game['path'].exists())
        self.assertTrue((self.old / 'new' / 'PS1' / 'a.exe').is_file())


if __name__ == '__main__': unittest.main()
