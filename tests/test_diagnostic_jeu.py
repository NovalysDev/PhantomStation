import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from diagnostic_jeu import create_game_diagnostic
from rapport_erreur import submission_payload

class GameDiagnosticTests(unittest.TestCase):
    def test_captures_only_latest_matching_game_and_preserves_errors(self):
        with tempfile.TemporaryDirectory(prefix='phantom-diag-') as d:
            root = Path(d); logs = root/'logs'; reports = root/'rapports'; ident='1'*32
            one=logs/('Jeu - '+ident);two=logs/('Autre - '+'2'*32);one.mkdir(parents=True);two.mkdir()
            (one/'journal.log').write_text('boss absent\nerreur graphique',encoding='utf8')
            (two/'journal.log').write_text('autre jeu',encoding='utf8')
            path, text = create_game_diagnostic({'id':ident,'name':'Mon jeu','platform':'DC'},'Boss invisible au niveau 1',logs,reports)
            with zipfile.ZipFile(path) as z:
                self.assertEqual(set(z.namelist()), {'rapport.txt','journal.log'})
                self.assertEqual(z.read('journal.log').replace(b'\r\n',b'\n'), b'boss absent\nerreur graphique')
                self.assertIn(b'Boss invisible',z.read('rapport.txt'))
            self.assertIn('boss absent', text)
            self.assertEqual(submission_payload('Mon jeu', text, 'jeu')[0]['subject'], 'Rapport de jeu - Mon jeu')

    def test_no_log_and_invalid_identity(self):
        with tempfile.TemporaryDirectory(prefix='phantom-diag-') as d:
            with self.assertRaises(ValueError):
                create_game_diagnostic({'id':'3'*32,'name':'Jeu'},'Image figée',Path(d)/'missing',Path(d)/'reports')
            with self.assertRaises(ValueError):create_game_diagnostic({'id':'../bad'},'Image figée',Path(d),Path(d))

if __name__ == '__main__':unittest.main()
