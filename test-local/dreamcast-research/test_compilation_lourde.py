"""Fonctions traduites géantes : chacune seule dans un fichier compilé en -O0,
avec des sauts terminaux garantis (musttail), et une commande qui dépasse son
délai est arrêtée avec toute sa descendance.

Cas réel : Dino Crisis (26/11/2026), une fonction à plusieurs entrées de 9 Mo
de C ; clang -O1 y passait des heures et un clang orphelin bloquait le lot.
Le code ci-dessous est écrit à la main dans la forme du traducteur.
"""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

LAB = Path(__file__).resolve().parent
ROOT = LAB.parents[1]
spec = importlib.util.spec_from_file_location('sr_lourd', LAB / 'dcrecomp/tools/static_recompile.py')
sr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sr)
sys.path.insert(0, str(LAB))
import build_pal_prototype  # noqa: E402

CODE = '''static void impl_8C000100(SH4CPU *cpu, uint32_t entry) {
    switch (entry) { case 0x8C000104u: goto L_8C000104; default: break; }
    cpu->r[0] = 1;
    if (cpu->sr & SR_T) { func_8C000200(cpu); return; } /* bt tail */
L_8C000104:
    func_8C000300(cpu);
    sh4_jump_indirect(cpu);
    return;
}

void func_8C000100(SH4CPU *cpu) { impl_8C000100(cpu, 0x8C000100u); }
void func_8C000104(SH4CPU *cpu) { impl_8C000100(cpu, 0x8C000104u); }

void func_8C000400(SH4CPU *cpu) {
    cpu->r[1] = 2;
    func_8C000200(cpu); return; /* bra tail */
}
'''

ENTETE = '''#include <stdint.h>
typedef struct { uint32_t r[16]; uint32_t sr; } SH4CPU;
#define SR_T 1u
void func_8C000200(SH4CPU *cpu);
void func_8C000300(SH4CPU *cpu);
void sh4_jump_indirect(SH4CPU *cpu);
'''


class FonctionsGeantes(unittest.TestCase):
    def test_sauts_garantis(self):
        texte = sr.SH4Recompiler.guaranteed_tail_calls(CODE)
        self.assertIn('static void impl_8C000100(SH4CPU *cpu) {\n    uint32_t entry = phantom_entree_lourde;', texte)
        self.assertIn('{ phantom_entree_lourde = 0x8C000104u; __attribute__((musttail)) return impl_8C000100(cpu); }', texte)
        self.assertIn('__attribute__((musttail)) return func_8C000200(cpu); } /* bt tail */', texte)
        self.assertIn('__attribute__((musttail)) return sh4_jump_indirect(cpu);', texte)
        self.assertIn('    func_8C000300(cpu);\n', texte)            # un vrai appel reste un appel
        with tempfile.TemporaryDirectory(prefix='phantom-lourd-') as tmp:
            c = Path(tmp) / 'lourd.c'
            c.write_text(ENTETE + texte, encoding='utf-8')
            env = os.environ.copy(); env['PATH'] = str(ROOT / 'toolchain/bin') + os.pathsep + env['PATH']
            subprocess.run([str(ROOT / 'toolchain/bin/clang.exe'), '-std=gnu11', '-O1', '-O0', '-g0', '-w', '-c', str(c),
                            '-o', str(Path(tmp) / 'lourd.o')], env=env, check=True, capture_output=True, text=True)
            dis = subprocess.run([str(ROOT / 'toolchain/bin/llvm-objdump.exe'), '-d', '-r', str(Path(tmp) / 'lourd.o')],
                                 capture_output=True, text=True).stdout.splitlines()
        cibles = {}
        for i, ligne in enumerate(dis[:-1]):
            for instr in ('jmp', 'call'):
                if '\t' + instr in ligne:
                    for nom in ('impl_8C000100', 'func_8C000200', 'func_8C000300', 'sh4_jump_indirect'):
                        if dis[i + 1].rstrip().endswith(nom):
                            cibles.setdefault(nom, set()).add(instr)
        self.assertEqual(cibles.get('impl_8C000100'), {'jmp'})       # portes d'entrée : sauts
        self.assertEqual(cibles.get('func_8C000200'), {'jmp'})       # sauts terminaux
        self.assertEqual(cibles.get('sh4_jump_indirect'), {'jmp'})
        self.assertEqual(cibles.get('func_8C000300'), {'call'})      # appel ordinaire

    def test_fichier_a_part(self):
        rec = sr.SH4Recompiler.__new__(sr.SH4Recompiler)
        rec.functions = {0x8C000100: None, 0x8C000400: None}
        geant = CODE + '\n/* ' + 'x' * (sr.SH4Recompiler.GIANT_FUNCTION_CHARS + 10) + ' */\n'
        rec.recompile_function = lambda addr, info, next_func_addr=None: geant if addr == 0x8C000100 else 'void func_8C000400(SH4CPU *cpu) { }\n'
        with tempfile.TemporaryDirectory(prefix='phantom-lourd-') as tmp:
            rec.recompile_all(tmp)
            noms = sorted(os.listdir(tmp))
            self.assertIn('game_code_000_lourd_8C000100.c', noms)
            self.assertEqual(Path(tmp, 'compilation-legere.txt').read_text().split(), ['game_code_000_lourd_8C000100.c'])
            self.assertNotIn('impl_8C000100', Path(tmp, 'game_code_000.c').read_text())


class DelaiDepasse(unittest.TestCase):
    def test_descendance_arretee(self):
        script = ('import subprocess, sys, time; '
                  'subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"]); time.sleep(60)')
        debut = time.time()
        with self.assertRaises(RuntimeError) as erreur:
            build_pal_prototype.run_command([sys.executable, '-c', script], env=os.environ.copy(), timeout=3, on_line=lambda l: None)
        self.assertIn('Délai dépassé', str(erreur.exception))
        self.assertLess(time.time() - debut, 30)


if __name__ == '__main__':
    unittest.main()
