"""Passer les films Sofdec avec Start, pour tout jeu Dreamcast qui utilise Sofdec.

Sofdec (bibliothèque vidéo de CRI) donne l'état du lecteur par mwSfdGetStat :
2 = film en cours, 3 = film terminé. Grandia II avait un crochet écrit pour son
seul disque (grandia_cinema.py). Ici, la fonction est retrouvée dans n'importe
quel jeu par son propre message d'erreur (« mwSfdGetStat: handle is NULL »),
le même dans les versions vues (1.21 de V-Rally 2, 1.31 de Grandia II).

Sa traduction est enveloppée : quand elle répond « en cours » pendant un film
et que le joueur appuie sur Start, elle répond « terminé », la fin normale du
film pour le jeu. Tous les appels du jeu passent par elle.
"""
from __future__ import annotations

from pathlib import Path
import re

BASE = 0x8C010000
MESSAGE = b'mwSfdGetStat: handle is NULL'
ENTETE = '#include "hal/dc_cinematic.h"\n'


def trouver(boot: bytes) -> int | None:
    """Adresse de mwSfdGetStat dans le programme de démarrage, ou None."""
    i = boot.find(MESSAGE)
    if i < 0:
        return None
    while i > 0 and 0x20 <= boot[i - 1] < 0x7F:
        i -= 1
    cible = BASE + i

    def h(a):
        o = a - BASE
        return int.from_bytes(boot[o:o + 2], 'little') if 0 <= o <= len(boot) - 2 else None

    for p in range(0, len(boot) - 3, 4):
        if int.from_bytes(boot[p:p + 4], 'little') != cible:
            continue
        pool = BASE + p
        for a in range(max(BASE, pool - 1020), pool, 2):
            op = h(a)
            # mov.l @(disp,PC),Rn qui lit le message dans le réservoir.
            if op is None or (op & 0xF000) != 0xD000 or ((a + 4) & ~3) + (op & 0xFF) * 4 != pool:
                continue
            debut = a
            while debut > BASE + 4 and h(debut - 4) != 0x000B and a - debut < 0x200:
                debut -= 2
            if h(debut - 4) != 0x000B:
                continue
            while h(debut) == 0x0009:
                debut += 2
            # Elle doit commencer comme une fonction : registre ou PR sauvegardé.
            if any(o is not None and ((o & 0xFF0F) == 0x2F06 or o == 0x4F22)
                   for o in (h(debut + 2 * k) for k in range(4))):
                return debut
    return None


def entrees(boot: bytes) -> set[int]:
    """mwSfdGetStat devient un point d'entrée de la traduction."""
    adresse = trouver(boot)
    return {adresse} if adresse else set()


def apply(stage: Path, boot: bytes, log=print) -> int:
    """Enveloppe la traduction de mwSfdGetStat ; renvoie 1 si le jeu l'utilise."""
    adresse = trouver(boot)
    if not adresse:
        return 0
    nom = f'func_{adresse:08X}'
    definition = re.compile(r'^void ' + nom + r'\(SH4CPU \*cpu\) \{', re.M)
    for source in sorted((stage / 'src' / 'game').glob('game_code_*.c')):
        if source.name.startswith('game_code_L'):
            continue
        texte = source.read_text(encoding='utf-8')
        if len(definition.findall(texte)) != 1:
            continue
        if f'sofdec_etat_{adresse:08X}' in texte:
            return 1
        texte = definition.sub(f'void sofdec_etat_{adresse:08X}(SH4CPU *cpu) {{', texte, count=1)
        if ENTETE not in texte:
            texte = ENTETE + texte
        texte += (f'\n/* Sofdec : Start pendant un film = fin normale du film (sofdec_cinema.py). */\n'
                  f'void {nom}(SH4CPU *cpu) {{\n'
                  f'    sofdec_etat_{adresse:08X}(cpu);\n'
                  f'    if (cpu->r[0] == 2u) dc_cinematic_offer_skip();\n'
                  f'    if (cpu->r[0] == 2u && dc_cinematic_buttons()) cpu->r[0] = 3u;\n'
                  f'}}\n')
        source.write_text(texte, encoding='utf-8', newline='\n')
        log(f'Films Sofdec : Start permet de passer les films (mwSfdGetStat en 0x{adresse:08X}).')
        return 1
    log(f'Films Sofdec : mwSfdGetStat (0x{adresse:08X}) absente de la traduction ; films non passables.')
    return 0
