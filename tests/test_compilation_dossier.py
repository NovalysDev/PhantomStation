"""Compilation d'un dossier : titres nettoyés, jeux trouvés, et enchaînement
des compilations dans l'interface (compilation simulée, sans vrai jeu)."""
import os
import queue
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / 'vendor' / 'interface'))
from compilation_dossier import clean_game_title, scan_folder  # noqa: E402


class Titres(unittest.TestCase):
    def test_mentions_retirees(self):
        self.assertEqual(clean_game_title('Re-Volt (Europe) (En,Fr,De,Es,It)'), 'Re-Volt')
        self.assertEqual(clean_game_title('Grandia II (Europe) (Disc 1)'), 'Grandia II')
        self.assertEqual(clean_game_title('Shenmue (1999) (Rev 1) [!]'), 'Shenmue')
        self.assertEqual(clean_game_title('Sonic_Adventure_(PAL)'), 'Sonic Adventure')
        self.assertEqual(clean_game_title('Legend of Zelda, The - Ocarina of Time (Europe)'),
                         'The Legend of Zelda - Ocarina of Time')
        self.assertEqual(clean_game_title('FIFA 2000 (Europe)'), 'FIFA 2000')       # année du titre gardée
        self.assertEqual(clean_game_title('(Europe)'), '(Europe)')                    # jamais de titre vide
        self.assertEqual(clean_game_title("Disney's 102 Dalmatians - Puppies to the Rescue (Europe)"), '102 Dalmatians - Puppies to the Rescue')
        self.assertEqual(clean_game_title('Disney-Pixar Toy Story 2 - Buzz (France)'), 'Toy Story 2 - Buzz')
        self.assertEqual(clean_game_title('Walt Disney World Quest - Magical Racing Tour (Europe)'), 'Walt Disney World Quest - Magical Racing Tour')

    def test_nom_suggere_pour_un_seul_jeu(self):
        import lanceur
        self.assertEqual(lanceur.game_name_from_file(r'C:\Jeux\Re-Volt (Europe) (En,Fr,De,Es,It).chd'), 'Re-Volt')
        self.assertEqual(lanceur.game_name_from_file('Tony Hawk\'s PS 2 (Europe).cue'), "Tony Hawk's 2")


class Recherche(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def fichier(self, nom, texte=''):
        p = self.d / nom
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(texte, encoding='utf-8')
        return p

    def test_ps1_disques_et_pistes(self):
        self.fichier('FF7 (Europe) (Disc 1).cue', 'FILE "FF7 (Europe) (Disc 1).bin" BINARY\n')
        self.fichier('FF7 (Europe) (Disc 1).bin')
        self.fichier('FF7 (Europe) (Disc 2).cue', 'FILE "FF7 (Europe) (Disc 2).bin" BINARY\n')
        self.fichier('FF7 (Europe) (Disc 2).bin')
        self.fichier('Rayman (Europe)/Rayman (Europe).cue', 'FILE "Rayman (Track 1).bin" BINARY\nFILE "Rayman (Track 2).bin" BINARY\n')
        self.fichier('Rayman (Europe)/Rayman (Track 1).bin')
        self.fichier('Rayman (Europe)/Rayman (Track 2).bin')
        self.fichier('Seul.bin')
        self.fichier('Archive.7z')
        self.fichier('a/b/c/Trop profond.cue')
        jeux, archives = scan_folder(self.d, 'PS1')
        titres = {t: [p.name for p in d] for t, d, c in jeux}
        self.assertEqual(titres['FF7'], ['FF7 (Europe) (Disc 1).cue', 'FF7 (Europe) (Disc 2).cue'])
        self.assertEqual(titres['Rayman'], ['Rayman (Europe).cue'])
        self.assertEqual(titres['Seul'], ['Seul.bin'])
        self.assertEqual(len(jeux), 3)
        self.assertEqual([p.name for p in archives], ['Archive.7z'])

    def test_rien_n_est_ecarte(self):
        self.fichier('Jeu PS2.iso'); self.fichier('Jeu PS1.iso')
        self.fichier('Crazy Taxi (Europe).chd'); self.fichier('Crazy Taxi (USA).chd')
        jeux, _ = scan_folder(self.d, 'PS1', lambda p: 'PS2' if 'PS2' in p.name else 'PS1')
        self.assertEqual(sorted((t, c) for t, d, c in jeux), [('Jeu PS1', 'PS1'), ('Jeu PS2', 'PS2')])   # compilé en PS2
        self.fichier('Shenmue (Europe) (Disc 1).chd'); self.fichier('Shenmue (Europe) (Disc 2).chd')
        jeux, _ = scan_folder(self.d, 'DC')
        # Titres nus ; deux versions = deux jeux ; les disques d'un jeu Dreamcast vont ensemble.
        self.assertEqual(sorted((t, [p.name for p in d]) for t, d, c in jeux),
                         [('Crazy Taxi', ['Crazy Taxi (Europe).chd']), ('Crazy Taxi', ['Crazy Taxi (USA).chd']),
                          ('Shenmue', ['Shenmue (Europe) (Disc 1).chd', 'Shenmue (Europe) (Disc 2).chd'])])
        # La PS2 compile chaque disque à part : aucun n'est oublié.
        self.fichier('GT (Europe) (Disc 1).iso'); self.fichier('GT (Europe) (Disc 2).iso')
        jeux, _ = scan_folder(self.d, 'PS2', lambda p: 'PS2')
        self.assertEqual(sorted(d[0].name for t, d, c in jeux if t == 'GT'), ['GT (Europe) (Disc 1).iso', 'GT (Europe) (Disc 2).iso'])

    def test_extraction_zip(self):
        import zipfile
        from compilation_dossier import extraire_archive
        with zipfile.ZipFile(self.d / 'Jeu.zip', 'w') as z:
            z.writestr('Jeu (Europe)/Jeu (Europe).chd', b'disque')
        extraire_archive(self.d / 'Jeu.zip', self.d / 'sortie')
        self.assertEqual((self.d / 'sortie' / 'Jeu (Europe)' / 'Jeu (Europe).chd').read_bytes(), b'disque')
        with zipfile.ZipFile(self.d / 'Piege.zip', 'w') as z:
            z.writestr('../dehors.chd', b'x')
        with self.assertRaises(OSError):
            extraire_archive(self.d / 'Piege.zip', self.d / 'sortie2')


class Variable:
    def __init__(self, valeur=''): self.valeur = valeur
    def get(self): return self.valeur
    def set(self, valeur): self.valeur = valeur


class Enchainement(unittest.TestCase):
    """L'interface compile les jeux du dossier l'un après l'autre, sans question
    par jeu, puis affiche un bilan."""

    def application(self):
        import lanceur
        app = lanceur.App.__new__(lanceur.App)
        app.root = MagicMock(); self.planifies = []
        app.root.after.side_effect = lambda ms, fn, *a: self.planifies.append(fn)
        for nom in ('start', 'choose', 'choose_folder', 'launch_button', 'project_button', 'output_button', 'icon_button',
                    'background_button', 'console_switch', 'disc_remove', 'disc_clear', 'library', 'log', 'report_shortcut'):
            setattr(app, nom, MagicMock())
        app.console, app.name, app.status, app.icon_text = Variable('DC'), Variable(), Variable(), Variable()
        app.discs, app.suggested_name, app.icon_path, app.background_path = [], '', None, None
        app.events, app.busy, app.batch, app.progress_window = queue.Queue(), False, None, None
        app.finished_game, app.finished_images, app.finished_missing_sbi, app.failed_attempt = None, ([], None), [], None
        app.output_dir = Path(tempfile.gettempdir())
        app.change_console = MagicMock(); app.refresh_discs = MagicMock(); app.append = MagicMock()
        app.lances = []
        def build():
            app.lances.append((app.name.get(), list(app.discs)))
            app.busy = True
            reussi = app.name.get() != 'Casse'
            app.events.put(('done', (reussi, 'Fini' if reussi else 'Échec', 'C:/Jeux/' + app.name.get() + '.exe' if reussi else None, [], None)))
        app.build = build
        app.attempt_report_context, app.attempt_log_start = ('', []), '1.0'
        return app, lanceur

    def test_dossier_complet(self):
        app, lanceur = self.application()
        self.extractions = []; vrai_mkdtemp = tempfile.mkdtemp
        with tempfile.TemporaryDirectory() as d:
            for nom in ('Re-Volt (Europe) (En,Fr).chd', 'Casse (Europe).chd', 'Grandia II (Europe).chd'):
                (Path(d) / nom).write_bytes(b'')
            import zipfile
            with zipfile.ZipFile(Path(d) / 'Lot.zip', 'w') as z:                # extraite puis compilée
                z.writestr('Zed (Europe).chd', b'')
            with patch.object(lanceur.filedialog, 'askdirectory', return_value=d), \
                 patch.object(lanceur, 'discover_games', return_value=[]), \
                 patch.object(lanceur, 'find_duplicates', side_effect=lambda c, n, d, g: n == 'Grandia II'), \
                 patch.object(lanceur.messagebox, 'askyesno', return_value=True) as question, \
                 patch.object(lanceur.messagebox, 'showinfo') as bilan, \
                 patch.object(lanceur, 'report_text', return_value='rapport'), \
                 patch.object(lanceur, 'save_report', return_value=None), \
                 patch.object(lanceur.tempfile, 'mkdtemp', side_effect=lambda **k: self.extractions.append(vrai_mkdtemp(**k)) or self.extractions[-1]):
                app.select_folder()
                self.assertIn('Re-Volt', question.call_args.args[1])
                self.assertIn('Grandia II', question.call_args.args[1])      # listé comme déjà présent
                self.assertIn('Lot.zip — archive : extraite puis compilée', question.call_args.args[1])
                self.assertNotIn('ignor', question.call_args.args[1])
                import time
                for _ in range(200):                                          # boucle d'événements simulée
                    time.sleep(0.02)                                          # l'extraction se fait à côté
                    app.root.after.side_effect = lambda ms, fn, *a: self.planifies.append(fn)
                    try:
                        app.poll()
                    except Exception:
                        raise
                    while self.planifies:
                        fn = self.planifies.pop(0)
                        if fn != app.poll: fn()
                    if app.batch is None: break
        self.assertEqual([n for n, _ in app.lances], ['Casse', 'Re-Volt', 'Zed', 'Casse'])   # échec retenté en fin de liste
        self.assertTrue(self.extractions and not any(Path(x).exists() for x in self.extractions))   # dossier d'extraction supprimé
        self.assertIsNone(app.batch)
        texte = bilan.call_args.args[1]
        self.assertIn('2 jeu(x) créé(s), 1 échec(s).', texte)
        self.assertIn('Casse', texte)
        self.assertIn('Retentés en fin de liste après un premier échec : Casse', texte)
        self.assertEqual(question.call_count, 1)                              # aucune question par jeu


if __name__ == '__main__':
    unittest.main()
