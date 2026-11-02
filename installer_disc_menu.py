"""Met en français le menu de changement de disque pendant la partie."""
from pathlib import Path


def install_disc_menu_fr(project):
    root = Path(project)
    paths = (
        root / 'recomp-ui/src/consoles/psx/psx_menu_provider.cpp',
        root / 'psxrecomp/runtime/src/psx_runtime_menu.c',
    )
    replacements = {
        '"Disc", "Selected disc"': '"Disque", "Disque à insérer"',
        '"Disc", "Insert disc and resume"': '"Disque", "Insérer le disque et reprendre"',
        '"Choose a disc, then Insert disc and resume."': '"Choisissez un disque puis insérez-le pour reprendre."',
        '"Choose a disc, then select Insert disc and resume."': '"Choisissez un disque puis insérez-le pour reprendre."',
        '"Change the disc while the game keeps running."': '"Changer de disque sans fermer le jeu."',
        '"Change the disc while keeping your game running."': '"Changer de disque sans fermer le jeu."',
        '"Mounted: Disc %d"': '"Disque inséré : %d"',
        '"Game menu"': '"Menu du jeu"',
    }
    for path in paths:
        source = path.read_text(encoding='utf-8')
        if 'PHANTOM_DISC_MENU_FR_V1' in source:
            continue
        if '"Disc", "Selected disc"' not in source or '"Disc", "Insert disc and resume"' not in source:
            raise ValueError('Menu de changement de disque introuvable : ' + str(path))
        for old, new in replacements.items():
            source = source.replace(old, new)
        path.write_text('// PHANTOM_DISC_MENU_FR_V1\n' + source, encoding='utf-8', newline='\n')
