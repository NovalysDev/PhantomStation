import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
LAB = ROOT / 'test-local/dreamcast-research'
CLANG = Path(os.environ.get('PHANTOM_TEST_CLANG', ROOT / 'toolchain/bin/clang.exe'))


@unittest.skipUnless(os.name == 'nt' and CLANG.is_file(), 'Windows and Clang required')
class LearnedVariantTests(unittest.TestCase):
    def test_same_address_with_different_bytes_is_captured_twice(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory); executable = folder / 'test.exe'
            subprocess.run([str(CLANG), '-O0', '-I' + str(LAB / 'dcrecomp/include'),
                            str(ROOT / 'tests/test_dreamcast_learn_variants.c'),
                            str(LAB / 'pal-prototype/learn.c'), '-o', str(executable)],
                           check=True, capture_output=True)
            env = os.environ.copy(); env['PHANTOM_DC_LEARN_DIR'] = str(folder / 'captures')
            subprocess.run([str(executable)], env=env, check=True, capture_output=True)
            captured = folder / 'captures'
            self.assertEqual(len((captured / 'appels.txt').read_text().splitlines()), 2)
            first = (captured / 'bloc-000.bin').read_bytes()
            second = (captured / 'bloc-001.bin').read_bytes()
            self.assertEqual(len(first), 0x60000)
            self.assertEqual(len(second), 0x60000)
            self.assertNotEqual(first[0x20000], second[0x20000])


if __name__ == '__main__': unittest.main()
