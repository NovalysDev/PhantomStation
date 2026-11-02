"""Ajoute un raccourci explicite vers le disque suivant en cours de jeu."""
from pathlib import Path


def install_disc_hotkey(project):
    main = Path(project) / 'psxrecomp/runtime/src/main.cpp'
    source = main.read_text(encoding='utf-8')
    if 'PHANTOM_NEXT_DISC_F4_V1' in source:
        return
    anchor = '            else if (key == SDLK_c && (mod & KMOD_CTRL)) {'
    if source.count(anchor) != 1:
        raise ValueError('Gestion des touches du lecteur introuvable.')
    insert = '''            // PHANTOM_NEXT_DISC_F4_V1: explicit in-game multi-disc change.
            else if (!key_repeat && key == SDLK_F4 &&
                     !(mod & (KMOD_CTRL | KMOD_ALT | KMOD_GUI)) &&
                     cdrom_disc_roster_count() > 1) {
                const int count = cdrom_disc_roster_count();
                const int current = cdrom_disc_selected();
                const int next = current > 0 ? (current % count) + 1 : 1;
                const int rc = psx_disc_tray_cycle(next, PSX_DISC_TRAY_ORIGIN_MENU);
                char message[80];
                if (rc == PSX_DISC_TRAY_OK)
                    std::snprintf(message, sizeof message, "Disque %d insere", next);
                else
                    std::snprintf(message, sizeof message, "Changement de disque impossible");
                host_osd_push(message, 2500);
            }
'''
    source = source.replace(anchor, insert + anchor, 1)
    main.write_text(source, encoding='utf-8', newline='\n')
