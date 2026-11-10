"""Affiche le lanceur et le jeu dans la fenêtre du compilateur Windows."""
import ctypes
from ctypes import wintypes
import os
import subprocess
import uuid
import zipfile
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, simpledialog

from phantom_ui import ctk, button, tr, load_language, BG, MUTED, TEXT

user32 = ctypes.WinDLL('user32', use_last_error=True)
kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
HANDLE = wintypes.HANDLE
HWND = wintypes.HWND
user32.SetParent.argtypes = (HWND, HWND)
user32.SetParent.restype = HWND
user32.GetParent.argtypes = (HWND,)
user32.GetParent.restype = HWND
user32.GetWindowLongW.argtypes = (HWND, ctypes.c_int)
user32.GetWindowLongW.restype = ctypes.c_long
user32.SetWindowLongW.argtypes = (HWND, ctypes.c_int, ctypes.c_long)
user32.SetWindowLongW.restype = ctypes.c_long
user32.SetWindowPos.argtypes = (HWND, HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT)
user32.IsWindow.argtypes = (HWND,)
user32.IsWindowVisible.argtypes = (HWND,)
user32.GetWindowThreadProcessId.argtypes = (HWND, ctypes.POINTER(wintypes.DWORD))
user32.PostMessageW.argtypes = (HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
kernel32.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
kernel32.CreateJobObjectW.restype = HANDLE
kernel32.AssignProcessToJobObject.argtypes = (HANDLE, HANDLE)
kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
kernel32.QueryInformationJobObject.argtypes = (HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p)
kernel32.QueryInformationJobObject.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = (HANDLE,)


class GameInCompiler:
    def __init__(self, root, on_learning=None):
        self.root = root
        self.on_learning = on_learning or (lambda message: None)
        self.learning_after_id = None
        self.process = None
        self.job = None
        self.child = None
        self.windows = []
        self.overlay = None
        self.after_id = None
        self.closing = False
        self.previous_protocol = None
        self.last_close = 0
        self.game_info = None
        self.report_hotkey = None
        self.report_open = False
        self.pending_report = None
        self.save_selection = {}
        self.trial_hash = None
        self.trial_token = None
        self.trial_log_stamp = None
        self.debug_active = False
        self.root.bind_all('<F10>', self._compiler_f10, add='+')

    def start(self, exe, env=None):
        if self.process is not None:
            raise ValueError('Un jeu est déjà ouvert dans PhantomStation.')
        exe = Path(exe)
        actual = exe
        environment = os.environ.copy() if env is None else env.copy()
        environment.pop('PHANTOM_PACKAGE_PATH', None)
        for key in ('PHANTOM_SELECTED_PS1_CARD_DIR', 'PHANTOM_SELECTED_PS1_CARD1', 'PHANTOM_SELECTED_PS1_CARD2', 'PHANTOM_SELECTED_PS2_CARD', 'PHANTOM_SELECTED_PS2_CARD2',
                    'PHANTOM_SELECTED_DC_CARD', 'PHANTOM_SELECTED_PROFILE_DIR', 'PHANTOM_DC_TRIAL', 'PHANTOM_DC_TRIAL_TOKEN'):
            environment.pop(key, None)
        environment['PHANTOM_LANGUAGE'] = load_language()
        from bibliotheque import game_info
        from cartes_memoire import launch_environment, backup_selected
        info = game_info(exe, include_cover=False)
        self.game_info = info
        selected = launch_environment(info)
        debug_active = False
        self.debug_active = False
        self.trial_hash = None
        self.trial_token = None
        self.trial_log_stamp = None
        if info.get('platform') == 'DC' and not debug_active:
            from phantom_dc_autolearn import candidate
            try:
                trial = candidate(info)
                if trial:
                    self.trial_hash = trial['candidat']
                    self.trial_log_stamp = self._journal_stamp(info)
                    self.trial_token = uuid.uuid4().hex
                    environment['PHANTOM_DC_TRIAL'] = '1'
                    environment['PHANTOM_DC_TRIAL_TOKEN'] = self.trial_token
            except (OSError, ValueError, KeyError):
                messagebox.showwarning(tr('Apprentissage Dreamcast'),
                    tr('La version d’essai est illisible ; le moteur habituel sera utilisé.'), parent=self.root)
        self.save_selection = dict(selected)
        environment.update(selected)
        if selected:
            backup_selected(info['platform'])
            if info['platform'] in ('PS1', 'PS2'): backup_selected(info['platform'], 2)
        # Use current reporting and launch fixes for already-created games too.
        # The original single-file game remains untouched, even for large ISOs.
        from phantom_ps2 import WINDOWS_EXE_LIMIT, compatible_game_launcher
        try:
            actual = compatible_game_launcher(exe)
            environment['PHANTOM_PACKAGE_PATH'] = str(exe.resolve())
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, zipfile.BadZipFile) as exc:
            if selected:
                raise RuntimeError('Impossible de préparer le lanceur nécessaire à la carte sélectionnée : ' + str(exc)) from exc
            if exe.stat().st_size >= WINDOWS_EXE_LIMIT:
                raise
            self.trial_hash = None
            self.trial_token = None
            environment.pop('PHANTOM_DC_TRIAL', None)
            environment.pop('PHANTOM_DC_TRIAL_TOKEN', None)
            warning = ('The updated launcher could not be prepared. The game will use its original launcher; '
                       'error reporting depends on that version.' if load_language() == 'en' else
                       'Le lanceur à jour n’a pas pu être préparé. Le jeu va utiliser son lanceur d’origine ; '
                       'les rapports d’erreur dépendront de sa version.')
            messagebox.showwarning(tr('Fenêtre du jeu'), warning + '\n\n' + str(exc), parent=self.root)
        self.job = kernel32.CreateJobObjectW(None, None)
        if not self.job:
            raise OSError(ctypes.get_last_error(), 'Impossible de préparer la fenêtre du jeu.')
        try:
            self.process = subprocess.Popen([str(actual)], cwd=str(exe.parent), env=environment)
            if not kernel32.AssignProcessToJobObject(self.job, HANDLE(int(self.process._handle))):
                raise OSError(ctypes.get_last_error(), 'Impossible de suivre les fenêtres de ce jeu.')
        except Exception:
            if self.process is not None and self.process.poll() is None:
                self.process.terminate()
            self.process = None
            kernel32.CloseHandle(self.job); self.job = None
            raise

        self.closing = False
        self.overlay = ctk.CTkFrame(self.root, fg_color=BG, corner_radius=0)
        self.overlay.place(x=0, y=0, relwidth=1, relheight=1)
        self.overlay.lift()
        toolbar = ctk.CTkFrame(self.overlay, fg_color='#101d31', corner_radius=0, height=48)
        toolbar.pack(fill='x')
        button(toolbar, '← Retour au compilateur', self.close_game, width=200).pack(side='left', padx=10, pady=6)
        ctk.CTkLabel(toolbar, text=tr('F10 : signaler un bug'), text_color=MUTED).pack(side='right', padx=12)
        if self.trial_hash:
            ctk.CTkLabel(toolbar, text=tr('VERSION D’ESSAI DU JEU'), text_color='#f7c66b').pack(side='right', padx=12)
        self.status = tk.StringVar(value=tr('Ouverture du jeu…'))
        ctk.CTkLabel(toolbar, textvariable=self.status, text_color=TEXT, anchor='w').pack(side='left', fill='x', expand=True, padx=12)
        self.surface = tk.Frame(self.overlay, bg='#030814', highlightthickness=0)
        self.surface.pack(fill='both', expand=True)
        self.surface.bind('<Configure>', lambda event: self.resize())
        self.root.update_idletasks()
        self.previous_protocol = self.root.protocol('WM_DELETE_WINDOW')
        self.root.protocol('WM_DELETE_WINDOW', self.close_game)
        from phantom_report_hotkey import GameReportHotkey
        self.report_hotkey = GameReportHotkey()
        self.report_hotkey.update_pids({self.process.pid})
        if not self.report_hotkey.start():
            messagebox.showwarning(tr('Signaler un bug'),
                tr('Le raccourci F10 est indisponible pendant le jeu. Ferme le jeu puis appuie sur F10 dans PhantomStation.'), parent=self.root)
        self._poll()

    def _processes(self):
        # JOBOBJECT_BASIC_PROCESS_ID_LIST: two DWORDs followed by pointer-sized IDs.
        buf = ctypes.create_string_buffer(8 + 256 * ctypes.sizeof(ctypes.c_size_t))
        if not kernel32.QueryInformationJobObject(self.job, 3, buf, len(buf), None):
            raise OSError(ctypes.get_last_error(), 'Impossible de suivre le jeu.')
        count = ctypes.c_uint32.from_buffer(buf, 4).value
        values = (ctypes.c_size_t * min(count, 256)).from_buffer(buf, 8)
        return set(values)

    def _windows(self, pids):
        found = []
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, HWND, wintypes.LPARAM)
        def visit(hwnd, _):
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            style = user32.GetWindowLongW(hwnd, -16)
            if pid.value in pids and user32.IsWindowVisible(hwnd) and not (style & 0x40000000):
                found.append(hwnd)
            return True
        callback = callback_type(visit)
        user32.EnumWindows(callback, 0)
        return found

    def resize(self):
        if self.child and user32.IsWindow(self.child):
            user32.SetWindowPos(self.child, None, 0, 0, max(1, self.surface.winfo_width()),
                                max(1, self.surface.winfo_height()), 0x0014)

    def _attach(self, hwnd):
        target = self.surface.winfo_id()
        if not user32.IsWindow(hwnd) or not user32.IsWindow(target):
            return False
        old_style = user32.GetWindowLongW(hwnd, -16)
        user32.SetWindowLongW(hwnd, -16, (old_style & ~0x80CF0000) | 0x44000000)
        user32.SetParent(hwnd, target)
        if user32.GetParent(hwnd) != target:
            # A preparing/intro window can disappear between EnumWindows and
            # SetParent. Keep looking for its successor instead of closing the
            # session or reporting a stale HWND as an error.
            if user32.IsWindow(hwnd):
                user32.SetWindowLongW(hwnd, -16, old_style)
            return False
        self.child = hwnd
        self.resize()
        user32.SetWindowPos(hwnd, None, 0, 0, max(1, self.surface.winfo_width()),
                            max(1, self.surface.winfo_height()), 0x0054)
        self.status.set(tr('Le jeu est ouvert dans PhantomStation.'))
        return True

    def _poll(self):
        try:
            pids = self._processes()
            if self.report_hotkey:
                self.report_hotkey.update_pids(pids)
                if self.report_hotkey.consume() and not self.report_open:
                    self.report_game()
            if not pids and self.process.poll() is not None:
                self._finish(completed=True); return
            valid = self.child and user32.IsWindow(self.child) and user32.IsWindowVisible(self.child)
            if not valid:
                self.child = None
                while self.windows:
                    previous = self.windows.pop()
                    if user32.IsWindow(previous) and user32.IsWindowVisible(previous):
                        self.child = previous
                        self.resize()
                        break
            if self.child:
                current_pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(self.child, ctypes.byref(current_pid))
                # A modal settings window of the same launcher also belongs inside.
                candidates = self._windows({current_pid.value})
                if candidates:
                    previous = self.child
                    if self._attach(candidates[0]):
                        self.windows.append(previous)
            else:
                candidates = self._windows(pids)
                if candidates:
                    self._attach(candidates[0])
            if self.closing and self.child and user32.IsWindow(self.child):
                import time
                now = time.monotonic()
                if now - self.last_close >= 2:
                    user32.PostMessageW(self.child, 0x0010, 0, 0)
                    self.last_close = now
            self.after_id = self.root.after(80, self._poll)
        except (OSError, tk.TclError) as exc:
            self.pending_report = None
            self.report_open = False
            self._finish()
            messagebox.showerror(tr('Fenêtre du jeu'), str(exc), parent=self.root)

    def _compiler_f10(self, event=None):
        # During a game the native hook also sees F10; avoid processing it twice.
        if self.process is None:
            self.report_game()
        return 'break'

    @staticmethod
    def _journal_stamp(info):
        local = Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData/Local'))
        root = local / 'PhantomStation/Journaux/Jeux'
        if not root.is_dir(): return None
        stamps = []
        for folder in root.iterdir():
            if folder.is_dir() and not folder.is_symlink() and folder.name.endswith(' - ' + info['id']):
                journal = folder / 'journal.log'
                if journal.is_file() and not journal.is_symlink(): stamps.append(journal.stat().st_mtime_ns)
        return max(stamps, default=None)

    def _queue_learning(self, info):
        if info.get('platform') != 'DC': return
        try:
            from phantom_dc_autolearn import queue
            queue(info)
            self.on_learning(tr('Vérification de l’apprentissage Dreamcast en arrière-plan…'))
            self._watch_learning(info, 0)
        except (OSError, ValueError) as exc:
            messagebox.showwarning(tr('Apprentissage Dreamcast'), str(exc), parent=self.root)

    def _watch_learning(self, info, attempts):
        from phantom_dc_autolearn import _local
        import json
        if self.learning_after_id is not None:
            try: self.root.after_cancel(self.learning_after_id)
            except tk.TclError: pass
            self.learning_after_id = None
        path = _local() / 'Apprentissage-etat' / (info['id'] + '.json')
        try: state = json.loads(path.read_text(encoding='utf-8')).get('etat')
        except (OSError, ValueError, AttributeError): state = None
        messages = {
            'pret': 'Version d’essai Dreamcast prête pour la prochaine partie.',
            'aucun': 'Aucune nouvelle fonction Dreamcast à préparer.',
            'echec': 'Apprentissage impossible ; le moteur actuel est conservé.',
            'attente': 'Apprentissage reporté jusqu’à la fermeture du jeu.',
        }
        if state in messages:
            self.on_learning(tr(messages[state]))
        elif attempts < 1200:
            self.learning_after_id = self.root.after(1500, lambda: self._watch_learning(info, attempts + 1))

    @staticmethod
    def _trial_was_used(info, token):
        if not token: return False
        local = Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData/Local'))
        marker = local / 'PhantomStation/Jeux' / info['id'] / ('dreamcast-candidat-essai-' + token + '.txt')
        try:
            if marker.is_symlink() or not marker.is_file(): return False
            used = marker.read_text(encoding='ascii') == 'ok'
            marker.unlink()
            return used
        except (OSError, UnicodeError):
            return False

    def _continue_learning(self, info, expected):
        """Keep the used candidate without claiming the passage was verified."""
        try:
            from phantom_dc_autolearn import decide
            decide(info, expected, True)
            self._queue_learning(info)
        except (OSError, ValueError, KeyError, zipfile.BadZipFile, subprocess.SubprocessError) as exc:
            self.on_learning(tr('Apprentissage impossible ; le moteur actuel est conservé.') + ' ' + str(exc))

    def _recover_last_game(self):
        """After reopening the compiler, find the latest registered game's log."""
        import re
        from bibliotheque import registered_games, game_info
        local = Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData/Local'))
        root = local / 'PhantomStation/Journaux/Jeux'
        journals = []
        if root.is_dir():
            for folder in root.iterdir():
                match = re.search(r' - ([0-9a-f]{32})$', folder.name)
                journal = folder / 'journal.log'
                if match and folder.is_dir() and not folder.is_symlink() and journal.is_file() and not journal.is_symlink():
                    journals.append((journal.stat().st_mtime_ns, match.group(1)))
        games = {}
        for path in registered_games():
            try:
                info = game_info(path, include_cover=False)
                games[info['id']] = info
            except (OSError, ValueError, KeyError, zipfile.BadZipFile):
                continue
        for _, ident in sorted(journals, reverse=True):
            if ident in games:
                self.game_info = games[ident]
                # A former session did not record its card selection; use the
                # current choice and tell the user before including it.
                self.save_selection = self._current_save_selection(self.game_info)
                return

    @staticmethod
    def _current_save_selection(info):
        import cartes_memoire as cm
        console = info['platform']
        selected = cm.selected(console)
        if console in cm.NINTENDO:
            folder = cm.profile_dir(info)
            return {'PHANTOM_SELECTED_PROFILE_DIR': str(folder)} if folder else {}
        result = {}
        if selected:
            key = {'DC': 'PHANTOM_SELECTED_DC_CARD', 'PS1': 'PHANTOM_SELECTED_PS1_CARD1',
                   'PS2': 'PHANTOM_SELECTED_PS2_CARD'}[console]
            result[key] = str(selected['directory'] / cm.CARDS[console])
        if console in ('PS1', 'PS2'):
            other = cm.selected(console, 2)
            if other:
                result['PHANTOM_SELECTED_' + console + '_CARD2'] = str(other['directory'] / cm.CARDS[console])
        return result

    def report_game(self):
        if self.report_open:
            return
        self.report_open = True
        try:
            if not self.game_info:
                self._recover_last_game()
            if not self.game_info:
                messagebox.showinfo(tr('Signaler un bug'), tr('Aucun journal de jeu trouvé. Lance un jeu avant de faire un rapport.'), parent=self.root)
                self.report_open = False
                return
            description = simpledialog.askstring(tr('Signaler un bug'),
                tr('Que se passe-t-il et à quel moment ?'), parent=self.root)
            if description is None:
                self.report_open = False
                return
            if len(description.strip()) < 5:
                raise ValueError('Décris brièvement le problème.')
            include_save = messagebox.askyesnocancel(tr('Sauvegarde du jeu'),
                tr('Joindre la sauvegarde ou les cartes actuellement utilisées ?\n\nUne carte partagée peut aussi contenir des sauvegardes d’autres jeux.\nOui : joindre les sauvegardes. Non : envoyer le journal seul.'), parent=self.root)
            if include_save is None:
                self.report_open = False
                return
            include_blocks = False
            if self.game_info.get('platform') == 'DC':
                include_blocks = messagebox.askyesnocancel(tr('Blocs Dreamcast'),
                    tr('Joindre les blocs de mémoire enregistrés pour les parties du jeu qui ne fonctionnent pas encore ?\n\nIls peuvent contenir des données présentes dans la mémoire du jeu.\nOui : les joindre. Non : envoyer sans ces blocs.'), parent=self.root)
                if include_blocks is None:
                    self.report_open = False
                    return
            action = (tr('Le jeu va se fermer. ') if self.process is not None else '') + (
                tr('Le journal complet et les sauvegardes choisies seront envoyés au service de diagnostic.') if include_save else
                tr('Le journal complet sera envoyé au service de diagnostic.'))
            if include_blocks:
                action += '\n' + tr('Les blocs Dreamcast disponibles seront joints.')
            if not messagebox.askyesno(tr('Envoyer le rapport'), action + '\n\n' + tr('Continuer ?'), parent=self.root):
                self.report_open = False
                return
            self.pending_report = (self.game_info, description, bool(include_save), dict(self.save_selection), bool(include_blocks))
            if self.process is not None:
                self.close_game()
            else:
                self.root.after_idle(self._send_pending_report)
        except (OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:
            self.report_open = False
            messagebox.showerror(tr('Rapport impossible'), str(exc), parent=self.root)

    def _send_pending_report(self):
        if not self.pending_report:
            return
        info, description, include_save, selection, include_blocks = self.pending_report
        self.pending_report = None
        try:
            from diagnostic_jeu import create_game_diagnostic, game_save_files
            from phantom_report_ui import open_report_dialog
            from bibliotheque import game_running
            if game_running(info):
                raise ValueError('Le jeu est encore ouvert. Ferme-le avant de créer son rapport.')
            saves = game_save_files(info, selection) if include_save else []
            path, text = create_game_diagnostic(info, description, save_files=saves, include_blocks=include_blocks)
            from rapport_erreur import _delivery_status
            _delivery_status('Rapport préparé ; ouverture de la fenêtre d’envoi.', reset=True)
            dialog = open_report_dialog(self.root, {'path': path, 'text': text, 'game': info['name'],
                                                    'kind': 'jeu', 'attachment': True, 'auto_send': True})
            if dialog is not None:
                self.root.wait_window(dialog)
        except (OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:
            messagebox.showerror(tr('Rapport impossible'), str(exc), parent=self.root)
        finally:
            self.report_open = False

    def close_game(self):
        import time
        self.closing = True
        self.status.set(tr('Fermeture du jeu…'))
        if self.child and user32.IsWindow(self.child):
            user32.PostMessageW(self.child, 0x0010, 0, 0)
            self.last_close = time.monotonic()

    def _finish(self, completed=False):
        info, trial, stamp, token, debug = self.game_info, self.trial_hash, self.trial_log_stamp, self.trial_token, self.debug_active
        if self.report_hotkey is not None:
            self.report_hotkey.close()
            self.report_hotkey = None
        if self.after_id is not None:
            try: self.root.after_cancel(self.after_id)
            except tk.TclError: pass
            self.after_id = None
        if self.previous_protocol is not None:
            self.root.tk.call('wm', 'protocol', self.root._w, 'WM_DELETE_WINDOW', self.previous_protocol)
            self.previous_protocol = None
        if self.overlay is not None:
            self.overlay.destroy(); self.overlay = None
        if self.job:
            kernel32.CloseHandle(self.job); self.job = None
        self.process = None; self.child = None; self.windows = []; self.closing = False
        self.trial_hash = None; self.trial_log_stamp = None; self.trial_token = None; self.debug_active = False
        self.root.deiconify(); self.root.lift(); self.root.focus_force()
        used_trial = self._trial_was_used(info, token) if info and token else False
        if completed and info and trial and used_trial and self._journal_stamp(info) != stamp:
            self._continue_learning(info, trial)
        elif completed and info and trial and not used_trial:
            self.on_learning(tr('Version d’essai non utilisée ; moteur habituel conservé.'))
        elif completed and info and not trial and not debug:
            self._queue_learning(info)
        if completed and self.pending_report:
            self.root.after_idle(self._send_pending_report)
