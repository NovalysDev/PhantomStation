"""Composants visuels partagés du compilateur PhantomStation."""
import os
import sys
import tempfile
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

# The choice belongs to the Windows user, independently of game/save folders.
LANGUAGE_FILE = Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData/Local') / 'PhantomStation' / 'language.txt'

def load_language():
    try:
        return 'en' if LANGUAGE_FILE.read_text(encoding='ascii').strip() == 'en' else 'fr'
    except (OSError, UnicodeError):
        return 'fr'

LANGUAGE = load_language()

def set_language(language):
    global LANGUAGE
    if language not in ('fr', 'en'):
        raise ValueError('Unsupported language')
    LANGUAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp = None
    try:
        with tempfile.NamedTemporaryFile('w', encoding='ascii', dir=LANGUAGE_FILE.parent, delete=False) as stream:
            temp = Path(stream.name)
            stream.write(language + '\n')
        os.replace(temp, LANGUAGE_FILE)
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)
    LANGUAGE = language

ENGLISH = {
    'Carte mémoire': 'Memory card', 'Carte mémoire 2': 'Memory card 2', 'Sauvegarde actuelle du jeu': 'Game’s current save',
    'Nom affiché dans PhantomStation :': 'Name shown in PhantomStation:',
    'Choisis d’abord une carte ou un profil dans la liste.': 'First choose a card or profile from the list.',
    'Cet emplacement est vide : choisis une carte à renommer.': 'This slot is empty: choose a card to rename.',
    'Profil de sauvegarde': 'Save profile', 'Ajouter une carte': 'Add a memory card',
    'Créer une carte vierge ?\n\nOui : créer une carte.\nNon : importer un fichier existant.': 'Create a blank card?\n\nYes: create a card.\nNo: import an existing file.',
    'Choisir une carte mémoire': 'Choose a memory card', 'Cartes mémoire': 'Memory cards',
    'Nouveau profil': 'New profile', 'Nom du profil :': 'Profile name:', 'Nom de la carte :': 'Card name:',
    'Sélection impossible': 'Unable to select', 'Création impossible': 'Unable to create',
    'Copies de la carte partagée': 'Shared card backups',
    'Les pages PS1 concernent uniquement la carte par défaut du jeu.': 'PS1 pages apply only to the game’s default card.',
    '⚙   Paramètres': '⚙   Settings', 'Paramètres': 'Settings',
    'Un sous-dossier est créé automatiquement pour chaque console.': 'A subfolder is created automatically for each console.',
    'Changer le dossier des jeux': 'Change games folder',
    'Ouvrir le dossier de la console': 'Open console folder',
    'Supprimer': 'Delete', 'Supprimer le jeu': 'Delete game', 'Suppression impossible': 'Unable to delete game',
    'Déplacer les jeux': 'Move games', 'Déplacement en cours': 'Moving games',
    'Déplacement des jeux en cours…': 'Moving games…',
    'Attends la fin du déplacement des jeux.': 'Wait for the games to finish moving.',
    'Attends la fin de l’opération en cours.': 'Wait for the current operation to finish.',
    '\nLes jeux déjà déplacés restent accessibles dans la bibliothèque.': '\nGames already moved remain accessible in the library.',
    'Dreamcast : fichier .chd. Mode expérimental : certains jeux peuvent ne pas fonctionner.': 'Dreamcast: .chd file. Experimental mode: some games may not work.',
    'Vidéo et son expérimental dans le lanceur.': 'Video and experimental audio in the launcher.',
    'Carte mémoire Dreamcast : les sauvegardes dépendent du jeu.': 'Dreamcast memory card: saves depend on the game.',
    'PhantomStation — Utilisation responsable': 'PhantomStation — Responsible use',
    'Utilisation responsable': 'Responsible use', 'Quitter': 'Quit', 'J’accepte et je continue': 'I agree and continue',
    'PhantomStation doit être utilisé dans le respect des lois et des droits des auteurs. Utilise uniquement des jeux, BIOS et autres fichiers que tu es autorisé à utiliser.\n\nChaque utilisateur est responsable de ses propres fichiers et de l’usage qu’il fait du logiciel. Toute utilisation illégale est interdite.\n\nDans les limites prévues par la loi, le créateur décline toute responsabilité pour une utilisation illégale du logiciel par un utilisateur.': 'PhantomStation must be used in accordance with the law and the rights of authors. Only use games, BIOS files and other files you are authorized to use.\n\nEach user is responsible for their own files and their use of the software. Illegal use is prohibited.\n\nTo the extent permitted by law, the creator disclaims responsibility for illegal use of the software by a user.',
    'Enregistrement impossible': 'Unable to save', 'Impossible de mémoriser ton choix. Vérifie les droits du dossier PhantomStation.': 'Unable to remember your choice. Check permissions for the PhantomStation folder.',
    'PhantomStation — Recompilation de jeux rétro': 'PhantomStation — Retro game recompilation',
    'Ton univers rétro, réinventé.': 'Your retro world, reimagined.',
    'TON ESPACE': 'YOUR SPACE', '◈   Compilation': '◈   Build', '▦   Jeux': '▦   Games',
    '?   Mode d’emploi': '?   User guide', 'ATELIER PHANTOMSTATION': 'PHANTOMSTATION WORKSHOP',
    'Créer un jeu': 'Create a game', 'Choisis tes disques et prépare ton jeu en quelques étapes.': 'Choose your discs and prepare your game in a few steps.',
    '01   TON JEU': '01   YOUR GAME', 'Nom du jeu': 'Game title', '＋  Ajouter un ou plusieurs disques': '＋  Add one or more discs',
    'Aucun disque sélectionné.': 'No disc selected.', 'Retirer le dernier': 'Remove last', 'Tout retirer': 'Remove all',
    '02   OPTIONS DE CRÉATION': '02   BUILD OPTIONS', 'Joueurs': 'Players', 'Résolution': 'Resolution',
    'BIOS PhantomStation (expérimental)': 'PhantomStation BIOS (experimental)',
    'Nombre maximal de manettes ; le jeu choisit son mode multijoueur.': 'Maximum controllers; the game determines its multiplayer mode.',
    'Dossier des jeux': 'Games folder', 'Ouvrir': 'Open', 'Choisir la jaquette': 'Choose cover',
    'Choisir le fond': 'Choose background', 'Jaquette automatique si disponible': 'Cover found automatically when available',
    '✦  CRÉER LE PROGRAMME': '✦  CREATE PROGRAM', 'Ouvrir le projet': 'Open project', 'Choisir un projet': 'Choose project',
    'Lancer le programme': 'Launch program', 'En attente': 'Ready', 'JOURNAL DE COMPILATION': 'BUILD LOG',
    'Suivi et détails': 'Progress and details', 'Envoyer le rapport': 'Send report', 'BIENVENUE': 'WELCOME',
    'Mode d’emploi': 'User guide', 'TA COLLECTION': 'YOUR COLLECTION', 'Bibliothèque PS1': 'PS1 library',
    'Actualiser': 'Refresh', '+ Ajouter un jeu': '+ Add a game', 'Changer de dossier': 'Change folder',
    'Choisir la console': 'Choose a console', '▶  Ouvrir le lanceur': '▶  Open launcher', 'Sauvegardes': 'Saves',
    'Renommer': 'Rename', 'SAUVEGARDES': 'SAVES', 'Copies de secours': 'Backups',
    'Ouvrir le dossier des sauvegardes': 'Open saves folder', 'Créer une copie de secours': 'Create backup',
    'Restaurer une copie de secours': 'Restore backup', 'Fermer': 'Close', 'Changer de page': 'Change page',
    'Carte mémoire  ·  240 blocs': 'Memory card  ·  240 blocks', 'Carte mémoire': 'Memory card',
    '16 pages de 15 blocs. Choisis une page avant de lancer le jeu.': '16 pages of 15 blocks. Choose a page before starting the game.',
    'Une copie est créée avant de lancer depuis la bibliothèque.\nFerme le jeu avant de copier ou restaurer.': 'A backup is created before launching from the library.\nClose the game before copying or restoring saves.',
    'Carte mémoire PS2 : les sauvegardes dépendent du jeu.': 'PS2 memory card: saves depend on the game.',
    'Les sauvegardes dépendent de la cartouche et du jeu.': 'Saves depend on the cartridge and game.',
    'Réglages et commandes : F10 pendant le jeu.': 'Settings and controls: F10 in game.',
    'Réglages et commandes : F2 pendant le jeu.': 'Settings and controls: F2 in game.',
    'Codes, vidéo et commandes dans le lanceur.': 'Cheats, video and controls are in the launcher.',
    'Choisir le jeu': 'Choose a game', 'Tous les fichiers': 'All files', 'Images PS1': 'PS1 images', 'Image PS2': 'PS2 image',
    'Cartouche NES': 'NES cartridge', 'Cartouche Super Nintendo': 'Super Nintendo cartridge', 'Cartouche Nintendo 64': 'Nintendo 64 cartridge',
    'Jeu PS1 détecté': 'PS1 game detected', 'Jeu PS2 détecté': 'PS2 game detected',
    'Cette image est un jeu PS1. Sélectionne PS1 pour l’ajouter.': 'This image is a PS1 game. Select PS1 to add it.',
    'Cette image est un jeu PS2. Sélectionne PS2 : une sélection PS2 existe déjà et a été conservée.': 'This image is a PS2 game. Select PS2: your existing PS2 selection was kept.',
    'Ces disques sont déjà dans la sélection.': 'These discs are already selected.', 'PS1 sélectionnée.': 'PS1 selected.',
    'Jeu PS2 détecté : console PS2 sélectionnée automatiquement.': 'PS2 game detected: PS2 selected automatically.',
    'Jeu déjà présent': 'Game already present', 'Choisir un projet déjà créé': 'Choose an existing project',
    'Choisir le dossier des jeux compilés': 'Choose the compiled games folder', 'Choisir une image de fond pour le jeu': 'Choose a game background',
    'Choisir une jaquette': 'Choose a cover', 'Impossible de lancer le jeu': 'Unable to launch the game',
    'Programme indisponible': 'Program unavailable', 'Dossier inaccessible': 'Folder unavailable', 'Dossier non mémorisé': 'Folder not saved',
    'Sélectionne au moins un disque.': 'Select at least one disc.', 'Sélectionne une seule image ISO PS2 pour ce jeu.': 'Select one PS2 ISO image for this game.',
    'Sélectionne une seule cartouche au format indiqué pour cette console.': 'Select one cartridge in the indicated format for this console.',
    'Raccourci sur le bureau': 'Desktop shortcut', 'Images du jeu d’origine': 'Original game images',
    'Fichiers du jeu d’origine': 'Original game files', 'Dossier conservé': 'Folder kept', 'Déplacement interrompu': 'Move interrupted',
    'Fichier SBI manquant': 'Missing SBI file', 'Jeu créé — compatibilité à vérifier en partie.': 'Game created — test compatibility in game.',
    'Programme PS2 créé — compatibilité à vérifier.': 'PS2 program created — test compatibility.',
    'Jeu PS2 créé : lance-le depuis la bibliothèque PhantomStation (fichier supérieur à 4 Gio).': 'PS2 game created: launch it from the PhantomStation library (file exceeds 4 GiB).',
    'Échec de la compilation — détails dans le journal.': 'Build failed — see the log for details.',
    'Échec de la compilation Nintendo — voir le journal.': 'Nintendo build failed — see the log.',
    'Échec de la compilation PS2 — voir le journal.': 'PS2 build failed — see the log.',
    'Fichier unique créé — SBI manquant.': 'Single file created — SBI missing.',
    'Fichier unique créé — compatibilité en jeu à vérifier.': 'Single file created — test compatibility in game.',
    'Échec ou préparation incomplète — voir le journal.': 'Build failed or preparation incomplete — see the log.',
    'Préparation du projet…': 'Preparing the project…', 'Préparation du projet PS2…': 'Preparing the PS2 project…',
    'Création et génération PS2…': 'Creating and generating PS2…', 'Création du fichier unique…': 'Creating the single file…',
    'Compilation en cours': 'Building', 'Compilation terminée': 'Build complete', 'Compilation interrompue': 'Build interrupted',
    'Préparation du projet': 'Preparing project', 'Vérification des disques': 'Checking discs',
    'Création et génération': 'Creation and generation', 'Enregistrement des disques': 'Recording discs',
    'Compilation Windows': 'Windows build', 'Création du fichier unique': 'Creating single file',
    'Vérification du fichier unique': 'Checking single file', 'Nettoyage des fichiers temporaires': 'Cleaning temporary files',
    'Terminé': 'Done', 'Passer l’introduction': 'Skip introduction',
    'Rapport d’erreur — PhantomStation': 'Error report — PhantomStation', 'Envoyer le rapport d’erreur': 'Send error report',
    'Vérifie le contenu avant l’envoi direct.': 'Review the contents before sending.', 'Rapport à envoyer': 'Report to send',
    'Ouvrir le dossier': 'Open folder', 'Copier le rapport': 'Copy report', 'Rapport transmis': 'Report sent',
    'Envoi en cours…': 'Sending…', 'Rapport copié': 'Report copied', 'Envoi impossible': 'Unable to send',
    'Mise à jour — PhantomStation': 'Update — PhantomStation', 'Une mise à jour est disponible': 'An update is available',
    'Mise à jour impossible': 'Unable to update', 'Tes jeux, sauvegardes et réglages sont conservés.': 'Your games, saves and settings are kept.',
    'Préparation de l’installation et du redémarrage…': 'Preparing installation and restart…',
    '← Retour au compilateur': '← Back to the compiler', 'Ouverture du jeu…': 'Opening game…',
    'Le jeu est ouvert dans PhantomStation.': 'The game is open in PhantomStation.', 'Fermeture du jeu…': 'Closing game…',
    'Fenêtre du jeu': 'Game window', 'Changer de langue': 'Change language',
    'La langue ne peut pas être changée pendant une compilation.': 'The language cannot be changed during a build.',
    'Impossible d’enregistrer la langue': 'Unable to save the language',
    'Dossier : ': 'Folder: ', 'Dossier des jeux : ': 'Games folder: ', 'Image choisie : ': 'Image selected: ', 'Fond choisi : ': 'Background selected: ',
    ' expérimental : réglages disponibles selon le jeu.': ' experimental: settings depend on the game.',
    'Le dossier contient peut-être d’autres fichiers. Veux-tu déplacer uniquement ces fichiers d’origine dans la Corbeille ?\n\n': 'This folder may contain other files. Move only these original files to the Recycle Bin?\n\n',
    '\n\nLe fonctionnement du jeu doit encore être testé. Tu peux répondre Non pour les garder.': '\n\nThe game still needs to be tested. Choose No to keep the originals.',
    'Le jeu a été créé et son fichier vérifié. Veux-tu déplacer ce dossier et tout son contenu dans la Corbeille ?\n\n': 'The game has been created and its file verified. Move this folder and all its contents to the Recycle Bin?\n\n',
    '\n\nLe fonctionnement du jeu doit encore être testé. Tu peux répondre Non pour garder les originaux.': '\n\nThe game still needs to be tested. Choose No to keep the originals.',
    'Le jeu a été créé, mais un fichier SBI nécessaire n’a pas été trouvé pour :\n\n': 'The game was created, but a required SBI file was not found for:\n\n',
    '\n\nDans le lanceur du jeu, utilise « Ajouter le fichier SBI » pour sélectionner le fichier correspondant.': '\n\nIn the game launcher, use “Add SBI file” to select the matching file.',
    'Raccourci impossible': 'Unable to create shortcut', 'Raccourci créé sur le bureau : ': 'Desktop shortcut created: ',
    'Fichiers d’origine déplacés dans la Corbeille.': 'Original files moved to the Recycle Bin.',
    'Images d’origine déplacées dans la Corbeille : ': 'Original images moved to the Recycle Bin: ',
    '\nVérifie les fichiers déjà présents dans la Corbeille.': '\nCheck which files are already in the Recycle Bin.',
    'Choisir une jaquette ou image': 'Choose a cover or image', 'Icône du projet ou jaquette automatique': 'Project icon or automatic cover',
    'Projet sélectionné : ': 'Project selected: ', 'Compilation en cours…': 'Building…', 'Compilation PS2 en cours…': 'Building PS2…',
    'Disques': 'Discs', 'Outils manquants': 'Missing tools', 'Nom': 'Name', 'Indique le nom du jeu.': 'Enter the game title.',
    'Disque PS2': 'PS2 disc', 'Cartouche Nintendo': 'Nintendo cartridge', 'Son PS2 requis': 'PS2 sound required', 'Son PS2 invalide': 'Invalid PS2 sound',
    'Choisir le son de démarrage PS2': 'Choose the PS2 startup sound', 'Sélectionne un son de démarrage PS2 au format WAV pour continuer.': 'Select a PS2 startup sound in WAV format to continue.',
    'Impossible d’utiliser ce fichier WAV :\n': 'Unable to use this WAV file:\n', 'Impossible d’écrire dans le dossier des jeux :\n': 'Unable to write to the games folder:\n',
    'Ouvre le fichier EXE dans le dossier des jeux. Les fichiers de compilation peuvent avoir été nettoyés après sa création.': 'Open the EXE in the games folder. Intermediate build files may have been cleaned up.',
    'Le dossier est utilisé pour cette session, mais n’a pas pu être mémorisé :\n': 'The folder will be used for this session but could not be saved:\n',
    'Impossible de retrouver le jeu recompilé.': 'The recompiled game could not be found.',
    'Ces dossiers ont été conservés : leur contenu a changé, ils contiennent des sauvegardes ou des liens, sont trop volumineux à afficher ou correspondent à un emplacement partagé :\n\n': 'These folders were kept because their contents changed, they contain saves or links, they are too large to list, or they are shared locations:\n\n',
    'Le dossier PSXRecomp ou le compilateur est manquant.': 'The PSXRecomp folder or compiler is missing.',
    'Un jeu qui pourrait déjà correspondre existe dans ta bibliothèque :\n\n': 'A game that may already match exists in your library:\n\n',
    '\n\nVérifie la version et la console. Veux-tu quand même créer un autre fichier ?': '\n\nCheck the version and console. Create another file anyway?',
    '\n\nRapport non enregistré : ': '\n\nReport not saved: ', 'Rapport non enregistré : ': 'Report not saved: ',
    'Ajouter des jeux PhantomStation': 'Add PhantomStation games', 'Jeux Windows': 'Windows games', 'Jeux non ajoutés': 'Games not added',
    'Renommer le jeu': 'Rename game', 'Nouveau nom du jeu :': 'New game title:', 'Renommage impossible': 'Unable to rename',
    'Jeu introuvable': 'Game not found', 'Le fichier du jeu a été déplacé ou supprimé.': 'The game file was moved or deleted.',
    'Lancement impossible': 'Unable to launch', 'Changement impossible': 'Unable to change',
    'Lance une première fois le jeu pour créer ses sauvegardes.': 'Launch the game once to create its saves.',
    'Copie de secours': 'Backup', 'Copie impossible': 'Unable to back up', 'Copie créée :\n': 'Backup created:\n',
    'Aucune sauvegarde à copier pour le moment.': 'There are no saves to back up yet.',
    'Restauration': 'Restore', 'Aucune copie de secours trouvée pour ce jeu.': 'No backups were found for this game.',
    'Choisir une copie de secours': 'Choose a backup', 'Copies de secours': 'Backups', 'Restaurer les sauvegardes': 'Restore saves',
    'Restaurer cette copie ? Une copie des sauvegardes actuelles sera créée avant le remplacement.': 'Restore this backup? The current saves will be backed up before replacement.',
    'Restauration terminée': 'Restore complete', 'Sauvegardes restaurées.': 'Saves restored.', 'Restauration impossible': 'Unable to restore',
    '\nCopie précédente : ': '\nPrevious backup: ',
    'Rapport impossible': 'Unable to prepare report', 'Impossible d’enregistrer le rapport :\n': 'Unable to save the report:\n',
    'Rapport enregistré : ': 'Report saved: ', 'Le rapport complet est copié. Tu peux le coller dans ton e-mail.': 'The full report was copied. You can paste it into your email.',
    'Le service Novalys a confirmé la réception du rapport.': 'The Novalys service confirmed receipt of the report.',
    '\n\nLe rapport reste enregistré sur ce PC.': '\n\nThe report remains saved on this PC.',
    'Téléchargement de la version ': 'Downloading version ', '\n\nTu peux continuer à utiliser PhantomStation.': '\n\nYou can continue using PhantomStation.',
    'PHANTOMSTATION  /  MISE À JOUR': 'PHANTOMSTATION  /  UPDATE',
    'PS1 : .cue, .bin ou .iso\nPlusieurs pistes : choisis le .cue et garde tous les .bin à côté.\nArchive .7z : extrais-la d’abord · .chd non pris en charge.': 'PS1: .cue, .bin or .iso\nFor multiple tracks, choose the .cue and keep every .bin beside it.\nExtract .7z archives first · .chd is not supported.',
    'PS2 : une image .iso par jeu\nArchive .7z : extrais-la d’abord pour obtenir l’ISO.\nLe format .chd n’est pas pris en charge.': 'PS2: one .iso image per game\nExtract .7z archives first to obtain the ISO.\nThe .chd format is not supported.',
    'NES : une cartouche .nes par jeu.\nArchive .7z : extrais-la d’abord.\nCompatibilité et réglages à vérifier selon le jeu.': 'NES: one .nes cartridge per game.\nExtract .7z archives first.\nCompatibility and settings vary by game.',
    'Super Nintendo : une cartouche .sfc ou .smc par jeu.\nArchive .7z : extrais-la d’abord.\nCompatibilité et réglages à vérifier selon le jeu.': 'Super Nintendo: one .sfc or .smc cartridge per game.\nExtract .7z archives first.\nCompatibility and settings vary by game.',
    'Nintendo 64 : fichiers .z64, .v64 ou .n64.\nMoteur hybride expérimental : la compatibilité dépend du jeu.': 'Nintendo 64: .z64, .v64 or .n64 files.\nExperimental hybrid engine: compatibility depends on the game.',
}
_FRENCH = {v: k for k, v in ENGLISH.items()}
_PREFIXES = ('Dossier : ', 'Dossier des jeux : ', 'Image choisie : ', 'Fond choisi : ',
             'Projet sélectionné : ', 'Raccourci créé sur le bureau : ',
             'Images d’origine déplacées dans la Corbeille : ', 'Rapport enregistré : ')

def tr(text):
    if not isinstance(text, str):
        return text
    if LANGUAGE == 'en':
        if text in ENGLISH:
            return ENGLISH[text]
        for prefix in _PREFIXES:
            if text.startswith(prefix):
                return ENGLISH[prefix] + text[len(prefix):]
        return text
    if text in _FRENCH:
        return _FRENCH[text]
    for prefix in _PREFIXES:
        translated = ENGLISH[prefix]
        if text.startswith(translated):
            return prefix + text[len(translated):]
    return text


def button(parent, text, command, *, primary=False, **kwargs):
    return ctk.CTkButton(parent, text=tr(text), command=command, height=36, corner_radius=12,
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
        w, h = (max(1, event.width), max(1, event.height)) if event is not None else (max(1, self.winfo_width()), max(1, self.winfo_height()))
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
        self.create_text(94, 67, text=tr('Ton univers rétro, réinventé.'), fill='#a8c9dc', anchor='nw', font=('Segoe UI', 11))
        self.create_line(30, h-2, w-30, h-2, fill='#245671', width=1)
