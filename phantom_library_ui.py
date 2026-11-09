"""Bibliothèque et gestion des sauvegardes dans l'interface PhantomStation."""
import os
import subprocess
import tkinter as tk
import zipfile
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog
from PIL import Image

from phantom_ui import ctk, button, panel, tr, load_language, BASE, BG, SURFACE, CARD, BORDER, TEXT, MUTED, ACCENT
from phantom_window_icon import set_window_icon
from bibliotheque import (discover_games, register_game, game_info, save_directory,
                          backup_saves, restore_saves, documents_folder, active_page, switch_page, rename_game)
from gestion_jeux import recycle_game
import cartes_memoire as cm


class LibraryView(ctk.CTkFrame):
    def __init__(self, parent, output_directory, choose_directory=None, play_game=None, operation_active=None):
        super().__init__(parent, fg_color='transparent')
        self.output_directory = output_directory
        self.operation_active = operation_active or (lambda: False)
        self.play_game = play_game
        self.images = []
        self.games = []
        self.platform = 'PS1'
        top = ctk.CTkFrame(self, fg_color='transparent')
        top.pack(fill='x', pady=(2, 12))
        labels = ctk.CTkFrame(top, fg_color='transparent')
        labels.pack(side='left', fill='x', expand=True)
        ctk.CTkLabel(labels, text=tr('TA COLLECTION'), text_color='#6bdcf0', font=('Segoe UI', 10, 'bold'), anchor='w').pack(anchor='w')
        self.library_title = tk.StringVar(value=tr('Bibliothèque PS1'))
        ctk.CTkLabel(labels, textvariable=self.library_title, text_color=TEXT, font=('Segoe UI', 25, 'bold'), anchor='w').pack(anchor='w')
        button(top, 'Actualiser', self.refresh, width=104).pack(side='right', padx=(8, 0))
        button(top, '+ Ajouter un jeu', self.add_game, primary=True, width=145).pack(side='right')
        console_area = ctk.CTkFrame(self, fg_color='transparent')
        console_area.pack(fill='x', pady=(0, 8))
        ctk.CTkLabel(console_area, text=tr('Choisir la console'), text_color=TEXT, font=('Segoe UI', 11, 'bold')).pack(side='left', padx=(0, 12))
        self.platform_choice = tk.StringVar(value='PS1')
        self.platform_switch = ctk.CTkSegmentedButton(console_area, values=['PS1', 'PS2', 'NES', 'SNES', 'N64', 'DC'], variable=self.platform_choice,
            command=self.set_platform, selected_color=ACCENT, selected_hover_color='#31bee6',
            unselected_color='#20334b', unselected_hover_color='#31516b', width=260)
        self.platform_switch.pack(side='left')
        self.card_bar = ctk.CTkFrame(self, fg_color='#101d31', corner_radius=12)
        self.card_bar.pack(fill='x', pady=(0, 10))
        self.card_row = ctk.CTkFrame(self.card_bar, fg_color='transparent')
        self.card_row.pack(fill='x')
        self.card_label = tk.StringVar()
        ctk.CTkLabel(self.card_row, textvariable=self.card_label, text_color=TEXT, font=('Segoe UI', 11, 'bold'), width=155, anchor='w').pack(side='left', padx=(14, 12), pady=8)
        self.card_choice = tk.StringVar()
        self.card_combo = ctk.CTkComboBox(self.card_row, variable=self.card_choice, values=[], state='readonly', width=250,
                                          fg_color=CARD, border_color='#29415c', button_color='#28536d', command=self.choose_card)
        self.card_combo.pack(side='left', padx=(0, 8), pady=8)
        button(self.card_row, '+', lambda: self.add_card_slot(1), width=42).pack(side='left', pady=8)
        button(self.card_row, 'Renommer', lambda: self.rename_card_slot(1), width=95).pack(side='left', padx=(8, 0), pady=8)
        self.card_row2 = ctk.CTkFrame(self.card_bar, fg_color='transparent')
        ctk.CTkLabel(self.card_row2, text=tr('Carte mémoire 2'), text_color=TEXT, font=('Segoe UI', 11, 'bold'), width=155, anchor='w').pack(side='left', padx=(14, 12), pady=(0, 8))
        self.card_choice2 = tk.StringVar()
        self.card_combo2 = ctk.CTkComboBox(self.card_row2, variable=self.card_choice2, values=[], state='readonly', width=250,
                                           fg_color=CARD, border_color='#29415c', button_color='#28536d', command=lambda value: self.choose_card(value, 2))
        self.card_combo2.pack(side='left', padx=(0, 8), pady=(0, 8))
        button(self.card_row2, '+', lambda: self.add_card_slot(2), width=42).pack(side='left', pady=(0, 8))
        button(self.card_row2, 'Renommer', lambda: self.rename_card_slot(2), width=95).pack(side='left', padx=(8, 0), pady=(0, 8))
        self.card_limit = tk.StringVar()
        self.card_limit_label = ctk.CTkLabel(self.card_bar, textvariable=self.card_limit, text_color=MUTED, font=('Segoe UI', 10), anchor='w')
        self.note = tk.StringVar(value='')
        ctk.CTkLabel(self, textvariable=self.note, text_color=MUTED, font=('Segoe UI', 11), anchor='w').pack(fill='x', pady=(0, 10))
        self.cards = ctk.CTkScrollableFrame(self, fg_color='transparent', corner_radius=0,
                                             scrollbar_button_color='#28445e', scrollbar_button_hover_color='#3b728c')
        self.cards.pack(fill='both', expand=True)
        self.cards.grid_columnconfigure(0, weight=1, uniform='games')
        self.cards.grid_columnconfigure(1, weight=1, uniform='games')
        self.after_idle(self.refresh)

    def set_platform(self, platform):
        self.platform = platform if platform in ('PS1', 'PS2', 'NES', 'SNES', 'N64', 'DC') else 'PS1'
        self.platform_choice.set(self.platform)
        self.library_title.set(('Library ' if load_language() == 'en' else 'Bibliothèque ') + self.platform)
        self.refresh_cards()
        self.refresh()

    def refresh_cards(self):
        profile = self.platform in cm.NINTENDO
        self.card_label.set(('Save profile' if profile else 'Memory card 1' if self.platform in ('PS1', 'PS2') else 'Memory card') if load_language() == 'en'
                            else ('Profil de sauvegarde' if profile else 'Carte mémoire 1' if self.platform in ('PS1', 'PS2') else 'Carte mémoire'))
        default = 'Game’s current save' if load_language() == 'en' else 'Sauvegarde actuelle du jeu'
        try:
            default_name = cm.default_label(self.platform)
            second_name = cm.default_label(self.platform, 2) if self.platform == 'PS1' else ''
        except (OSError, ValueError) as exc:
            self.card_choice.set('—')
            messagebox.showwarning(tr('Carte mémoire'), str(exc), parent=self)
            return
        suffix = ' · game save' if load_language() == 'en' else ' · sauvegarde du jeu'
        if default_name: default = default_name + suffix
        cards = cm.list_cards(self.platform)
        self.card_map = {default: None}
        for card in cards:
            label = card['nom']
            if label in self.card_map: label += ' · ' + card['id'][:6]
            self.card_map[label] = card['id']
        second_default = ('Empty slot' if load_language() == 'en' else 'Emplacement vide') if self.platform == 'PS2' else (
            second_name + suffix if second_name else ('Game’s current save' if load_language() == 'en' else 'Sauvegarde actuelle du jeu'))
        self.card_map2 = {second_default: None}
        for label, cid in self.card_map.items():
            if cid is not None:
                self.card_map2[label if label not in self.card_map2 else label + ' · ' + cid[:6]] = cid
        self.card_combo.configure(values=list(self.card_map))
        self.card_combo2.configure(values=list(self.card_map2))
        if self.platform in ('PS1', 'PS2'): self.card_row2.pack(fill='x')
        else: self.card_row2.pack_forget()
        if self.platform == 'DC':
            self.card_limit.set('Only one slot is supported by the current engine.' if load_language() == 'en' else 'Le moteur actuel prend en charge un seul emplacement.')
            self.card_limit_label.pack(fill='x', padx=14, pady=(0, 8))
        else: self.card_limit_label.pack_forget()
        try:
            chosen = cm.selected(self.platform)
            current = chosen['id'] if chosen else None
            self.card_choice.set(next((label for label, cid in self.card_map.items() if cid == current), default))
            if self.platform in ('PS1', 'PS2'):
                second = cm.selected(self.platform, 2)
                second_id = second['id'] if second else None
                self.card_choice2.set(next((label for label, cid in self.card_map2.items() if cid == second_id), second_default))
        except (OSError, ValueError) as exc:
            self.card_choice.set('—')
            messagebox.showwarning(tr('Carte mémoire'), str(exc), parent=self)

    def choose_card(self, label, slot=1):
        try:
            if self.operation_active(): raise ValueError('Attends la fin de l’opération en cours.')
            if any(cm.bibliotheque.game_running(game) for game in self.games):
                raise ValueError('Ferme les jeux de cette console avant de changer de carte ou de profil.')
            mapping = self.card_map2 if slot == 2 else self.card_map
            if label not in mapping: raise ValueError('Sélection inconnue.')
            cm.select(self.platform, mapping[label], slot)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            messagebox.showerror(tr('Sélection impossible'), str(exc), parent=self)
            self.refresh_cards()

    def add_card_slot(self, slot=1):
        if self.operation_active():
            messagebox.showinfo(tr('Déplacement en cours'), tr('Attends la fin du déplacement des jeux.'), parent=self)
            return
        is_profile = self.platform in cm.NINTENDO
        source = None
        if not is_profile:
            use_blank = messagebox.askyesnocancel(tr('Ajouter une carte'),
                tr('Créer une carte vierge ?\n\nOui : créer une carte.\nNon : importer un fichier existant.'), parent=self)
            if use_blank is None: return
            if not use_blank:
                extensions = {'PS1': '*.mcd *.mcr *.mc', 'PS2': '*.ps2', 'DC': '*.vmu *.bin'}
                source = filedialog.askopenfilename(parent=self, title=tr('Choisir une carte mémoire'),
                           filetypes=[(tr('Cartes mémoire'), extensions[self.platform]), (tr('Tous les fichiers'), '*.*')])
                if not source: return
        name = simpledialog.askstring(tr('Nouveau profil') if is_profile else tr('Ajouter une carte'),
                                      tr('Nom affiché dans PhantomStation :'), parent=self,
                                      initialvalue=Path(source).stem if source else '')
        if name is None: return
        try:
            card = cm.create(self.platform, name, source)
            self.refresh_cards()
            mapping = self.card_map2 if slot == 2 else self.card_map
            label = next(label for label, cid in mapping.items() if cid == card['id'])
            self.choose_card(label, slot)
            self.refresh_cards()
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            messagebox.showerror(tr('Création impossible'), str(exc), parent=self)

    def rename_card_slot(self, slot=1):
        try:
            if self.operation_active(): raise ValueError('Attends la fin de l’opération en cours.')
            selected = cm.selected(self.platform, slot)
            if selected is None:
                if self.platform == 'PS2' and slot == 2:
                    messagebox.showinfo(tr('Renommer'), tr('Cet emplacement est vide : choisis une carte à renommer.'), parent=self)
                    return
                initial = cm.default_label(self.platform, slot)
                name = simpledialog.askstring(tr('Renommer'), tr('Nom affiché dans PhantomStation :'),
                                              parent=self, initialvalue=initial)
                if name is None: return
                cm.rename_default(self.platform, slot, name)
                self.refresh_cards()
                return
            name = simpledialog.askstring(tr('Renommer'), tr('Nom affiché dans PhantomStation :'),
                                          parent=self, initialvalue=selected['nom'])
            if name is None: return
            cm.rename(self.platform, selected['id'], name)
            self.refresh_cards()
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            messagebox.showerror(tr('Renommage impossible'), str(exc), parent=self)

    def refresh(self):
        if self.operation_active():
            self.note.set(tr('Déplacement des jeux en cours…'))
            return
        for child in self.cards.winfo_children(): child.destroy()
        self.images = []
        self.refresh_cards()
        self.games = [game for game in discover_games(self.output_directory()) if game.get('platform', 'PS1') == self.platform]
        if not self.games:
            self.note.set(('No ' + self.platform + ' games found. Create a game or use “Add a game”.') if load_language() == 'en'
                          else ('Aucun jeu ' + self.platform + ' trouvé. Crée un jeu ou utilise « Ajouter un jeu ».'))
            return
        self.note.set((f'{len(self.games)} {self.platform} games  ·  Saves only: backed up before launch if changed.') if load_language() == 'en'
                      else f'{len(self.games)} jeux {self.platform}  ·  Sauvegardes uniquement : copie avant le lancement si elles ont changé.')
        for index, game in enumerate(self.games):
            try: register_game(game['path'])
            except OSError: pass
            self.add_card(game, index)

    def add_card(self, game, index):
        card = panel(self.cards)
        card.grid(row=index//2, column=index%2, sticky='nsew', padx=(0, 7) if index%2 == 0 else (7, 0), pady=(0, 14))
        body = ctk.CTkFrame(card, fg_color='transparent')
        body.pack(fill='both', expand=True, padx=14, pady=14)
        source = game['cover']
        if source is None:
            with Image.open(BASE / 'assets' / 'phantomstation-symboles.png') as im: source = im.convert('RGBA')
        source = source.copy()
        source.thumbnail((105, 145), Image.Resampling.LANCZOS)
        art = Image.new('RGBA', (105, 145), '#1c3048')
        art.alpha_composite(source, ((105-source.width)//2, (145-source.height)//2))
        photo = ctk.CTkImage(light_image=art, dark_image=art, size=(105, 145))
        self.images.append(photo)
        ctk.CTkLabel(body, text='', image=photo).pack(side='left', padx=(0, 13))
        details = ctk.CTkFrame(body, fg_color='transparent')
        details.pack(side='left', fill='both', expand=True)
        platform = game.get('platform', 'PS1')
        console = ('SEGA  ·  ' if platform == 'DC' else 'NINTENDO  ·  ' if platform in ('NES', 'SNES', 'N64') else 'PLAYSTATION  ·  ') + platform
        ctk.CTkLabel(details, text=console + ('  ·  ' + game['serial'] if game['serial'] else ''), text_color='#69dcec', font=('Segoe UI', 9, 'bold'), anchor='w').pack(fill='x')
        game_title = ctk.CTkLabel(details, text=game['name'], text_color=TEXT, font=('Segoe UI', 14, 'bold'), anchor='w', wraplength=230, justify='left')
        game_title._phantom_no_translate = True
        game_title.pack(fill='x', pady=(4, 7))
        actions = ctk.CTkFrame(details, fg_color='transparent')
        actions.pack(anchor='w')
        button(actions, '▶  Ouvrir le lanceur', lambda g=game: self.play(g), primary=True, width=148).pack(side='left')
        button(actions, 'Sauvegardes', lambda g=game: self.save_menu(g), width=105).pack(side='left', padx=(7, 0))
        manage = ctk.CTkFrame(details, fg_color='transparent')
        manage.pack(anchor='w', pady=(7, 0))
        button(manage, 'Renommer', lambda g=game: self.rename(g), width=105).pack(side='left')
        button(manage, 'Supprimer', lambda g=game: self.delete_game(g), width=105).pack(side='left', padx=(7, 0))
        hint = tr('Vidéo et son expérimental dans le lanceur.' if platform == 'DC' else
                'Réglages et commandes : F10 pendant le jeu.' if platform == 'N64' else
                'Réglages et commandes : F2 pendant le jeu.' if platform in ('NES', 'SNES') else
                'Codes, vidéo et commandes dans le lanceur.')
        ctk.CTkLabel(details, text=hint, text_color=MUTED,
                     font=('Segoe UI', 9), anchor='w', wraplength=260, justify='left').pack(fill='x', pady=(7, 0))

    def add_game(self):
        if self.operation_active():
            messagebox.showinfo(tr('Déplacement en cours'), tr('Attends la fin du déplacement des jeux.'), parent=self)
            return
        paths = filedialog.askopenfilenames(parent=self, title=tr('Ajouter des jeux PhantomStation'), filetypes=[('Jeux Windows', '*.exe')])
        errors = []
        for path in paths:
            try: game_info(path); register_game(path)
            except (OSError, ValueError, KeyError, UnicodeError, zipfile.BadZipFile) as exc: errors.append(Path(path).name + ' : ' + str(exc))
        self.refresh()
        if errors: messagebox.showwarning(tr('Jeux non ajoutés'), '\n'.join(errors), parent=self)

    def rename(self, game):
        if self.operation_active():
            messagebox.showinfo(tr('Déplacement en cours'), tr('Attends la fin du déplacement des jeux.'), parent=self)
            return
        name = simpledialog.askstring(tr('Renommer le jeu'), tr('Nouveau nom du jeu :'), initialvalue=game['name'], parent=self)
        if name is None:
            return
        try:
            rename_game(game, name)
            self.refresh()
        except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
            messagebox.showerror(tr('Renommage impossible'), str(exc), parent=self)

    def delete_game(self, game):
        if self.operation_active():
            messagebox.showinfo(tr('Déplacement en cours'), tr('Attends la fin du déplacement des jeux.'), parent=self)
            return
        text = (f'Move “{game["name"]}” to the Recycle Bin? Saved games are kept.'
                if load_language() == 'en' else
                f'Déplacer « {game["name"]} » dans la Corbeille ? Les sauvegardes seront conservées.')
        if not messagebox.askyesno(tr('Supprimer le jeu'), text, parent=self, default=messagebox.NO):
            return
        try:
            recycle_game(game)
            self.refresh()
        except (OSError, ValueError, KeyError, UnicodeError, zipfile.BadZipFile, subprocess.SubprocessError) as exc:
            self.refresh()
            messagebox.showerror(tr('Suppression impossible'), str(exc), parent=self)

    def play(self, game):
        if self.operation_active():
            messagebox.showinfo(tr('Déplacement en cours'), tr('Attends la fin du déplacement des jeux.'), parent=self)
            return
        if not game['path'].is_file():
            messagebox.showerror(tr('Jeu introuvable'), tr('Le fichier du jeu a été déplacé ou supprimé.'), parent=self)
            return
        try:
            if game.get('platform') in cm.CARDS and (cm.selected(game['platform']) or (game['platform'] in ('PS1', 'PS2') and cm.selected(game['platform'], 2))):
                if any(cm.bibliotheque.game_running(other) for other in self.games):
                    raise ValueError('Ferme les jeux de cette console avant d’utiliser la carte partagée.')
            backup_saves(game, skip_unchanged=True)
            if self.play_game is None:
                raise ValueError('Le lecteur intégré au compilateur est indisponible.')
            self.play_game(game['path'])
        except (OSError, ValueError, subprocess.SubprocessError) as exc: messagebox.showerror(tr('Lancement impossible'), str(exc), parent=self)

    def save_menu(self, game):
        if self.operation_active():
            messagebox.showinfo(tr('Déplacement en cours'), tr('Attends la fin du déplacement des jeux.'), parent=self)
            return
        dialog = tk.Toplevel(self)
        dialog.title(('Saves — ' if load_language() == 'en' else 'Sauvegardes — ') + game['name'])
        dialog.configure(bg=BG)
        dialog.resizable(False, False)
        set_window_icon(dialog)
        outer = ctk.CTkFrame(dialog, fg_color=BG, corner_radius=0)
        outer.pack(fill='both', expand=True, padx=20, pady=20)
        ctk.CTkLabel(outer, text=tr('SAUVEGARDES'), text_color='#6bdcec', font=('Segoe UI', 10, 'bold'), anchor='w').pack(fill='x')
        game_title = ctk.CTkLabel(outer, text=game['name'], text_color=TEXT, font=('Segoe UI', 22, 'bold'), anchor='w', wraplength=520)
        game_title._phantom_no_translate = True
        game_title.pack(fill='x', pady=(0, 14))
        backups = panel(outer)
        backups.pack(fill='x', pady=(0, 12))
        inner = ctk.CTkFrame(backups, fg_color='transparent')
        inner.pack(fill='x', padx=18, pady=16)
        ctk.CTkLabel(inner, text=tr('Copies de secours'), text_color=TEXT, font=('Segoe UI', 16, 'bold'), anchor='w').pack(fill='x')
        ctk.CTkLabel(inner, text=tr('Une copie est créée avant de lancer depuis la bibliothèque.\nFerme le jeu avant de copier ou restaurer.'), text_color=MUTED, font=('Segoe UI', 11), anchor='w', justify='left').pack(fill='x', pady=(4, 10))
        button(inner, 'Ouvrir le dossier des sauvegardes', lambda: self.open_saves(game, dialog)).pack(fill='x', pady=3)
        button(inner, 'Créer une copie de secours', lambda: self.backup(game, dialog)).pack(fill='x', pady=3)
        page_label = tk.StringVar(value=''); selected_page = tk.StringVar(value='1')
        button(inner, 'Restaurer une copie de secours', lambda: self.restore(game, dialog, page_label, selected_page)).pack(fill='x', pady=3)
        try:
            selected_card = cm.selected(game.get('platform')) if game.get('platform') in cm.CARDS else None
            selected_second = cm.selected(game['platform'], 2) if game.get('platform') in ('PS1', 'PS2') else None
        except (OSError, ValueError) as exc:
            dialog.destroy()
            messagebox.showerror(tr('Sélection impossible'), str(exc), parent=self)
            return
        for position, selected_item in ((1, selected_card), (2, selected_second)):
            if not selected_item: continue
            info = (f'Shared card {position}: ' if load_language() == 'en' else f'Carte partagée {position} : ') + selected_item['nom']
            ctk.CTkLabel(inner, text=info, text_color=MUTED, font=('Segoe UI', 11), anchor='w').pack(fill='x', pady=(8, 0))
            backup_dir = documents_folder() / 'PhantomStation' / 'Sauvegardes-secours' / 'Cartes' / game['platform'] / selected_item['id']
            def open_card_backups(folder=backup_dir):
                folder.mkdir(parents=True, exist_ok=True)
                os.startfile(str(folder))
            button(inner, 'Copies de la carte partagée', open_card_backups).pack(fill='x', pady=3)
        if selected_card and game.get('platform') == 'PS1':
            ctk.CTkLabel(inner, text=tr('Les pages PS1 concernent uniquement la carte par défaut du jeu.'), text_color=MUTED,
                         font=('Segoe UI', 11), anchor='w', wraplength=500).pack(fill='x', pady=(4, 0))
            button(outer, 'Fermer', dialog.destroy, width=110).pack(anchor='e', pady=(14, 0))
            return
        if game.get('platform') in ('PS2', 'NES', 'SNES', 'N64', 'DC'):
            note = tr('Carte mémoire Dreamcast : les sauvegardes dépendent du jeu.' if game.get('platform') == 'DC' else 'Carte mémoire PS2 : les sauvegardes dépendent du jeu.' if game.get('platform') == 'PS2' else 'Les sauvegardes dépendent de la cartouche et du jeu.')
            ctk.CTkLabel(inner, text=note, text_color=MUTED,
                         font=('Segoe UI', 11), anchor='w').pack(fill='x', pady=(5, 0))
            button(outer, 'Fermer', dialog.destroy, width=110).pack(anchor='e', pady=(14, 0))
            return
        memory = panel(outer)
        memory.pack(fill='x')
        inside = ctk.CTkFrame(memory, fg_color='transparent')
        inside.pack(fill='x', padx=18, pady=16)
        ctk.CTkLabel(inside, text=tr('Carte mémoire  ·  240 blocs'), text_color=TEXT, font=('Segoe UI', 16, 'bold'), anchor='w').pack(fill='x')
        ctk.CTkLabel(inside, text=tr('16 pages de 15 blocs. Choisis une page avant de lancer le jeu.'), text_color=MUTED, font=('Segoe UI', 11), anchor='w').pack(fill='x', pady=(4, 10))
        try: current = active_page(game)
        except (OSError, ValueError) as exc:
            current = 1
            messagebox.showwarning(tr('Carte mémoire'), str(exc), parent=dialog)
        selected_page = tk.StringVar(value=str(current))
        page_label = tk.StringVar(value=(f'Active page: {current} / 16' if load_language() == 'en' else f'Page active : {current} / 16'))
        row = ctk.CTkFrame(inside, fg_color='transparent'); row.pack(fill='x')
        ctk.CTkComboBox(row, variable=selected_page, values=[str(i) for i in range(1, 17)], state='readonly', width=75, height=39, corner_radius=10, fg_color=CARD, border_color=BORDER, button_color='#28536d').pack(side='left')
        button(row, 'Changer de page', lambda: self.change_page(game, selected_page, page_label, dialog), primary=True, width=140).pack(side='left', padx=9)
        ctk.CTkLabel(row, textvariable=page_label, text_color=MUTED, font=('Segoe UI', 11)).pack(side='left')
        button(outer, 'Fermer', dialog.destroy, width=110).pack(anchor='e', pady=(14, 0))

    def change_page(self, game, selected, label, parent):
        try:
            page = switch_page(game, int(selected.get()))
            label.set(f'Active page: {page} / 16' if load_language() == 'en' else f'Page active : {page} / 16')
            messagebox.showinfo(tr('Carte mémoire'), f'Page {page} sélectionnée. Tu peux lancer le jeu.', parent=parent)
        except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc: messagebox.showerror(tr('Changement impossible'), str(exc), parent=parent)

    def open_saves(self, game, parent):
        try:
            folder = save_directory(game)
            if folder is None or not folder.is_dir(): raise ValueError('Lance une première fois le jeu pour créer ses sauvegardes.')
            os.startfile(str(folder))
        except (OSError, ValueError) as exc: messagebox.showinfo(tr('Sauvegardes'), str(exc), parent=parent)

    def backup(self, game, parent):
        try:
            path = backup_saves(game)
            messagebox.showinfo(tr('Copie de secours'), tr('Copie créée :\n') + str(path) if path else tr('Aucune sauvegarde à copier pour le moment.'), parent=parent)
        except (OSError, ValueError, subprocess.SubprocessError) as exc: messagebox.showerror(tr('Copie impossible'), str(exc), parent=parent)

    def restore(self, game, parent, page_label, selected_page):
        folder = documents_folder() / 'PhantomStation' / 'Sauvegardes-secours' / game['id']
        if not folder.is_dir():
            messagebox.showinfo(tr('Restauration'), tr('Aucune copie de secours trouvée pour ce jeu.'), parent=parent)
            return
        path = filedialog.askopenfilename(parent=parent, title=tr('Choisir une copie de secours'), initialdir=str(folder), filetypes=[('Copies de secours', '*.zip')])
        if not path or not messagebox.askyesno(tr('Restaurer les sauvegardes'), tr('Restaurer cette copie ? Une copie des sauvegardes actuelles sera créée avant le remplacement.'), parent=parent, default='no'): return
        try:
            previous = restore_saves(game, path)
            if game.get('platform') == 'PS1':
                page = active_page(game); page_label.set(f'Active page: {page} / 16' if load_language() == 'en' else f'Page active : {page} / 16'); selected_page.set(str(page))
            messagebox.showinfo(tr('Restauration terminée'), tr('Sauvegardes restaurées.') + (tr('\nCopie précédente : ') + str(previous) if previous else ''), parent=parent)
        except (OSError, ValueError, KeyError, UnicodeError, zipfile.BadZipFile, subprocess.SubprocessError) as exc: messagebox.showerror(tr('Restauration impossible'), str(exc), parent=parent)
