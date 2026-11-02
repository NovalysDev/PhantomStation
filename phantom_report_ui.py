"""Fenêtre de préparation et de relecture des rapports d'erreur."""
import os
import threading
import tkinter as tk
from tkinter import messagebox

from phantom_ui import ctk, button, panel, tr, BG, TEXT, MUTED
from phantom_window_icon import set_window_icon
from rapport_erreur import save_report, submission_payload, send_report


def open_report_dialog(parent, failure):
    if failure.get('path') is None:
        try: failure['path'] = save_report(failure['text'])
        except OSError as exc:
            messagebox.showerror(tr('Rapport impossible'), 'Impossible d’enregistrer le rapport :\n' + str(exc), parent=parent)
            return
    path = failure['path']
    payload, shortened = submission_payload(failure['game'], failure['text'], failure.get('kind', 'compilation'))
    dialog = tk.Toplevel(parent)
    dialog.title(tr('Rapport d’erreur — PhantomStation'))
    dialog.configure(bg=BG)
    width = min(760, max(620, dialog.winfo_screenwidth() - 40))
    height = min(620, max(480, dialog.winfo_screenheight() - 80))
    left = max(0, (dialog.winfo_screenwidth() - width) // 2)
    top = max(0, (dialog.winfo_screenheight() - height) // 2)
    dialog.geometry(f'{width}x{height}+{left}+{top}')
    dialog.minsize(620, 480)
    set_window_icon(dialog)
    outer = ctk.CTkFrame(dialog, fg_color=BG, corner_radius=0)
    outer.pack(fill='both', expand=True, padx=20, pady=16)
    ctk.CTkLabel(outer, text='PHANTOMSTATION  /  RAPPORT', text_color='#71deea', font=('Segoe UI', 10, 'bold'), anchor='w').pack(fill='x')
    ctk.CTkLabel(outer, text=tr('Envoyer le rapport d’erreur'), text_color=TEXT, font=('Segoe UI', 21, 'bold'), anchor='w').pack(fill='x')
    ctk.CTkLabel(outer, text=tr('Vérifie le contenu avant l’envoi direct.'), text_color=MUTED, font=('Segoe UI', 11), anchor='w').pack(fill='x', pady=(0, 8))
    footer = ctk.CTkFrame(outer, fg_color='transparent'); footer.pack(side='bottom', fill='x', pady=(8, 0))
    hint = ('Le rapport sera transmis directement au service Novalys. '
            + ('Les dernières lignes sont envoyées ; le journal complet reste enregistré sur le PC.'
               if shortened else 'Le rapport complet affiché sera envoyé.'))
    ctk.CTkLabel(footer, text=hint, text_color=MUTED, font=('Segoe UI', 10), anchor='w', wraplength=690, justify='left').pack(fill='x', pady=(0, 8))
    actions = ctk.CTkFrame(footer, fg_color='transparent'); actions.pack(fill='x')
    card = panel(outer); card.pack(fill='both', expand=True)
    content = ctk.CTkFrame(card, fg_color='transparent'); content.pack(fill='both', expand=True, padx=16, pady=12)
    ctk.CTkLabel(content, text=tr('Rapport à envoyer'), text_color=TEXT, font=('Segoe UI', 11, 'bold'), anchor='w').pack(fill='x')
    ctk.CTkLabel(content, text='Rapport enregistré : ' + str(path), text_color=MUTED, font=('Segoe UI', 10),
                 anchor='w', wraplength=650, justify='left').pack(side='bottom', fill='x')
    preview_frame = ctk.CTkFrame(content, fg_color='#0b1727', corner_radius=12)
    preview_frame.pack(fill='both', expand=True, pady=(4, 8))
    preview = tk.Text(preview_frame, height=8, wrap='word', bg='#0b1727', fg='#d8eaff', insertbackground='white',
                      relief='flat', highlightthickness=0, font=('Consolas', 9), padx=10, pady=8)
    scrollbar = ctk.CTkScrollbar(preview_frame, command=preview.yview)
    preview.configure(yscrollcommand=scrollbar.set)
    scrollbar.pack(side='right', fill='y', padx=(0, 5), pady=5)
    preview.pack(side='left', fill='both', expand=True, padx=(2, 0), pady=2)
    preview.insert('1.0', payload['message']); preview.configure(state='disabled')

    def copy_report():
        dialog.clipboard_clear(); dialog.clipboard_append(failure['text'])
        messagebox.showinfo(tr('Rapport copié'), tr('Le rapport complet est copié. Tu peux le coller dans ton e-mail.'), parent=dialog)

    def open_folder():
        try: os.startfile(str(path.parent))
        except OSError as exc: messagebox.showerror(tr('Dossier inaccessible'), str(exc), parent=dialog)

    def poll_send():
        if not dialog.winfo_exists():
            return
        if failure.get('sending'):
            dialog.after(100, poll_send)
            return
        if failure.get('sent'):
            send_button.configure(state='disabled', text=tr('Rapport transmis'))
            if failure.pop('send_notification', False):
                messagebox.showinfo(tr('Rapport transmis'), tr('Le service Novalys a confirmé la réception du rapport.'), parent=dialog)
        else:
            send_button.configure(state='normal', text=tr('Envoyer le rapport'))
            error = failure.pop('send_error', None)
            if error:
                messagebox.showerror(tr('Envoi impossible'), error + '\n\nLe rapport reste enregistré sur ce PC.', parent=dialog)

    def transmit():
        if failure.get('sending') or failure.get('sent'):
            return
        failure['sending'] = True
        send_button.configure(state='disabled', text=tr('Envoi en cours…'))
        def worker():
            try:
                send_report(payload)
                failure['sent'] = True
                failure['send_notification'] = True
            except (OSError, ValueError) as exc:
                failure['send_error'] = str(exc)
            except Exception:
                failure['send_error'] = 'Une erreur inattendue a empêché l’envoi.'
            finally:
                failure['sending'] = False
        threading.Thread(target=worker, daemon=True).start()
        dialog.after(100, poll_send)

    button(actions, 'Ouvrir le dossier', open_folder, width=130).pack(side='left')
    button(actions, 'Copier le rapport', copy_report, width=140).pack(side='left', padx=7)
    send_button = button(actions, 'Envoyer le rapport', transmit, primary=True, width=160)
    send_button.pack(side='right')
    if failure.get('sending'):
        send_button.configure(state='disabled', text=tr('Envoi en cours…'))
        dialog.after(100, poll_send)
    elif failure.get('sent'):
        send_button.configure(state='disabled', text=tr('Rapport transmis'))
        if failure.get('send_notification'):
            dialog.after(100, poll_send)
    elif failure.get('send_error'):
        dialog.after(100, poll_send)
    dialog.transient(parent)
    dialog.lift()
    return dialog
