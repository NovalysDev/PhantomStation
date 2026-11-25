"""Saturations du SH-4 dans le code traduit : FTRC hors limites, MAC.W et
MAC.L avec SR.S = 1.

Instructions écrites à la main ; aucune donnée de jeu. Le cas de FTRC vient
du décodeur de voix de Grandia II : en 16.16, une crête à peine au-dessus de
+32767 sortait à -32768 (un claquement) au lieu d'être écrêtée à +32767.
"""
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

LAB = Path(__file__).resolve().parent
ROOT = LAB.parents[1]
spec = importlib.util.spec_from_file_location('sh4_recomp_sat', LAB / 'dcrecomp/tools/static_recompile.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
BASE = 0x8C010000

ENTETE = r'''#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include "recompiler/sh4_cpu.h"
static uint8_t mem[64];
static uint8_t *place(uint32_t a) { if (a >= 0x8C000000u && a < 0x8C000000u + sizeof mem) return mem + (a - 0x8C000000u); exit(3); }
uint16_t sh4_read16(SH4CPU *cpu, uint32_t a) { uint16_t v; memcpy(&v, place(a), 2); return v; }
uint32_t sh4_read32(SH4CPU *cpu, uint32_t a) { uint32_t v; memcpy(&v, place(a), 4); return v; }
'''


def instruction(op):
    return module.SH4Recompiler(b'\0\0', BASE).recompile_instruction(op, BASE)[0]


def executer(corps):
    with tempfile.TemporaryDirectory(prefix='phantom-sat-') as tmp:
        c = Path(tmp) / 'essai.c'
        c.write_text(ENTETE + '\nint main(void) {\n' + corps + '\n    return 0;\n}\n', encoding='utf-8')
        env = os.environ.copy()
        env['PATH'] = str(ROOT / 'toolchain/bin') + os.pathsep + env['PATH']
        subprocess.run([str(ROOT / 'toolchain/bin/clang.exe'), '-std=c11', '-O1', '-w',
                        '-I', str(LAB / 'dcrecomp/include'), str(c), '-o', str(Path(tmp) / 'essai.exe')],
                       env=env, check=True, capture_output=True, text=True)
        return subprocess.run([str(Path(tmp) / 'essai.exe')], capture_output=True, text=True, timeout=30)


class Saturations(unittest.TestCase):
    def test_ftrc_simple(self):
        ftrc = instruction(0xF13D)              # ftrc fr1, fpul
        corps = f'''
    SH4CPU c; SH4CPU *cpu = &c; memset(&c, 0, sizeof c);
    const float entree[] = {{ 33000.0f * 65536.0f, 3e9f, -3e9f, 12345.7f, -12345.7f, 32767.5f * 65536.0f }};
    const uint32_t attendu[] = {{ 0x7FFFFFFFu, 0x7FFFFFFFu, 0x80000000u, 12345u, (uint32_t)-12345, 0x7FFF8000u }};
    for (int i = 0; i < 6; i++) {{
        c.fr[1] = entree[i];
        {ftrc}
        if (c.fpul != attendu[i]) {{ printf("ftrc %d : %08X\\n", i, c.fpul); return 1; }}
    }}
    c.fr[1] = 33000.0f * 65536.0f;
    {ftrc}
    if ((int16_t)(c.fpul >> 16) != 32767) {{ printf("echantillon %d\\n", (int16_t)(c.fpul >> 16)); return 2; }}'''
        fini = executer(corps)
        self.assertEqual(fini.returncode, 0, fini.stdout + fini.stderr)

    def test_mac_w_sature(self):
        mac = instruction(0x421F)               # mac.w @r1+, @r2+
        corps = f'''
    SH4CPU c; SH4CPU *cpu = &c; memset(&c, 0, sizeof c);
    int16_t v = 32767; memcpy(mem, &v, 2); memcpy(mem + 2, &v, 2);
    /* S = 1 : MACL seul, saturé à 32 bits, MACH inchangé. */
    c.sr = SR_S; c.r[1] = 0x8C000000u; c.r[2] = 0x8C000002u; c.macl = 0x7FF00000u; c.mach = 0x12345678u;
    {mac}
    if (c.macl != 0x7FFFFFFFu || c.mach != 0x12345678u) {{ printf("S=1 : %08X %08X\\n", c.mach, c.macl); return 1; }}
    if (c.r[1] != 0x8C000002u || c.r[2] != 0x8C000004u) return 2;
    /* S = 0 : addition sur 64 bits, comme avant. */
    c.sr = 0; c.r[1] = 0x8C000000u; c.r[2] = 0x8C000002u; c.macl = 0x7FF00000u; c.mach = 0;
    {mac}
    if (c.mach != 0 || c.macl != 0x7FF00000u + 1073676289u) {{ printf("S=0 : %08X %08X\\n", c.mach, c.macl); return 3; }}'''
        fini = executer(corps)
        self.assertEqual(fini.returncode, 0, fini.stdout + fini.stderr)

    def test_mac_l_sature(self):
        mac = instruction(0x021F)               # mac.l @r1+, @r2+
        corps = f'''
    SH4CPU c; SH4CPU *cpu = &c; memset(&c, 0, sizeof c);
    int32_t v = 0x7FFFFFFF; memcpy(mem, &v, 4); memcpy(mem + 4, &v, 4);
    c.sr = SR_S; c.r[1] = 0x8C000000u; c.r[2] = 0x8C000004u;
    {mac}
    if (c.mach != 0x00007FFFu || c.macl != 0xFFFFFFFFu) {{ printf("S=1 : %08X %08X\\n", c.mach, c.macl); return 1; }}
    c.sr = 0; c.r[1] = 0x8C000000u; c.r[2] = 0x8C000004u; c.mach = 0; c.macl = 0;
    {mac}
    if (c.mach != 0x3FFFFFFFu || c.macl != 0x00000001u) {{ printf("S=0 : %08X %08X\\n", c.mach, c.macl); return 2; }}'''
        fini = executer(corps)
        self.assertEqual(fini.returncode, 0, fini.stdout + fini.stderr)


if __name__ == '__main__':
    unittest.main()
