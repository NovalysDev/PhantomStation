"""Bibliothèque et gestion des sauvegardes dans l'interface PhantomStation."""
import os
import subprocess
import tkinter as tk
import zipfile
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog
from PIL import Image

from phantom_ui import ctk, button, panel, tr, load_language, BASE, BG, SURFACE, CARD, BORDER, TEXT, MUTED, ACCENT, ACCENT_HOVER
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
        # Les cartes des jeux sont dessinées une fois en images (gardées en
        # cache) sur un seul canevas : défiler ne fait que déplacer ces images,
        # au lieu de redessiner une douzaine de widgets par jeu.
        zone = ctk.CTkFrame(self, fg_color='transparent')
        zone.pack(fill='both', expand=True)
        self.scroll_canvas = tk.Canvas(zone, bg=BG, highlightthickness=0, bd=0, yscrollincrement=1)
        self.scrollbar = ctk.CTkScrollbar(zone, command=self.scroll_canvas.yview,
                                          button_color='#28445e', button_hover_color='#3b728c')
        self.scrollbar.pack(side='right', fill='y')
        self.scroll_canvas.pack(side='left', fill='both', expand=True)
        self.scroll_canvas.configure(yscrollcommand=self.scrollbar.set)
        self.scroll_canvas.bind('<Configure>', lambda event: self._schedule_layout())
        self.scroll_canvas.bind('<MouseWheel>', self._wheel)
        self._layout_job = None
        self._layout_width = 0
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
        self.refresh_cards()
        # Même console (renommage, suppression…) : la liste reste où elle était.
        position = self.scroll_canvas.yview()[0] if getattr(self, '_shown_platform', None) == self.platform else 0
        self._shown_platform = self.platform
        self.games = [game for game in discover_games(self.output_directory()) if game.get('platform', 'PS1') == self.platform]
        if not self.games:
            self.note.set(('No ' + self.platform + ' games found. Create a game or use “Add a game”.') if load_language() == 'en'
                          else ('Aucun jeu ' + self.platform + ' trouvé. Crée un jeu ou utilise « Ajouter un jeu ».'))
            self.layout_cards()
            return
        self.note.set((f'{len(self.games)} {self.platform} games  ·  Saves only: backed up before launch if changed.') if load_language() == 'en'
                      else f'{len(self.games)} jeux {self.platform}  ·  Sauvegardes uniquement : copie avant le lancement si elles ont changé.')
        for game in self.games:
            try: register_game(game['path'])
            except OSError: pass
        self.layout_cards()
        self.scroll_canvas.yview_moveto(position)

    # --- Cartes dessinées en images -------------------------------------------

    _card_cache = {}      # carte déjà dessinée : (jeu, largeur, échelle, langue) -> (image, hauteur)
    _button_cache = {}    # bouton déjà dessiné : (texte, largeur, principal, survol, échelle) -> image

    def _scale(self):
        try: return self._get_widget_scaling()
        except AttributeError: return 1.0

    @staticmethod
    def _font(size, bold=False):
        from PIL import ImageFont
        windows = Path(os.environ.get('WINDIR', r'C:\Windows')) / 'Fonts'
        try: return ImageFont.truetype(str(windows / ('segoeuib.ttf' if bold else 'segoeui.ttf')), max(1, round(size)))
        except OSError: return ImageFont.load_default()

    @staticmethod
    def _wrap(text, font, width):
        """Lignes de text tenant dans width pixels (coupe aux espaces)."""
        lines, line = [], ''
        for word in text.split():
            candidate = (line + ' ' + word).strip()
            if line and font.getlength(candidate) > width:
                lines.append(line); line = word
            else:
                line = candidate
        return lines + ([line] if line else []) or ['']

    def _button_image(self, text, width, primary, hover, s):
        """Bouton dessiné comme ceux de phantom_ui.button (36 de haut, coins de 12)."""
        key = (text, width, primary, hover, s)
        if key in self._button_cache: return self._button_cache[key]
        from PIL import ImageDraw, ImageTk
        w, h, q = max(1, round(width * s)), max(1, round(36 * s)), 3
        color = (ACCENT_HOVER if hover else ACCENT) if primary else ('#31516b' if hover else '#20334b')
        # Image opaque (coins sur le fond de la carte) : Tk l'affiche sans mélange
        # de transparence, bien plus vite pendant le défilement.
        shape = Image.new('RGB', (w * q, h * q), SURFACE)
        ImageDraw.Draw(shape).rounded_rectangle((0, 0, w * q - 1, h * q - 1), radius=round(12 * s * q), fill=color)
        font = self._font(12 * s, bold=primary)
        label = text.lstrip('▶').strip()
        icon = round(10 * s) if text.startswith('▶') else 0
        gap = round(8 * s) if icon else 0
        total = icon + gap + font.getlength(label)
        x = (w - total) / 2
        if icon:   # le triangle de « Ouvrir le lanceur », lissé comme le fond
            cy = h * q / 2
            ImageDraw.Draw(shape).polygon([(x * q, cy - icon * q / 2), (x * q, cy + icon * q / 2), ((x + icon * 0.9) * q, cy)], fill='#ffffff')
        image = shape.resize((w, h), Image.Resampling.LANCZOS)
        ImageDraw.Draw(image).text((x + icon + gap, h / 2), label, font=font, fill='#ffffff', anchor='lm')
        photo = ImageTk.PhotoImage(image, master=self)
        self._button_cache[key] = photo
        return photo

    def _buttons(self):
        return [('play', tr('▶  Ouvrir le lanceur'), 148, True, self.play),
                ('saves', tr('Sauvegardes'), 105, False, self.save_menu),
                ('rename', tr('Renommer'), 105, False, self.rename),
                ('delete', tr('Supprimer'), 105, False, self.delete_game)]

    def _card_layout(self, s):
        """Position des 4 boutons dans la carte, comme l'ancienne mise en page."""
        x0 = round((14 + 105 + 13) * s)
        return {'play': (x0, 0), 'saves': (x0 + round((148 + 7) * s), 0),
                'rename': (x0, round(43 * s)), 'delete': (x0 + round((105 + 7) * s), round(43 * s))}

    def _card_image(self, game, width, s):
        """Image d'une carte (fond, jaquette, textes) ; les boutons sont posés
        par-dessus pour réagir au survol. Renvoie (image, hauteur, y des boutons)."""
        try:
            stat = game['path'].stat(); stamp = (stat.st_size, stat.st_mtime_ns)
        except OSError:
            stamp = None
        language = load_language()
        key = (str(game['path']), game['name'], game.get('serial'), stamp, width, s, language)
        if key in self._card_cache: return self._card_cache[key]
        from PIL import ImageDraw, ImageTk
        platform = game.get('platform', 'PS1')
        pad, cover_w, cover_h = round(14 * s), round(105 * s), round(145 * s)
        text_x = pad + cover_w + round(13 * s)
        text_w = max(1, width - text_x - pad)
        console = ('SEGA  ·  ' if platform == 'DC' else 'NINTENDO  ·  ' if platform in ('NES', 'SNES', 'N64') else 'PLAYSTATION  ·  ') + platform
        console += '  ·  ' + game['serial'] if game['serial'] else ''
        hint = tr('Vidéo et son expérimental dans le lanceur.' if platform == 'DC' else
                  'Réglages et commandes : F10 pendant le jeu.' if platform == 'N64' else
                  'Réglages et commandes : F2 pendant le jeu.' if platform in ('NES', 'SNES') else
                  'Codes, vidéo et commandes dans le lanceur.')
        small, title_font, small_regular = self._font(9 * s, True), self._font(14 * s, True), self._font(9 * s)
        title_lines = self._wrap(game['name'], title_font, min(text_w, round(230 * s)))
        hint_lines = self._wrap(hint, small_regular, min(text_w, round(260 * s)))
        small_h, title_h = round(13 * s), round(20 * s)
        y_title = pad + small_h + round(4 * s)
        y_buttons = y_title + title_h * len(title_lines) + round(7 * s)
        y_hint = y_buttons + round((36 + 7 + 36 + 7) * s)
        height = max(pad + cover_h + pad, y_hint + small_h * len(hint_lines) + pad)
        # Fond arrondi lissé (dessiné 3 fois plus grand puis réduit), comme phantom_ui.panel.
        q = 3
        shape = Image.new('RGBA', (width * q, height * q), BG)
        ImageDraw.Draw(shape).rounded_rectangle((0, 0, width * q - 1, height * q - 1), radius=round(20 * s * q),
                                                fill=SURFACE, outline=BORDER, width=max(1, round(s * q)))
        card = shape.resize((width, height), Image.Resampling.LANCZOS)
        source = game['cover']
        if source is None:
            with Image.open(BASE / 'assets' / 'phantomstation-symboles.png') as im: source = im.convert('RGBA')
        source = source.copy().convert('RGBA')
        source.thumbnail((cover_w, cover_h), Image.Resampling.LANCZOS)
        art = Image.new('RGBA', (cover_w, cover_h), '#1c3048')
        art.alpha_composite(source, ((cover_w - source.width) // 2, (cover_h - source.height) // 2))
        card.alpha_composite(art, (pad, (height - cover_h) // 2))
        draw = ImageDraw.Draw(card)
        draw.text((text_x, pad), console, font=small, fill='#69dcec')
        for i, line in enumerate(title_lines):
            draw.text((text_x, y_title + i * title_h), line, font=title_font, fill=TEXT)
        for i, line in enumerate(hint_lines):
            draw.text((text_x, y_hint + i * small_h), line, font=small_regular, fill=MUTED)
        result = (ImageTk.PhotoImage(card.convert('RGB'), master=self), height, y_buttons)   # opaque : défilement rapide
        if len(self._card_cache) > 400: self._card_cache.clear()
        self._card_cache[key] = result
        return result

    def _schedule_layout(self):
        if self._layout_job is not None: self.after_cancel(self._layout_job)
        self._layout_job = self.after(60, self.layout_cards)

    def layout_cards(self):
        """Place les cartes sur deux colonnes ; ne redessine que si la largeur a changé."""
        self._layout_job = None
        canvas = self.scroll_canvas
        canvas.delete('all')
        if not self.games:
            canvas.configure(scrollregion=(0, 0, 1, 1))
            return
        s = self._scale()
        total = max(1, canvas.winfo_width())
        gap = round(14 * s)
        width = max(round(300 * s), (total - gap) // 2)
        self._layout_width = total
        layout = self._card_layout(s)
        y = 0
        for row in range(0, len(self.games), 2):
            pair = self.games[row:row + 2]
            images = [self._card_image(game, width, s) for game in pair]
            for column, (game, (photo, height, y_buttons)) in enumerate(zip(pair, images)):
                x = column * (width + gap)
                canvas.create_image(x, y, image=photo, anchor='nw')
                for name, text, button_width, primary, action in self._buttons():
                    bx, by = layout[name]
                    normal = self._button_image(text, button_width, primary, False, s)
                    hover = self._button_image(text, button_width, primary, True, s)
                    item = canvas.create_image(x + bx, y + y_buttons + by, image=normal, anchor='nw')
                    canvas.tag_bind(item, '<Enter>', lambda e, i=item, h=hover: (canvas.itemconfigure(i, image=h), canvas.configure(cursor='hand2')))
                    canvas.tag_bind(item, '<Leave>', lambda e, i=item, n=normal: (canvas.itemconfigure(i, image=n), canvas.configure(cursor='')))
                    canvas.tag_bind(item, '<ButtonRelease-1>', lambda e, a=action, g=game: a(g))
            y += max(height for _, height, _ in images) + gap
        canvas.configure(scrollregion=(0, 0, total, max(1, y - gap)))

    def _wheel(self, event):
        top, bottom = self.scroll_canvas.yview()
        if (top, bottom) != (0.0, 1.0):
            self.scroll_canvas.yview_scroll(-int(event.delta / 6 * self._scale()), 'units')

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
        dialog.geometry(f'650x{min(820, max(500, dialog.winfo_screenheight() - 100))}')
        set_window_icon(dialog)
        outer = ctk.CTkScrollableFrame(dialog, fg_color=BG, corner_radius=0)
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
        debug_panel = panel(outer)
        debug_panel.pack(fill='x', pady=(0, 12))
        debug_inner = ctk.CTkFrame(debug_panel, fg_color='transparent')
        debug_inner.pack(fill='x', padx=18, pady=12)
        ctk.CTkLabel(debug_inner, text=tr('Sauvegarde de débogage'), text_color=TEXT, font=('Segoe UI', 16, 'bold'), anchor='w').pack(fill='x')
        ctk.CTkLabel(debug_inner, text=tr('Importe la sauvegarde d’un rapport pour tester ce jeu. Ta propre sauvegarde reste intacte.'),
                     text_color=MUTED, font=('Segoe UI', 11), wraplength=500, justify='left', anchor='w').pack(fill='x', pady=(4, 6))
        debug_status = tk.StringVar()
        try:
            import diagnostic_debug as debug
            debug_status.set(tr('Débogage actif pour ce jeu') if debug.active(game) else tr('Aucune sauvegarde de débogage active'))
        except (OSError, ValueError) as exc:
            debug_status.set(str(exc))
        ctk.CTkLabel(debug_inner, textvariable=debug_status, text_color='#f7c66b', anchor='w').pack(fill='x')
        button(debug_inner, 'Importer un rapport pour tester', lambda: self.import_debug(game, dialog, debug_status)).pack(fill='x', pady=3)
        button(debug_inner, 'Supprimer la sauvegarde de débogage', lambda: self.remove_debug(game, dialog, debug_status)).pack(fill='x', pady=3)
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

    def import_debug(self, game, parent, label):
        path = filedialog.askopenfilename(parent=parent, title=tr('Choisir un rapport ou une sauvegarde'),
            filetypes=[('Rapports et sauvegardes', '*.zip *.vmu *.bin *.mcd *.mcr *.mc *.ps2 *.sav *.srm *.state *.rrstate *.rrsram *.eep'), ('Tous les fichiers', '*.*')])
        if not path: return
        try:
            import diagnostic_debug as debug
            choices = debug.candidates(game, path)
            if not choices: raise ValueError('Ce rapport ne contient pas de sauvegarde compatible avec cette console.')
            if game['platform'] in cm.CARDS and len(choices) > 1:
                listing = '\n'.join(f'{i + 1}. {name}' for i, name in enumerate(choices))
                index = simpledialog.askinteger(tr('Choisir la carte'), listing, minvalue=1, maxvalue=len(choices), parent=parent)
                if index is None: return
                choices = [choices[index - 1]]
            if not messagebox.askyesno(tr('Sauvegarde de débogage'),
                tr('Utiliser une copie de cette sauvegarde pour tester ce jeu ? Vérifie que le rapport correspond au même jeu et à la même version.'), parent=parent): return
            debug.install(game, path, choices)
            label.set(tr('Débogage actif pour ce jeu'))
            messagebox.showinfo(tr('Sauvegarde de débogage'), tr('Copie de débogage activée pour ce jeu. Ta sauvegarde habituelle sera réutilisée après suppression.'), parent=parent)
        except (OSError, ValueError, KeyError, UnicodeError, zipfile.BadZipFile, subprocess.SubprocessError) as exc:
            messagebox.showerror(tr('Import impossible'), str(exc), parent=parent)

    def remove_debug(self, game, parent, label):
        if not messagebox.askyesno(tr('Sauvegarde de débogage'), tr('Supprimer la copie de débogage et reprendre ta sauvegarde habituelle ?'), parent=parent): return
        try:
            import diagnostic_debug as debug
            debug.remove(game)
            label.set(tr('Aucune sauvegarde de débogage active'))
            messagebox.showinfo(tr('Sauvegarde de débogage'), tr('Copie supprimée. Ta sauvegarde habituelle sera utilisée au prochain lancement.'), parent=parent)
        except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
            messagebox.showerror(tr('Suppression impossible'), str(exc), parent=parent)

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
