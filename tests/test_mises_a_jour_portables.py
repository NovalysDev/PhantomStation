import hashlib
import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import phantom_updates as updates


def make_archive(files):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    data = output.getvalue()
    manifest = {'version': '1.0.2', 'taille': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
    return manifest, data


class PortableUpdateTest(unittest.TestCase):
    def test_new_console_files_update_without_touching_user_data(self):
        files = {name: b'new version' for name in (
            'detection_doublons.py', 'phantom_compiler_game.py', 'phantom_embedded_game.cs',
            'phantom_n64_rom.py', 'phantom_nintendo.py', 'phantom_nintendo_launcher.cs')}
        manifest, data = make_archive(files)
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            (base / 'phantom_nintendo.py').write_bytes(b'old version')
            for name in ('sauvegardes/jeu.mcd', 'projets/jeu.bin', 'assets/image-personnelle.png'):
                path = base / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'user data')
            updates.install_update(manifest, data, base)
            for name, content in files.items():
                self.assertEqual((base / name).read_bytes(), content)
            for name in ('sauvegardes/jeu.mcd', 'projets/jeu.bin', 'assets/image-personnelle.png'):
                self.assertEqual((base / name).read_bytes(), b'user data')
            self.assertEqual(json.loads((base / 'phantom_version.json').read_text())['version'], '1.0.2')

    def test_disallowed_paths_reject_entire_update(self):
        for name in ('../escape.py', 'projets/jeu.bin', 'sauvegardes/jeu.mcd', 'assets/image-personnelle.png'):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as folder:
                base = Path(folder)
                (base / 'lanceur.py').write_bytes(b'old version')
                manifest, data = make_archive({'lanceur.py': b'new version', name: b'bad'})
                with self.assertRaises(updates.UpdateError):
                    updates.install_update(manifest, data, base)
                self.assertEqual((base / 'lanceur.py').read_bytes(), b'old version')

    def test_digest_mismatch_is_rejected(self):
        manifest, data = make_archive({'phantom_nintendo.py': b'new version'})
        manifest['sha256'] = '0' * 64
        with tempfile.TemporaryDirectory() as folder, self.assertRaises(updates.UpdateError):
            updates.install_update(manifest, data, folder)


if __name__ == '__main__':
    unittest.main()
