"""Mélangeur de la puce son et son processeur d'effets (DSP).

Compile et lance pal-prototype/aica_pcm_test.c et aica_dsp_test.c, écrits à
la main ; aucune donnée de jeu.
"""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

LAB = Path(__file__).resolve().parent
ROOT = LAB.parents[1]
CLANG = ROOT / 'toolchain/bin/clang.exe'


def compiler_et_lancer(sources):
    with tempfile.TemporaryDirectory(prefix='phantom-son-') as tmp:
        exe = Path(tmp) / 'essai.exe'
        env = os.environ.copy()
        env['PATH'] = str(ROOT / 'toolchain/bin') + os.pathsep + env['PATH']
        env.pop('PHANTOM_DC_DSP', None)
        subprocess.run([str(CLANG), '-std=c11', '-O1', '-w', '-I', str(LAB / 'dcrecomp/include'),
                        *map(str, sources), '-o', str(exe)], env=env, check=True, capture_output=True, text=True)
        return subprocess.run([str(exe)], env=env, capture_output=True, text=True, timeout=60)


class MelangeurSon(unittest.TestCase):
    def test_melangeur(self):
        fini = compiler_et_lancer([LAB / 'pal-prototype/aica_pcm_test.c', LAB / 'dcrecomp/src/hal/aica_pcm.c'])
        self.assertEqual(fini.returncode, 0, fini.stdout + fini.stderr)

    def test_dsp(self):
        fini = compiler_et_lancer([LAB / 'pal-prototype/aica_dsp_test.c'])
        self.assertEqual(fini.returncode, 0, fini.stdout + fini.stderr)
        self.assertIn('Essais du DSP reussis', fini.stdout)


if __name__ == '__main__':
    unittest.main()
