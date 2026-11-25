"""Branches de `switch` compilées par GCC avec une table d'octets non doublés.

Faux programme SH-4 écrit à la main ; aucun fichier de jeu n'est utilisé.
Re-Volt a 29 tables de cette forme ; lues doublées, leurs branches n'étaient
jamais traduites et chacune arrêtait le jeu la première fois qu'elle servait.
"""
import importlib.util
from pathlib import Path
import struct
import unittest

LAB = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('sh4_recomp_tables', LAB / 'dcrecomp/tools/static_recompile.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
BASE = 0x8C010000


def programme(entre_chargement_et_saut, octets):
    """mova ; mov.b @(r0,r2),r1 ; [...] ; braf r1 ; nop, puis la table et quatre branches."""
    debut = [0xC702, 0x012C] + entre_chargement_et_saut + [0x0123, 0x0009]
    assert len(debut) * 2 <= 0x0C
    while len(debut) * 2 < 0x0C:
        debut.append(0x0009)             # mova en BASE : la table est en BASE + 0x0C
    corps = struct.pack('<' + 'H' * len(debut), *debut) + bytes(octets)
    while len(corps) < 0x10:
        corps += b'\0'
    corps += struct.pack('<8H', 0x000B, 0x0009, 0x000B, 0x0009, 0x000B, 0x0009, 0x000B, 0x0009)
    return corps


class TablesSwitch(unittest.TestCase):
    def test_octets_non_doubles(self):
        # braf en BASE+6 : les écarts partent de BASE+0x0A ; branches en 0x10..0x1C.
        code = programme([0x611C], [0x06, 0x0A, 0x0E, 0x12])     # extu.b r1,r1
        r = module.SH4Recompiler(code, BASE)
        cibles = r._byte_jump_table_targets()
        base = BASE + 0x0A
        self.assertEqual(cibles, {base + 0x06, base + 0x0A, base + 0x0E, base + 0x12})

    def test_table_arretee_a_la_premiere_branche(self):
        # Les octets qui suivent la table sont du code : ils ne sont pas lus.
        code = programme([0x611C], [0x06, 0x0A, 0x0E, 0x12])
        r = module.SH4Recompiler(code, BASE)
        self.assertEqual(len(r._byte_jump_table_targets()), 4)

    def test_table_doublee_laissee_a_l_ancienne_lecture(self):
        code = programme([0x611C, 0x311C], [0x02, 0x04, 0x06, 0x08])   # add r1,r1
        r = module.SH4Recompiler(code, BASE)
        self.assertEqual(r._byte_jump_table_targets(), set())

    def test_ancienne_lecture_inchangee(self):
        # Les jeux qui marchent ont été traduits avec : elle ne doit pas bouger.
        code = programme([0x611C], [0x06, 0x0A, 0x0E, 0x12])
        r = module.SH4Recompiler(code, BASE)
        base = BASE + 0x0A
        self.assertIn(base + 0x0C, r._jump_table_targets())      # 0x06 doublé


if __name__ == '__main__':
    unittest.main()
