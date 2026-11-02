"""Contrôle et installation des mises à jour au lancement."""
import queue
import threading
import tkinter as tk
from tkinter import messagebox
from phantom_ui import ctk, tr, BG, TEXT, MUTED, panel
from phantom_window_icon import set_window_icon
import phantom_updates as updates


def start_update_check(root, app):
    events = queue.Queue()
    def check():
        try: events.put(('checked', updates.check_update()))
        except Exception: events.put(('unavailable', None))
    def poll():
        if not root.winfo_exists(): return
        try: kind, result = events.get_nowait()
        except queue.Empty:
            root.after(250, poll); return
        if kind != 'checked' or result is None: return
        if app.busy:
            events.put((kind, result)); root.after(1000, poll); return
        install_dialog(root, result)
    threading.Thread(target=check, daemon=True).start()
    root.after(250, poll)


def install_dialog(root, manifest):
    dialog = tk.Toplevel(root)
    dialog.title(tr('Mise à jour — PhantomStation'))
    dialog.configure(bg=BG); dialog.resizable(False, False)
    width, height = 540, 230
    x = max(0, (dialog.winfo_screenwidth()-width)//2); y = max(0, (dialog.winfo_screenheight()-height)//2)
    dialog.geometry(f'{width}x{height}+{x}+{y}')
    set_window_icon(dialog)
    outer = ctk.CTkFrame(dialog, fg_color=BG, corner_radius=0); outer.pack(fill='both', expand=True, padx=22, pady=20)
    ctk.CTkLabel(outer, text=tr('PHANTOMSTATION  /  MISE À JOUR'), text_color='#71deea', font=('Segoe UI', 10, 'bold'), anchor='w').pack(fill='x')
    ctk.CTkLabel(outer, text=tr('Une mise à jour est disponible'), text_color=TEXT, font=('Segoe UI', 20, 'bold'), anchor='w').pack(fill='x', pady=(3, 7))
    status = ctk.CTkLabel(outer, text=tr('Téléchargement de la version ') + manifest['version'] + '…', text_color=MUTED, font=('Segoe UI', 12), anchor='w')
    status.pack(fill='x', pady=(0, 10))
    progress = ctk.CTkProgressBar(outer, mode='determinate', progress_color='#1ca8d9'); progress.pack(fill='x', pady=(5, 12)); progress.set(0)
    ctk.CTkLabel(outer, text=tr('Tes jeux, sauvegardes et réglages sont conservés.'), text_color=MUTED, font=('Segoe UI', 11), anchor='w').pack(fill='x')
    dialog.protocol('WM_DELETE_WINDOW', lambda: None)
    dialog.transient(root); dialog.grab_set(); dialog.lift(); dialog.focus_force()
    events = queue.Queue()
    def work():
        try:
            data = updates.download_update(manifest, lambda received: events.put(('progress', received)))
            events.put(('status', 'Préparation de l’installation et du redémarrage…'))
            events.put(('done', data))
        except Exception as exc:
            events.put(('error', str(exc) or 'Une erreur inattendue est survenue.'))
    def poll():
        if not dialog.winfo_exists(): return
        try: kind, value = events.get_nowait()
        except queue.Empty:
            dialog.after(100, poll); return
        if kind == 'status':
            status.configure(text=tr(value)); dialog.after(100, poll); return
        if kind == 'progress':
            progress.set(min(1.0, value / manifest['taille']))
            status.configure(text=f'Téléchargement : {min(100, int(value * 100 / manifest["taille"]))} %')
            dialog.after(20, poll); return
        if kind == 'done':
            try:
                root._phantom_update_process = updates.defer_update(manifest, value)
            except Exception as exc:
                messagebox.showerror(tr('Mise à jour impossible'), str(exc) + '\n\nTu peux continuer à utiliser PhantomStation.', parent=dialog)
                dialog.destroy()
                return
            root.destroy()
        else:
            messagebox.showerror(tr('Mise à jour impossible'), value + '\n\nTu peux continuer à utiliser PhantomStation.', parent=dialog)
            dialog.destroy()
    threading.Thread(target=work, daemon=True).start()
    dialog.after(100, poll)
