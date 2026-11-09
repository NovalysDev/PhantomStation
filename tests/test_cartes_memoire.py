import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import bibliotheque
import cartes_memoire as cards


class CardSelectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='phantom-test-cartes-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.documents = patch.object(bibliotheque, 'documents_folder', return_value=self.base)
        self.config = patch.object(cards, 'CONFIG', self.base / 'selection.json')
        self.documents.start(); self.config.start()
        self.addCleanup(self.documents.stop); self.addCleanup(self.config.stop)

    def make_game(self, platform, marker, game_id='a'*32, extra=None):
        path = self.base / (game_id + '.exe')
        with zipfile.ZipFile(path, 'w') as z:
            z.writestr('phantom-package.txt', game_id + '\nruntime.exe\n')
            z.writestr('game.toml', f'[game]\nname="test"\nplatform="{platform}"\n')
            z.writestr('runtime.exe', b'prefix' + marker + b'suffix')
            if extra: z.writestr(extra, b'example')
        return bibliotheque.game_info(path, include_cover=False)

    def test_create_select_and_reuse_cards_for_each_console(self):
        for console, marker, env_key in [('PS1', b'PHANTOM_SELECTED_PS1_CARD2', 'PHANTOM_SELECTED_PS1_CARD1'),
                                         ('PS2', b'--memcard', 'PHANTOM_SELECTED_PS2_CARD'),
                                         ('DC', 'PHANTOM_DC_VMU'.encode('utf-16le'), 'PHANTOM_SELECTED_DC_CARD')]:
            game = self.make_game(console, marker, game_id={'PS1':'a', 'PS2':'b', 'DC':'c'}[console]*32)
            self.assertEqual(cards.launch_environment(game), {})
            card = cards.create(console, 'Ma carte')
            cards.select(console, card['id'])
            env = cards.launch_environment(game)
            self.assertIn(env_key, env)
            self.assertEqual(cards.selected(console)['id'], card['id'])
            target = card['directory'] / cards.CARDS[console]
            cards._validate_card(console, target)
            if console == 'DC':
                blob = target.read_bytes()
                self.assertEqual(blob[255*512:255*512+16], b'\x55'*16)
                self.assertEqual(int.from_bytes(blob[254*512+254*2:254*512+254*2+2], 'little'), 0xfffa)
            cards.select(console, None)
            self.assertEqual(cards.launch_environment(game), {})

    def test_import_copies_card_without_modifying_source(self):
        source = self.base / 'ma carte.mcd'; source.write_bytes(bibliotheque.blank_card())
        original = source.read_bytes()
        card = cards.create('PS1', 'Import', source)
        self.assertEqual(source.read_bytes(), original)
        self.assertEqual((card['directory'] / 'card1.mcd').read_bytes(), original)

    def test_rename_keeps_card_contents_selection_and_original_file(self):
        source = self.base / 'source.mcd'
        source.write_bytes(bibliotheque.blank_card())
        card = cards.create('PS1', 'Ancien nom', source)
        cards.select('PS1', card['id'], 2)
        target = card['directory'] / cards.CARDS['PS1']
        saved = target.read_bytes()
        renamed = cards.rename('PS1', card['id'], '  Mes sauvegardes  ')
        self.assertEqual(renamed['nom'], 'Mes sauvegardes')
        self.assertEqual(cards.selected('PS1', 2)['id'], card['id'])
        self.assertEqual(cards.selected('PS1', 2)['nom'], 'Mes sauvegardes')
        self.assertEqual(target.read_bytes(), saved)
        self.assertEqual(source.read_bytes(), saved)
        cards.create('PS1', 'Autre carte')
        with self.assertRaises(ValueError): cards.rename('PS1', card['id'], 'autre CARTE')
        with self.assertRaises(ValueError): cards.rename('PS1', card['id'], '  ')
        self.assertEqual(cards.selected('PS1', 2)['nom'], 'Mes sauvegardes')

    def test_rename_nintendo_profile_keeps_saved_files(self):
        profile = cards.create('NES', 'Ancien profil')
        cards.select('NES', profile['id'])
        game = self.make_game('NES', b'PHANTOM_RUNTIME_DATA_ROOT')
        save = cards.profile_dir(game) / 'partie.sav'
        save.parent.mkdir(parents=True)
        save.write_bytes(b'ma partie')
        cards.rename('NES', profile['id'], 'Joueur 1')
        self.assertEqual(cards.selected('NES')['nom'], 'Joueur 1')
        self.assertEqual(cards.profile_dir(game) / 'partie.sav', save)
        self.assertEqual(save.read_bytes(), b'ma partie')

    def test_rename_default_is_only_a_label_and_keeps_existing_saves(self):
        game = self.make_game('NES', b'PHANTOM_RUNTIME_DATA_ROOT')
        folder = bibliotheque.save_directory(game)
        folder.mkdir(parents=True, exist_ok=True)
        save = folder / 'partie.sav'; save.write_bytes(b'ma partie')
        cards.rename_default('NES', 1, '  Mon jeu  ')
        self.assertEqual(cards.default_label('NES'), 'Mon jeu')
        self.assertIsNone(cards.selected('NES'))
        self.assertEqual(bibliotheque.save_directory(game), folder)
        self.assertEqual(save.read_bytes(), b'ma partie')
        with self.assertRaises(ValueError): cards.rename_default('PS2', 2, 'Vide')

    def test_two_independent_ps1_cards(self):
        game = self.make_game('PS1', 'PHANTOM_SELECTED_PS1_CARD2'.encode('utf-16le'))
        first = cards.create('PS1', 'Première')
        second = cards.create('PS1', 'Deuxième')
        cards.select('PS1', second['id'], 2)
        env = cards.launch_environment(game)
        self.assertNotIn('PHANTOM_SELECTED_PS1_CARD1', env)
        self.assertEqual(Path(env['PHANTOM_SELECTED_PS1_CARD2']), second['directory'] / 'card1.mcd')
        with self.assertRaises(ValueError): cards.select('PS1', second['id'], 1)
        cards.select('PS1', first['id'], 1)
        env = cards.launch_environment(game)
        self.assertEqual(Path(env['PHANTOM_SELECTED_PS1_CARD1']), first['directory'] / 'card1.mcd')
        self.assertEqual(Path(env['PHANTOM_SELECTED_PS1_CARD2']), second['directory'] / 'card1.mcd')
        self.assertTrue(cards.backup_selected('PS1', 1).is_file())
        self.assertTrue(cards.backup_selected('PS1', 2).is_file())
        cards.select('PS1', None, 2)
        self.assertEqual(set(cards.launch_environment(game)), {'PHANTOM_SELECTED_PS1_CARD1'})

    def test_ps1_extension_is_preserved(self):
        source = self.base / 'extension.mcd'
        source.write_bytes(bibliotheque.blank_card() + b'extra')
        card = cards.create('PS1', 'Extension', source)
        cards.select('PS1', card['id'])
        self.assertEqual(cards.backup_selected('PS1').read_bytes(), source.read_bytes())

    def test_two_independent_ps2_cards_and_old_engine_guard(self):
        game = self.make_game('PS2', b'--memcard PHANTOM_SELECTED_PS2_CARD2')
        legacy = self.make_game('PS2', b'--memcard', 'c'*32)
        first = cards.create('PS2', 'Première')
        second = cards.create('PS2', 'Deuxième')
        cards.select('PS2', second['id'], 2)
        env = cards.launch_environment(game)
        self.assertEqual(set(env), {'PHANTOM_SELECTED_PS2_CARD2'})
        with self.assertRaises(ValueError): cards.launch_environment(legacy)
        with self.assertRaises(ValueError): cards.select('PS2', second['id'], 1)
        cards.select('PS2', first['id'], 1)
        env = cards.launch_environment(game)
        self.assertEqual(Path(env['PHANTOM_SELECTED_PS2_CARD']), first['directory'] / 'carte.ps2')
        self.assertEqual(Path(env['PHANTOM_SELECTED_PS2_CARD2']), second['directory'] / 'carte.ps2')
        self.assertTrue(cards.backup_selected('PS2', 1).is_file())
        self.assertTrue(cards.backup_selected('PS2', 2).is_file())

    def test_invalid_card_and_duplicate_name_are_refused(self):
        source = self.base / 'bad.vmu'; source.write_bytes(b'bad')
        with self.assertRaises(ValueError): cards.create('DC', 'bad', source)
        cards.create('PS1', 'Famille')
        with self.assertRaises(ValueError): cards.create('PS1', 'famille')

    def test_nintendo_profiles_are_per_game_and_switch_back_to_default(self):
        a = self.make_game('NES', 'PHANTOM_RUNTIME_DATA_ROOT'.encode('utf-16le'), 'd'*32)
        b = self.make_game('NES', 'PHANTOM_RUNTIME_DATA_ROOT'.encode('utf-16le'), 'e'*32)
        profile = cards.create('NES', 'Joueur 1'); cards.select('NES', profile['id'])
        one = Path(cards.launch_environment(a)['PHANTOM_SELECTED_PROFILE_DIR'])
        two = Path(cards.launch_environment(b)['PHANTOM_SELECTED_PROFILE_DIR'])
        self.assertNotEqual(one, two)
        (one/'partie.sav').write_bytes(b'partie A')
        self.assertFalse((two/'partie.sav').exists())
        self.assertEqual(bibliotheque.save_directory(a), one)
        cards.select('NES', None)
        self.assertNotEqual(bibliotheque.save_directory(a), one)
        self.assertEqual((one/'partie.sav').read_bytes(), b'partie A')

    def test_old_games_are_refused_and_goldeneye_gets_its_own_profile(self):
        old = self.make_game('NES', b'legacy', 'f'*32)
        profile = cards.create('NES', 'Profil'); cards.select('NES', profile['id'])
        with self.assertRaises(ValueError): cards.launch_environment(old)
        golden = self.make_game('N64', b'legacy', '1'*32, 'data/ge007.pal-final.z64')
        p64 = cards.create('N64', 'Profil'); cards.select('N64', p64['id'])
        profile_path = Path(cards.launch_environment(golden)['PHANTOM_SELECTED_PROFILE_DIR'])
        self.assertTrue(profile_path.is_dir())
        self.assertTrue(profile_path.is_relative_to(p64['directory']))

    def test_shared_card_backup_and_missing_card(self):
        card = cards.create('DC', 'Partagée'); cards.select('DC', card['id'])
        path = card['directory'] / 'carte.vmu'
        first = cards.backup_selected('DC'); self.assertEqual(first.read_bytes(), path.read_bytes())
        self.assertIsNone(cards.backup_selected('DC'))
        payload = bytearray(path.read_bytes()); payload[0] = 17; path.write_bytes(payload)
        second = cards.backup_selected('DC'); self.assertNotEqual(first.read_bytes(), second.read_bytes())
        path.unlink()
        with self.assertRaises(ValueError): cards.backup_selected('DC')


if __name__ == '__main__': unittest.main()
