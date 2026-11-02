"""Introduction PhantomStation, fondée sur les éléments du projet d'origine."""
import time
import tkinter as tk
from tkinter import ttk
from pathlib import Path
import winsound

from PIL import Image, ImageDraw, ImageOps, ImageTk
from phantom_window_icon import set_window_icon
from phantom_ui import tr

ASSETS = Path(__file__).resolve().parent / 'assets'
DURATION = 8.4


def visible_ps2_logo(image):
    """Make the black PS2 wordmark legible on PhantomStation's dark artwork."""
    image = image.convert('RGBA')
    pixels = bytearray(image.tobytes())
    for pos in range(0, len(pixels), 4):
        red, green, blue, alpha = pixels[pos:pos + 4]
        if alpha and max(red, green, blue) < 45:
            pixels[pos:pos + 3] = bytes((222, 241, 255))
    return Image.frombytes('RGBA', image.size, bytes(pixels))


class CompilationProgress:
    """Progression globale pondérée par étapes, toujours croissante.

    Les bornes sont indicatives : un décompte Ninja ou d'octets mesure une
    étape, et non le temps restant pour toute la compilation.
    """
    STAGES = (
        ('Vérification des disques', 0, 4),
        ('Création et génération', 4, 26),
        ('Enregistrement des disques', 26, 30),
        ('Compilation Windows', 30, 70),
        ('Création du fichier unique', 70, 84),
        ('Vérification du fichier unique', 84, 96),
        ('Nettoyage des fichiers temporaires', 96, 98),
    )

    def __init__(self):
        self.value = 0.0
        self.label = 'Préparation du projet'

    @classmethod
    def bounds(cls, label):
        for prefix, start, end in cls.STAGES:
            if str(label).startswith(prefix):
                return start, end
        return None

    def phase(self, label):
        self.label = str(label).rstrip('…. ')
        bounds = self.bounds(self.label)
        if bounds:
            self.value = max(self.value, float(bounds[0]))
        return self.value

    def advance(self, completed, total, label):
        self.phase(label)
        bounds = self.bounds(self.label)
        if bounds and total > 0:
            ratio = max(0.0, min(1.0, completed / total))
            self.value = max(self.value, bounds[0] + (bounds[1] - bounds[0]) * ratio)
        return self.value

    def text(self, value=None):
        label = self.label
        for prefix, _, _ in self.STAGES:
            if label.startswith(prefix):
                label = tr(prefix) + label[len(prefix):]
                break
        else:
            if label.startswith('Préparation du jeu ') and tr('Compilation en cours') == 'Building':
                label = 'Preparing ' + label[len('Préparation du jeu '):] + ' game'
            else:
                label = tr(label)
        suffix = 'estimated progress' if tr('Compilation en cours') == 'Building' else 'progression indicative'
        return f'{label} — {int(self.value if value is None else value)} % ({suffix})'


class PhantomIntro:
    def __init__(self, parent, message, on_finish=None, progress=False, on_hide=None, on_report=None, startup_sound=None, logo_path=None):
        self.on_finish = on_finish
        self.on_hide = on_hide
        self.on_report = on_report
        self.progress = progress
        self.completed = False
        self.success = False
        self.closed = False
        self.started = time.monotonic()
        self.window = tk.Toplevel(parent, background='#030814')
        self.window.title('PhantomStation')
        set_window_icon(self.window)
        self.window.protocol('WM_DELETE_WINDOW', self.request_close)
        self.window.bind('<Escape>', lambda _event: self.request_close())
        sw, sh = self.window.winfo_screenwidth(), self.window.winfo_screenheight()
        self.width = min(1100, max(640, sw - 80))
        self.height = min(650, max(400, sh - 100))
        self.window.geometry(f'{self.width}x{self.height}+{max(0, (sw-self.width)//2)}+{max(0, (sh-self.height)//2)}')
        self.window.resizable(False, False)
        self.window.attributes('-topmost', True)
        if progress:
            self.window.after(1500, lambda: self.window.attributes('-topmost', False) if not self.closed else None)
        self.canvas = tk.Canvas(self.window, width=self.width, height=self.height,
                                highlightthickness=0, background='#030814')
        self.canvas.pack(fill='both', expand=True)
        self.source = Image.open(ASSETS / 'phantom-startup.png').convert('RGB')
        self.logo = Image.open(logo_path or ASSETS / 'playstation-logo.png').convert('RGBA')
        if logo_path and Path(logo_path).name.lower() == 'logo-ps2.png':
            self.logo = visible_ps2_logo(self.logo)
        scale = max(self.width/self.source.width, self.height/self.source.height)
        w, h = round(self.source.width*scale), round(self.source.height*scale)
        background = self.source.resize((w, h), Image.Resampling.LANCZOS)
        x = max(0, (w-self.width)//2)
        y = max(0, min(h-self.height, round((h-self.height)*.42)))
        background = background.crop((x, y, x+self.width, y+self.height)).convert('RGBA')
        shade = Image.new('RGBA', background.size, (0, 0, 0, 0))
        shade_draw = ImageDraw.Draw(shade)
        for line in range(int(self.height*.44), self.height, 4):
            alpha = int(225 * ((line/self.height-.44)/.56)**1.5)
            shade_draw.rectangle((0, line, self.width, line+4), fill=(2, 5, 14, max(0, min(alpha, 225))))
        self.background = Image.alpha_composite(background, shade)
        self.canvas_image = self.canvas.create_image(0, 0, anchor='nw')
        self.message = message
        self.canvas.create_text(self.width // 2, int(self.height*.70), text='B I E N V E N U E   D A N S',
                                fill='#a9d9ec', font=('Segoe UI', 11, 'bold'))
        self.canvas.create_text(self.width // 2, int(self.height*.77), text='PHANTOMSTATION',
                                fill='#f4fbff', font=('Segoe UI', max(24, self.width//30), 'bold'))
        self.canvas.create_line(self.width*.34, self.height*.82, self.width*.66, self.height*.82,
                                fill='#95f4e4')
        self.canvas.create_text(self.width // 2, int(self.height*.86), text='U N E   N O U V E L L E   V I E   P O U R   T E S   J E U X',
                                fill='#bed8e7', font=('Segoe UI', 9))
        self.canvas.create_text(self.width // 2, int(self.height*.90), text='DEV BY NOVALYS',
                                fill='#9af4e3', font=('Segoe UI', 10))
        if progress:
            self.progress_model = CompilationProgress()
            self.progress_model.phase(message)
            self.display_value = 0.0
            self.phase_text = self.canvas.create_text(self.width // 2, int(self.height*.65), text=message,
                fill='#d9f8f1', font=('Segoe UI', 10), width=int(self.width*.75))
            style = ttk.Style(self.window)
            style.configure('Phantom.Horizontal.TProgressbar', background='#39aaf2',
                            troughcolor='#10243a', bordercolor='#315b7a',
                            lightcolor='#39aaf2', darkcolor='#39aaf2',
                            borderwidth=1, thickness=14)
            self.progressbar = ttk.Progressbar(self.window, style='Phantom.Horizontal.TProgressbar',
                                               mode='determinate', maximum=100, value=0, length=int(self.width*.42))
            self.canvas.create_window(self.width//2, int(self.height*.60), window=self.progressbar)
            self.canvas.itemconfigure(self.phase_text, text=self.progress_model.text())
            self.window.after(40, self.animate_progress)
            self.close_button = tk.Button(self.window, text=tr('Terminé'), command=self.finish, state='disabled',
                bg='#182b39', fg='#e4f8f5', activebackground='#284453', disabledforeground='#72828c',
                activeforeground='white', relief='flat', padx=18, pady=7)
            if on_report:
                self.report_button = tk.Button(self.window, text=tr('Envoyer le rapport'), command=on_report,
                    bg='#7f2635', fg='#fff5f5', activebackground='#ab3d4d', activeforeground='white',
                    relief='flat', padx=14, pady=7)
                self.report_button_item = self.canvas.create_window(self.width-120, self.height-12,
                    anchor='se', window=self.report_button, state='hidden')
        else:
            self.canvas.create_text(self.width // 2, int(self.height*.94), text=message,
                                    fill='#c3e7ee', font=('Segoe UI', 9))
            self.close_button = tk.Button(self.window, text=tr('Passer l’introduction'), command=self.finish,
                bg='#182b39', fg='#e4f8f5', activebackground='#284453',
                activeforeground='white', relief='flat', padx=12, pady=6)
        self.canvas.create_window(self.width-15, self.height-12, anchor='se', window=self.close_button)
        try:
            winsound.PlaySound(str(startup_sound or ASSETS / 'son-demarrage.wav'), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
        except (RuntimeError, OSError):
            pass
        self.draw()

    def draw(self):
        if self.closed: return
        elapsed = time.monotonic() - self.started
        if elapsed >= DURATION and not self.progress:
            self.finish()
            return
        # Keep the background still while the logo and text appear.
        image = self.background.copy()
        logo = self.logo.copy()
        logo.thumbnail((round(self.width*.40), round(self.height*.27)), Image.Resampling.LANCZOS)
        opacity = min(1.0, elapsed/2.0)
        logo.putalpha(logo.getchannel('A').point(lambda v: round(v*opacity)))
        logo_y = round(self.height*.345 - logo.height/2)
        image.alpha_composite(logo, ((self.width-logo.width)//2, logo_y))
        self.frame_image = image
        self.photo = ImageTk.PhotoImage(image)
        self.canvas.itemconfigure(self.canvas_image, image=self.photo)
        if elapsed > 3.5:
            self.canvas.delete('playstation-word')
            alpha = min(1.0, (elapsed-3.5)/1.2)
            color = '#%02x%02x%02x' % tuple(round(a+(b-a)*alpha) for a,b in zip((12,19,30),(243,244,250)))
            self.canvas.create_text(self.width//2, int(self.height*.505),
                                    text=(tr('Compilation terminée') if self.success else tr('Compilation interrompue')) if self.progress and self.completed else (tr('Compilation en cours') if self.progress else 'PlayStation'), fill=color,
                                    font=('Segoe UI', 24 if self.progress else 26, 'bold'), tags='playstation-word')
        if elapsed < 5 or not self.progress:
            self.window.after(70, self.draw)

    def set_phase(self, message):
        if self.progress and not self.closed and not self.completed:
            self.progress_model.phase(message)
            self.canvas.itemconfigure(self.phase_text, text=self.progress_model.text(self.display_value))

    def set_progress(self, completed, total, label):
        if self.progress and not self.closed and not self.completed and total > 0:
            self.progress_model.advance(completed, total, label)
            self.canvas.itemconfigure(self.phase_text, text=self.progress_model.text(self.display_value))

    def animate_progress(self):
        if self.closed or self.completed:
            return
        target = self.progress_model.value
        self.display_value = min(target, self.display_value + min(0.45, max(0.05, (target - self.display_value) * .08)))
        self.progressbar.configure(value=self.display_value)
        self.canvas.itemconfigure(self.phase_text, text=self.progress_model.text(self.display_value))
        self.window.after(40, self.animate_progress)

    def complete(self, success, message):
        if not self.progress or self.closed:
            return
        self.completed = True
        self.success = success
        self.window.deiconify()
        self.window.lift()
        if not success:
            red = ImageOps.colorize(ImageOps.grayscale(self.background.convert('RGB')),
                                    black=(20, 0, 0), white=(230, 35, 35)).convert('RGBA')
            self.background = Image.blend(self.background, red, 0.82)
            self.draw()
            style = ttk.Style(self.window)
            style.configure('PhantomError.Horizontal.TProgressbar', background='#ed5454',
                            troughcolor='#40151a', bordercolor='#a64848',
                            lightcolor='#ed5454', darkcolor='#ed5454', borderwidth=1, thickness=14)
            self.progressbar.configure(style='PhantomError.Horizontal.TProgressbar')
            try:
                winsound.PlaySound(None, 0)
                winsound.PlaySound(str(ASSETS / 'son-erreur-ps2.wav'),
                                   winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
            except (RuntimeError, OSError):
                pass
        self.canvas.delete('playstation-word')
        self.canvas.create_text(self.width//2, int(self.height*.505),
                                text=tr('Compilation terminée') if success else tr('Compilation interrompue'),
                                fill='#f3f4fa', font=('Segoe UI', 24, 'bold'), tags='playstation-word')
        self.progressbar.configure(value=100 if success else self.progressbar['value'])
        failure = 'Failed at' if tr('Compilation en cours') == 'Building' else 'Échec à'
        result = f'{tr(message)} — 100 %' if success else f'{failure} {int(self.display_value)} % — {tr(message)}'
        self.canvas.itemconfigure(self.phase_text, text=result, fill='#a7f5d4' if success else '#ffe4df')
        self.close_button.configure(state='normal', text=tr('Terminé'))
        if not success and self.on_report:
            self.canvas.itemconfigure(self.report_button_item, state='normal')
        self.window.attributes('-topmost', False)

    def request_close(self):
        if self.progress and not self.completed:
            self.window.iconify()
            if self.on_hide: self.on_hide()
        else:
            self.finish()

    def show(self):
        if not self.closed:
            self.window.deiconify()
            self.window.lift()

    def finish(self):
        if self.closed: return
        if self.progress and not self.completed: return
        self.closed = True
        try: winsound.PlaySound(None, 0)
        except (RuntimeError, OSError): pass
        self.window.destroy()
        if self.on_finish: self.on_finish()
