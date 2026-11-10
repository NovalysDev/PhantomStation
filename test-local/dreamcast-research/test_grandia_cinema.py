import tempfile
import unittest
from pathlib import Path
import grandia_cinema as hook


class GrandiaCinemaTest(unittest.TestCase):
    def test_only_the_expected_disc_and_call_site_are_modified(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            sources = root / 'src/game'
            sources.mkdir(parents=True)
            target = sources / 'game_code_L0123456789ab_000.c'
            target.write_text(hook.NEEDLE + '\n', encoding='utf8')
            self.assertEqual(hook.apply(root, 'other-disc'), 0)
            self.assertEqual(target.read_text(encoding='utf8'), hook.NEEDLE + '\n')
            self.assertEqual(hook.apply(root, hook.BOOT_SHA1), 1)
            result = target.read_text(encoding='utf8')
            self.assertIn(hook.HEADER, result)
            self.assertIn(hook.HOOK, result)
            self.assertEqual(hook.apply(root, hook.BOOT_SHA1), 0)

    def test_ambiguous_call_site_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            sources = root / 'src/game'
            sources.mkdir(parents=True)
            target = sources / 'game_code_L0123456789ab_000.c'
            target.write_text(hook.NEEDLE + '\n' + hook.NEEDLE, encoding='utf8')
            with self.assertRaises(ValueError):
                hook.apply(root, hook.BOOT_SHA1)
            self.assertNotIn(hook.HOOK, target.read_text(encoding='utf8'))


if __name__ == '__main__':
    unittest.main()
