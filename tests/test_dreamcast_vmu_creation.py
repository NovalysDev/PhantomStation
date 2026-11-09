"""VMU creation must never replace a saved file or a directory."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import cartes_memoire

ROOT = Path(__file__).resolve().parent.parent
LAB = ROOT / 'test-local' / 'dreamcast-research' / 'dcrecomp'


@unittest.skipUnless(os.name == 'nt', 'Windows launcher and runtime')
class DreamcastVmuCreationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='phantom-test-vmu-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def compile_launcher_helper(self):
        code = self.root / 'helper.cs'
        code.write_text('using System; class Test { static void Main(string[] a) '
                        '{ Console.WriteLine(PhantomDreamcastVmu.Prepare(a[0])); } }', encoding='utf-8')
        exe = self.root / 'helper.exe'
        csc = Path(os.environ['WINDIR']) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
        subprocess.run([str(csc), '/nologo', '/out:' + str(exe), str(code),
                        str(ROOT / 'phantom_dreamcast_vmu.cs')], check=True, capture_output=True)
        return exe

    def test_old_directory_is_preserved_and_blank_card_is_usable(self):
        exe = self.compile_launcher_helper()
        saves = self.root / 'saves'; saves.mkdir()
        old = saves / 'carte-dreamcast.vmu'; old.mkdir()
        keep = old / 'keep.txt'; keep.write_bytes(b'user data')
        result = Path(subprocess.check_output([str(exe), str(saves)], text=True).strip())
        self.assertEqual(result.name, 'carte-dreamcast-reparee.vmu')
        self.assertEqual(result.read_bytes(), cartes_memoire.blank_vmu())
        self.assertEqual(keep.read_bytes(), b'user data')
        result.write_bytes(b'pre-existing data')
        run = subprocess.run([str(exe), str(saves)], capture_output=True)
        self.assertNotEqual(run.returncode, 0)
        self.assertEqual(result.read_bytes(), b'pre-existing data')

    def test_native_engine_creates_and_reopens_file_in_nested_directory(self):
        code = self.root / 'test.c'; exe = self.root / 'test.exe'
        code.write_text('#include "hal/dc_vmu.h"\n'
                        'int wmain(int n,wchar_t **a) { '
                        'if(n!=2 || !dc_vmu_open_w(a[1])) return 1; '
                        'dc_vmu_close(); return dc_vmu_open_w(a[1]) ? 0 : 2; }', encoding='utf-8')
        compiler = ROOT / 'toolchain/bin/clang.exe'
        subprocess.run([str(compiler), '-municode', '-I', str(LAB / 'include'), str(code),
                        str(LAB / 'src/hal/dc_vmu.c'), '-o', str(exe)], check=True, capture_output=True)
        card = self.root / 'dossier accentué' / 'nested' / 'card.vmu'
        subprocess.run([str(exe), str(card)], check=True, capture_output=True)
        self.assertTrue(card.is_file())
        self.assertEqual(card.read_bytes(), cartes_memoire.blank_vmu())
        invalid = self.root / 'ne-pas-ecraser.vmu'
        invalid.write_bytes(b'sauvegarde existante')
        result = subprocess.run([str(exe), str(invalid)], capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(invalid.read_bytes(), b'sauvegarde existante')


if __name__ == '__main__':
    unittest.main()
