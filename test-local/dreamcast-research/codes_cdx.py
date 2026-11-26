"""Codes Dreamcast au format CDX (CodeBreaker / Xploder DC), chiffrés.

Ces boîtiers chiffrent le premier mot d'un code (type et adresse). Le second
mot, la valeur, reste en clair. Le déchiffrement ci-dessous suit la
description publiée par RetroSven (dépôt dreamcast-codebreaker-decrypt,
reconstituée à partir de l'outil DCcrypt de Parasyte) : seize graines choisies
par le premier chiffre, deux rotations sur 28 bits selon un état qui vaut 6 au
début de chaque code, puis un ou exclusif.

Une fois déchiffré, le premier mot vaut 0Taaaaaa : T est le type, aaaaaa
l'adresse dans la RAM principale (0x8C000000 + aaaaaa). Seules les écritures
simples sont gardées, parce que le moteur ne sait faire qu'elles :
T = 0 (8 bits), 1 (16 bits) ou 2 (32 bits). Les autres types (conditions,
copies, codes « à activer » du boîtier) sont écartés, comme le code entier
qui les contient.
"""
from __future__ import annotations

import re

GRAINES = (
    0xA53A8888, 0xA1427921, 0xAC9528B1, 0xC5892354, 0x49671B12, 0xACC56121,
    0xACB5381E, 0x765436E1, 0x9F2C3E54, 0x1133E312, 0xAC5E7894, 0xE9F208B1,
    0x4E87DCFE, 0x43174312, 0x1D7A6C99, 0x874224A2,
)
ETAT_INITIAL = 6
TAILLES = {0: 1, 1: 2, 2: 4}          # type de code -> octets écrits
TAILLE_RETROARCH = {1: 3, 2: 4, 4: 5}  # octets -> memory_search_size de RetroArch
RAM = 16 << 20


def _rotation_28(mot: int, decalage: int) -> int:
    mot &= 0x0FFFFFFF
    return ((mot << decalage) | (mot >> (28 - decalage))) & 0x0FFFFFFF


def dechiffre_mot(mot: int, etat: int = ETAT_INITIAL) -> tuple[int, int]:
    """Déchiffre le premier mot d'un code. Renvoie (mot clair, état suivant)."""
    graine = GRAINES[(mot >> 28) & 0xF]
    clair = mot & 0x0FFFFFFF
    if etat & 4:
        clair = _rotation_28(clair, 1)
    if etat & 2:
        clair = _rotation_28(_rotation_28(clair, 1), 8)
    if etat & 1:
        graine >>= 4
    clair ^= graine & 0x0FFFFFFF
    if (clair & 0x0FF00000) != 0x07100000:
        etat = (clair & 0xF) + 6
    return clair, etat


def valeur_chiffree(valeur: int) -> bool:
    """Les codes GameShark dont la valeur est chiffrée aussi donnent des valeurs
    autour de 0x1456xxxx (0x1456E7A5 est le chiffrement de 0). Leur adresse se
    déchiffre, mais pas leur valeur : on les écarte plutôt que d'écrire faux."""
    return valeur >> 16 == 0x1456


def ecritures(mots: list[int], chiffre: bool = True) -> list[tuple[int, int, int]] | None:
    """Liste (décalage en RAM, valeur, octets) d'un code, ou None s'il contient
    autre chose que des écritures simples ou une valeur impossible.

    Chaque ligne repart de l'état initial : vérifié sur Grandia II PAL, où les
    deux lignes d'un code de vie tombent alors sur les deux moitiés de la vie
    de Ryudo, comme dans le fichier européen fait à la main. Avec chiffre=False,
    les mots sont déjà en clair (fichiers CodeBreaker déchiffrés)."""
    if not mots or len(mots) % 2:
        return None
    sortie = []
    for i in range(0, len(mots), 2):
        clair = dechiffre_mot(mots[i])[0] if chiffre else mots[i]
        type_code, decalage, valeur = clair >> 24, clair & 0xFFFFFF, mots[i + 1]
        octets = TAILLES.get(type_code)
        if octets is None or decalage + octets > RAM or decalage % octets:
            return None
        if (octets < 4 and valeur >= 1 << (8 * octets)) or (chiffre and valeur_chiffree(valeur)):
            return None
        sortie.append((decalage, valeur, octets))
    return sortie


_MOT = re.compile(r'^[0-9A-Fa-f]{8}$')


def mots_du_texte(texte: str) -> list[int] | None:
    """« AC035027 0000173E » -> [0xAC035027, 0x173E] ; None si mal formé.
    Certains sites collent les mots (« AC0350270000173E ») : chaque groupe
    est alors découpé par huit chiffres, s'il en a un multiple exact."""
    morceaux = []
    for groupe in texte.split():
        if not re.fullmatch(r'[0-9A-Fa-f]+', groupe) or len(groupe) % 8:
            return None
        morceaux += [groupe[i:i + 8] for i in range(0, len(groupe), 8)]
    if not morceaux or not all(_MOT.match(m) for m in morceaux):
        return None
    return [int(m, 16) for m in morceaux]


_REMPLACE = {'–': '-', '—': '-', '‘': "'", '’': "'", '“': "'", '”': "'",
             '×': 'x', '"': "'", chr(92): ' '}


def texte_menu(texte: str) -> str:
    """Nom lisible par la petite police du menu F11 (ASCII, sans guillemets)."""
    import unicodedata
    texte = ''.join(_REMPLACE.get(c, c) for c in texte)
    texte = unicodedata.normalize('NFKD', texte).encode('ascii', 'ignore').decode('ascii')
    return ' '.join(texte.split())


def regroupe(codes: list[tuple[str, list[tuple[int, int, int]]]]) -> list[tuple[str, list[tuple[int, int, int]]]]:
    """Retire les doublons exacts et numérote les codes différents de même nom :
    le moteur réunit en un seul code les lignes consécutives de même nom."""
    vus, noms, sortie = set(), {}, []
    for description, ecrits in codes:
        cle = tuple(ecrits)
        if not ecrits or cle in vus:
            continue
        vus.add(cle)
        nom = texte_menu(description) or 'Code'
        noms[nom] = noms.get(nom, 0) + 1
        sortie.append((nom if noms[nom] == 1 else f'{nom} {noms[nom]}', ecrits))
    return sortie


def cht(codes: list[tuple[str, list[tuple[int, int, int]]]], entete: str = '') -> str:
    """Fichier .cht de RetroArch (celui que lit le moteur). Un code à plusieurs
    écritures donne des entrées consécutives de même nom, activées ensemble."""
    lignes, n = [], 0
    for description, ecrits in regroupe(codes):
        for decalage, valeur, octets in ecrits:
            lignes += [f'cheat{n}_desc = "{description}"',
                       f'cheat{n}_address = "{decalage}"',
                       f'cheat{n}_value = "{valeur}"',
                       f'cheat{n}_memory_search_size = "{TAILLE_RETROARCH[octets]}"',
                       f'cheat{n}_cheat_type = "1"',
                       f'cheat{n}_handler = "1"',
                       f'cheat{n}_enable = "false"']
            n += 1
    tete = ''.join('# ' + l + '\n' for l in entete.splitlines())
    return tete + f'cheats = {n}\n\n' + '\n'.join(lignes) + ('\n' if lignes else '')
