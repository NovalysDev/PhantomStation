"""Affiche le lanceur et le jeu dans la fenêtre du compilateur Windows."""
import ctypes
from ctypes import wintypes
import os
import subprocess
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
    def __init__(self, root):
        self.root = root
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

    def start(self, exe, env=None):
        if self.process is not None:
            raise ValueError('Un jeu est déjà ouvert dans PhantomStation.')
        exe = Path(exe)
        actual = exe
        environment = os.environ.copy() if env is None else env.copy()
        environment.pop('PHANTOM_PACKAGE_PATH', None)
        for key in ('PHANTOM_SELECTED_PS1_CARD_DIR', 'PHANTOM_SELECTED_PS1_CARD1', 'PHANTOM_SELECTED_PS1_CARD2', 'PHANTOM_SELECTED_PS2_CARD', 'PHANTOM_SELECTED_PS2_CARD2',
                    'PHANTOM_SELECTED_DC_CARD', 'PHANTOM_SELECTED_PROFILE_DIR'):
            environment.pop(key, None)
        environment['PHANTOM_LANGUAGE'] = load_language()
        from bibliotheque import game_info
        from cartes_memoire import launch_environment, backup_selected
        info = game_info(exe, include_cover=False)
        self.game_info = info
        selected = launch_environment(info)
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
        button(toolbar, 'Signaler un bug  ·  F10', self.report_game, width=170).pack(side='right', padx=10, pady=6)
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
                tr('Le raccourci F10 est indisponible. Utilise le bouton Signaler un bug.'), parent=self.root)
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
                self._finish(); return
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
            self._finish()
            messagebox.showerror(tr('Fenêtre du jeu'), str(exc), parent=self.root)

    def report_game(self):
        if not self.game_info or self.report_open:
            return
        self.report_open = True
        try:
            description = simpledialog.askstring(tr('Signaler un bug'),
                tr('Que se passe-t-il et à quel moment ?'), parent=self.root)
            if description is None:
                return
            from diagnostic_jeu import create_game_diagnostic
            from phantom_report_ui import open_report_dialog
            path, text = create_game_diagnostic(self.game_info, description)
            dialog = open_report_dialog(self.root, {'path': path, 'text': text, 'game': self.game_info['name'],
                                                    'kind': 'jeu', 'attachment': True})
            if dialog is not None:
                self.root.wait_window(dialog)
        except (OSError, ValueError) as exc:
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

    def _finish(self):
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
        self.root.deiconify(); self.root.lift(); self.root.focus_force()
