"""Repérage de mwSfdGetStat (Sofdec) et enveloppe de sa traduction.

Faux programme SH-4 écrit à la main ; aucun fichier de jeu n'est utilisé.
"""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sofdec_cinema as sc

BASE = sc.BASE


def _code(*ops):
    return b''.join(op.to_bytes(2, 'little') for op in ops)


def _programme(prefixe=b'\xff'):
    """f0 (rts), puis mwSfdGetStat en 0x8C010010, qui lit son message par mov.l @(disp,PC)."""
    corps = bytearray(_code(0x2FE6, 0x6E43, 0x000B, 0x0009) + _code(0x0009) * 4)       # f0 + bourrage
    assert len(corps) == 16
    getstat = _code(0x2FE6, 0x6E43, 0x4F22, 0x2EE8, 0xD301, 0x0009, 0x000B, 0x6EF6)    # D301 : mov.l @(4,PC),r3
    corps += getstat
    # D301 est en 0x8C010018 : elle lit ((0x8C010018+4)&~3) + 4 = 0x8C010020, le réservoir aligné qui suit.
    while len(corps) % 4: corps += b'\0'
    reservoir = len(corps)
    corps += b'\0\0\0\0'
    message = len(corps)
    corps += prefixe + b'W2004 mwSfdGetStat: handle is NULL\0'
    debut_message = message + len(prefixe)
    corps[reservoir:reservoir + 4] = (BASE + debut_message).to_bytes(4, 'little')
    return bytes(corps), reservoir


class SofdecCinema(unittest.TestCase):
    def test_trouve_la_fonction(self):
        boot, reservoir = _programme()
        # L'instruction mov.l doit lire ce réservoir : on la place pour qu'elle le désigne.
        self.assertEqual(reservoir, 0x20)
        self.assertEqual(sc.trouver(boot), BASE + 0x10)

    def test_prefixe_nul(self):
        boot, _ = _programme(b'\0')
        self.assertEqual(sc.trouver(boot), BASE + 0x10)

    def test_sans_sofdec(self):
        self.assertIsNone(sc.trouver(b'\x09\x00' * 64))
        self.assertEqual(sc.entrees(b'\x09\x00' * 64), set())

    def test_enveloppe(self):
        boot, _ = _programme()
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / 'src' / 'game'; src.mkdir(parents=True)
            f = src / 'game_code_000.c'
            f.write_text('#include "game/game_functions.h"\nvoid func_8C010010(SH4CPU *cpu) {\n    cpu->r[0] = 2u;\n}\n', encoding='utf-8')
            self.assertEqual(sc.apply(Path(tmp), boot, log=lambda _: None), 1)
            texte = f.read_text(encoding='utf-8')
            self.assertIn('void sofdec_etat_8C010010(SH4CPU *cpu) {', texte)
            self.assertIn('if (cpu->r[0] == 2u && dc_cinematic_buttons()) cpu->r[0] = 3u;', texte)
            self.assertTrue(texte.startswith(sc.ENTETE))
            self.assertEqual(sc.apply(Path(tmp), boot, log=lambda _: None), 1)   # pas deux fois
            self.assertEqual(f.read_text(encoding='utf-8'), texte)


if __name__ == '__main__':
    unittest.main()
