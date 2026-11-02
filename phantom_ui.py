"""Composants visuels partagés du compilateur PhantomStation."""
import sys
import tkinter as tk
from pathlib import Path
from PIL import Image, ImageDraw, ImageOps, ImageTk

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE / 'vendor' / 'interface'))
import customtkinter as ctk

ctk.set_appearance_mode('dark')

BG = '#070d1a'
SURFACE = '#101d31'
CARD = '#14253b'
BORDER = '#29415c'
TEXT = '#f2f7ff'
MUTED = '#9bafc6'
ACCENT = '#1ca8d9'
ACCENT_HOVER = '#31bee6'


def button(parent, text, command, *, primary=False, **kwargs):
    return ctk.CTkButton(parent, text=text, command=command, height=36, corner_radius=12,
                         font=('Segoe UI', 12, 'bold' if primary else 'normal'),
                         fg_color=ACCENT if primary else '#20334b',
                         hover_color=ACCENT_HOVER if primary else '#31516b',
                         text_color='#ffffff', **kwargs)


def panel(parent, **kwargs):
    return ctk.CTkFrame(parent, fg_color=SURFACE, corner_radius=20,
                        border_width=1, border_color=BORDER, **kwargs)


class HeroBanner(tk.Canvas):
    """Paysage PhantomStation avec dégradé pour garder les titres lisibles."""
    def __init__(self, parent):
        super().__init__(parent, height=112, bg=BG, highlightthickness=0, bd=0)
        self.original = Image.open(BASE / 'assets' / 'phantom-startup.png').convert('RGB')
        self.logo_source = Image.open(BASE / 'assets' / 'phantomstation-symboles.png').convert('RGBA')
        self.bind('<Configure>', self.redraw)

    def redraw(self, event):
        w, h = max(1, event.width), max(1, event.height)
        background = ImageOps.fit(self.original, (w, h), Image.Resampling.LANCZOS, centering=(.5, .42)).convert('RGBA')
        shade = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(shade)
        for x in range(w):
            alpha = round(228 - 75 * min(1, x / max(1, w * .65)))
            draw.line((x, 0, x, h), fill=(3, 10, 25, alpha))
        self.background_image = ImageTk.PhotoImage(Image.alpha_composite(background, shade))
        logo = self.logo_source.copy()
        logo.thumbnail((48, 48), Image.Resampling.LANCZOS)
        self.logo_image = ImageTk.PhotoImage(logo)
        self.delete('all')
        self.create_image(0, 0, image=self.background_image, anchor='nw')
        self.create_image(29, 28, image=self.logo_image, anchor='nw')
        self.create_text(93, 25, text='PHANTOMSTATION', fill='#f3f8ff', anchor='nw', font=('Segoe UI', 23, 'bold'))
        self.create_text(94, 67, text='Ton univers rétro, réinventé.', fill='#a8c9dc', anchor='nw', font=('Segoe UI', 11))
        self.create_line(30, h-2, w-30, h-2, fill='#245671', width=1)
