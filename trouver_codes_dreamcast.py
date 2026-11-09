#!/usr/bin/env python3
"""Recupere une liste de codes Dreamcast depuis libretro-database.

La base utilise le meme format .cht que RetroArch et Flycast ; le moteur
(phantom-cheats.cht) lit exactement ce format. Le fichier est ecrit a cote de
l'exe du jeu, avec une liste commentee des codes dans phantom-cheats.txt.

Usage :
    python trouver_codes_dreamcast.py "Grandia II (Europe)"
    python trouver_codes_dreamcast.py --jeu "Grandia II" --dossier "D:\\Jeux compile\\DC"
    python trouver_codes_dreamcast.py --liste phantom-cheats.cht

Sans internet, le script le dit et ne touche a rien.
"""
from __future__ import annotations

import argparse
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "https://raw.githubusercontent.com/libretro/libretro-database/master/cht/Sega%20-%20Dreamcast/"
DELAI = 20


def candidats(titre: str) -> list[str]:
    """Noms de fichiers a essayer, du plus precis au plus general."""
    titre = titre.strip()
    noms = [titre]
    sans_region = re.sub(r"\s*\((?:Europe|USA|Japan|En,[^)]*)\)\s*", " ", titre).strip()
    for region in ("USA", "Japan", "Europe"):
        noms.append(f"{sans_region} ({region})")
    noms.append(sans_region)
    # Variantes avec suffixe de langues, telles qu'on les trouve dans la base.
    for region in ("Europe", "USA"):
        noms.append(f"{sans_region} ({region}) (En,Fr,De,Es,It)")
        noms.append(f"{sans_region} ({region}) (En,Ja,Fr,De,Es)")
    vus, sortie = set(), []
    for n in noms:
        n = re.sub(r"\s+", " ", n).strip()
        if n and n not in vus:
            vus.add(n)
            sortie.append(n)
    return sortie


def telecharge(nom: str) -> str | None:
    url = BASE + urllib.parse.quote(nom + ".cht")
    try:
        with urllib.request.urlopen(url, timeout=DELAI) as reponse:
            return reponse.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as erreur:
        if erreur.code == 404:
            return None
        raise
    except urllib.error.URLError as erreur:
        raise SystemExit(f"Pas de connexion ({erreur.reason}) : rien n'a ete modifie.")


def liste_codes(texte: str) -> list[tuple[int, str]]:
    codes: dict[int, str] = {}
    for ligne in texte.splitlines():
        m = re.match(r"cheat(\d+)_desc\s*=\s*\"(.*)\"", ligne.strip())
        if m:
            codes[int(m.group(1))] = m.group(2)
    return sorted(codes.items())


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("titre", nargs="?", help="nom du jeu, par exemple \"Grandia II (Europe)\"")
    p.add_argument("--jeu", dest="jeu", help="nom du jeu si tu preferes une option nommee")
    p.add_argument("--dossier", default=".", help="dossier du jeu (defaut : dossier courant)")
    p.add_argument("--liste", help="affiche seulement les codes d'un .cht deja telecharge")
    args = p.parse_args()

    dossier = Path(args.dossier)

    if args.liste:
        texte = Path(args.liste).read_text(encoding="utf-8", errors="replace")
        codes = liste_codes(texte)
        print(f"{len(codes)} code(s) dans {args.liste}")
        for i, desc in codes:
            print(f"{i:4d}  {desc}")
        return 0

    titre = args.jeu or args.titre
    if not titre:
        p.error("donne un nom de jeu (ou --liste fichier.cht)")

    for nom in candidats(titre):
        texte = telecharge(nom)
        if texte is None:
            print(f"absent : {nom}")
            continue
        cible = dossier / "phantom-cheats.cht"
        cible.write_text(texte, encoding="utf-8")
        codes = liste_codes(texte)
        print(f"trouve : {nom}  ->  {cible}")
        print(f"{len(codes)} code(s) disponible(s) :")
        for i, desc in codes:
            print(f"{i:4d}  {desc}")
        notes = dossier / "phantom-cheats.txt"
        notes.write_text(
            "# Codes de triche : retire le # devant un numero pour l'activer.\n"
            "# Format : un numero par ligne. F11 coupe et remet les codes en jeu.\n"
            + "".join(f"# {i} {desc}\n" for i, desc in codes),
            encoding="utf-8",
        )
        print(f"liste ecrite dans {notes} (tous desactives au depart)")
        return 0

    print("Aucun fichier correspondant dans libretro-database pour ce titre.")
    print("Essaie le nom exact de la base, par exemple \"Grandia II (USA)\".")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
