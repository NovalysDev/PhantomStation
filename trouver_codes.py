#!/usr/bin/env python3
"""Recherche en ligne des codes de triche des jeux PhantomStation.

Sources :
  PS1, NES, SNES, DC : libretro-database, fichiers .cht (format RetroArch).
  PS2 : rien a faire ici. Le lanceur interroge deja deux collections pnach
        (par numero de serie + CRC de l'ELF) au moment de construire le jeu,
        dans find_ps2_codes() de phantom_ps2.py.

Sorties, selon la console :
  PS1      : phantom-codes.tsv, le format que lit le moteur PS1
             (enabled / nom / codes, un code par ligne separe par |),
             plus -liste.txt et -source.cht a cote.
  DC       : phantom-cheats.cht + phantom-cheats.txt (lus par dc_cheats.c).
  NES/SNES : <jeu>.cht + <jeu>-codes.txt. Le moteur Nintendo n'applique pas
             encore ces codes : le fichier est pret pour la suite.

Tous les codes sont ecrits desactives. Rien ne change en jeu tant qu'on ne
les active pas.

Usage :
  python trouver_codes.py --console PS1  --jeu "Dino Crisis"
  python trouver_codes.py --console SNES --jeu "Super Mario World"
  python trouver_codes.py --console NES  --jeu "Super Mario Bros"
  python trouver_codes.py --console DC   --jeu "Grandia II"
  python trouver_codes.py --console PS2  --jeu "Tony Hawk's 3"
  python trouver_codes.py --console PS1  --jeu "Dino Crisis" --dossier D:\\Essai
"""
from __future__ import annotations

import argparse
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

RACINE = "https://raw.githubusercontent.com/libretro/libretro-database/master/cht/"
ARBRE = "https://api.github.com/repos/libretro/libretro-database/git/trees/master?recursive=1"
DOSSIERS = {
    "PS1": "Sony - PlayStation",
    "NES": "Nintendo - Nintendo Entertainment System",
    "SNES": "Nintendo - Super Nintendo Entertainment System",
    "DC": "Sega - Dreamcast",
}
DOSSIER_JEUX = Path(r"D:\Jeux compilé")
DELAI = 25
# Largeur ecrite par le moteur PS1 pour chaque type de code (phantom_codes.hpp).
LARGEURS = {0x30: 1, 0x31: 1, 0x32: 1, 0xE0: 1, 0xE1: 1, 0xE2: 1, 0xE3: 1,
            0x80: 2, 0x81: 2, 0x82: 2, 0xD0: 2, 0xD1: 2, 0xD2: 2, 0xD3: 2,
            0x90: 4, 0x91: 4, 0x92: 4, 0xA0: 4, 0xA1: 4, 0xA2: 4, 0xA3: 4}
_CATALOGUE: dict[str, list[str]] = {}


def dossier_par_defaut(console: str) -> Path:
    noms = {"PS1": "PS1", "NES": "NES", "SNES": "Super Nintendo",
            "DC": "DC", "PS2": "PS2"}
    return DOSSIER_JEUX / noms.get(console, console)


def lire(url: str, taille_max: int = 8 * 1024 * 1024) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(
            url, headers={"User-Agent": "PhantomStation/1.0"}), timeout=DELAI) as reponse:
        return reponse.read(taille_max)


def catalogue(console: str) -> list[str]:
    """Noms de tous les .cht de la console dans libretro-database."""
    if console in _CATALOGUE:
        return _CATALOGUE[console]
    prefixe = f"cht/{DOSSIERS[console]}/"
    noms: list[str] = []
    try:
        arbre = json.loads(lire(ARBRE).decode("utf-8", "replace"))
        noms = [Path(e["path"]).stem for e in arbre.get("tree", [])
                if e.get("type") == "blob" and e["path"].startswith(prefixe)
                and e["path"].endswith(".cht")]
    except (OSError, ValueError, KeyError):
        noms = []
    _CATALOGUE[console] = noms
    return noms


def normalise(texte: str) -> str:
    return re.sub(r"[^a-z0-9]", "", texte.lower())


def titre_nu(titre: str) -> str:
    """Enleve les suffixes du lanceur : "Dino Crisis -France-" -> "Dino Crisis"."""
    t = re.sub(r"\.exe$", "", titre.strip(), flags=re.I)
    t = re.sub(r"\s*-\s*(France|Europe|USA|Japan|En[^-]*)\s*-\s*", " ", t, flags=re.I)
    t = re.sub(r"\s*\((?:Europe|USA|Japan|France|World)\)\s*", " ", t, flags=re.I)
    t = re.sub(r"\s+(France|Europe|USA|Japan)\s*$", "", t, flags=re.I)
    return re.sub(r"\s+", " ", t).strip()


def reste_de(nom: str, base: str) -> str:
    """Ce qui reste du nom une fois le titre, la region et le format retires."""
    reste = normalise(nom)[len(base):]
    return re.sub(r"(europe|usa|world|japan|france|gameshark|xploder|gamebuster|"
                  r"codebreaker|rev\d?|diff|preview|beta|proto|demo|sample)", "", reste)


def classement(noms: list[str], titre: str, console: str) -> list[str]:
    """Fichiers qui correspondent vraiment au titre, du meilleur au moins bon."""
    base = normalise(titre_nu(titre))
    if not base:
        return []
    # Un "Resident Evil" ne doit pas prendre les codes de "Resident Evil 2"
    # ni d'un "Director's Cut" : tout mot en plus du titre disqualifie.
    candidats = [n for n in noms if normalise(n).startswith(base) and not reste_de(n, base)]
    if not candidats:
        return []
    jeu = titre.lower()
    region = "france" if "france" in jeu else ("europe" if "europe" in jeu else None)

    def score(nom: str) -> float:
        petit = nom.lower()
        s = 0.0
        if "(diff)" in petit:
            s -= 3
        if any(x in petit for x in ("(beta", "(proto", "(demo", "(rev ", "(sample", "(preview")):
            s -= 2
        if region and f"({region})" in petit:
            s += 4
        if "(europe)" in petit:
            s += 2
        if "(world)" in petit or "(usa)" in petit:
            s += 1
        if "(japan)" in petit:
            s -= 1
        if console == "PS1":
            if "gameshark" in petit:
                s += 5          # format standard, celui que le moteur lit
            if "xploder" in petit or "game buster" in petit or "codebreaker" in petit:
                s -= 6          # format proprietaire different
        return s - len(petit) * 0.001

    return sorted(candidats, key=score, reverse=True)


def proches(noms: list[str], titre: str, limite: int = 5) -> list[str]:
    """Versions voisines, pour l'expliquer quand rien ne colle exactement."""
    base = normalise(titre_nu(titre))
    if not base:
        return []
    return sorted((n for n in noms if normalise(n).startswith(base)), key=len)[:limite]


def choisit(noms: list[str], titre: str, console: str) -> str | None:
    rang = classement(noms, titre, console)
    return rang[0] if rang else None


def variantes(titre: str) -> list[str]:
    """Repli quand la liste GitHub est injoignable."""
    base = titre_nu(titre)
    noms = [titre, base]
    for suffixe in ("", "."):
        for region in ("Europe", "USA", "Japan", "France", "World"):
            noms.append(f"{base}{suffixe} ({region})")
            noms.append(f"{base}{suffixe} ({region}) (GameShark)")
        noms.append(f"{base}{suffixe}")
    vus, sortie = set(), []
    for nom in noms:
        nom = re.sub(r"\s+", " ", nom).strip()
        if nom and nom not in vus:
            vus.add(nom)
            sortie.append(nom)
    return sortie


def telecharge(console: str, nom: str) -> str | None:
    url = RACINE + urllib.parse.quote(DOSSIERS[console]) + "/" + urllib.parse.quote(nom + ".cht")
    try:
        return lire(url, 4 * 1024 * 1024).decode("utf-8", "replace")
    except urllib.error.HTTPError as erreur:
        if erreur.code == 404:
            return None
        raise
    except urllib.error.URLError as erreur:
        raise SystemExit(f"Pas de connexion ({erreur.reason}) : rien n'a ete modifie.")


def entrees(texte: str) -> list[dict]:
    """Decoupe les cles cheatN_* du .cht en autant de codes."""
    codes: dict[int, dict] = {}
    for ligne in texte.splitlines():
        m = re.match(r"cheat(\d+)_(\w+)\s*=\s*\"(.*)\"", ligne.strip())
        if not m:
            continue
        codes.setdefault(int(m.group(1)), {})[m.group(2)] = m.group(3)
    return [codes[i] for i in sorted(codes)]


def paires_ps1(texte: str) -> list[str] | None:
    """ "800B9574+04B0+D00B1418+0005" -> paires que le moteur PS1 sait ecrire."""
    jetons = [t.strip() for t in re.split(r"[+|\n]", texte) if t.strip()]
    if not jetons or len(jetons) % 2:
        return None
    paires = []
    for i in range(0, len(jetons), 2):
        adresse, valeur = jetons[i], jetons[i + 1]
        if not re.fullmatch(r"[0-9A-Fa-f]{8}", adresse) or not re.fullmatch(r"[0-9A-Fa-f]{2,8}", valeur):
            return None                     # valeur a completer (?) ou type inconnu
        largeur = LARGEURS.get(int(adresse[:2], 16))
        if not largeur or len(valeur) != largeur * 2:
            return None
        if int(adresse[2:], 16) % largeur:
            return None                     # adresse mal alignee, le moteur refuse
        paires.append(f"{adresse.upper()} {valeur.upper()}")
    return paires or None


def convertit_ps1(texte: str) -> tuple[list[str], list[str], int]:
    """Codes du .cht -> lignes du format moteur, noms, nombre de codes ignores."""
    lignes, noms, ignores = [], [], 0
    for code in entrees(texte):
        nom = re.sub(r"\s+", " ", code.get("desc", "")).strip().replace("\t", " ") or "code"
        paires = paires_ps1(code.get("code") or code.get("codes") or "")
        if not paires:
            ignores += 1          # 8 bits, code Xploder ou valeur a completer
            continue
        lignes.append(f"0\t{nom[:70]}\t" + "|".join(paires))
        noms.append(nom)
    return lignes, noms, ignores


def ecrit_liste(chemin: Path, console: str, lignes: list[str]) -> None:
    entete = [
        f"# Codes {console} trouves en ligne (libretro-database), tous desactives.",
        "# PS1 : dans phantom-codes.tsv, remplace le 0 du debut par 1 pour activer.",
        "# Dreamcast : retire le # devant le numero dans phantom-cheats.txt.",
        "# NES/SNES : le moteur n'applique pas encore ces codes.",
        "",
    ]
    chemin.write_text("\n".join(entete + lignes) + "\n", encoding="utf-8")


def ecrit_ps1(texte: str, dossier: Path, lignes: list[str], noms: list[str],
              ignores: int, nom_sortie: str = "phantom-codes.tsv", source: str = "") -> int:
    cible = dossier / nom_sortie
    if cible.exists():
        sauvegarde = dossier / (nom_sortie + ".avant-recherche")
        if not sauvegarde.exists():
            sauvegarde.write_text(cible.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
            print(f"ancien fichier conserve : {sauvegarde.name}")
    cible.write_text("# Codes propres a ce jeu. 1 = actif, 0 = inactif.\n" + "\n".join(lignes) + "\n",
                     encoding="utf-8")
    base = nom_sortie.replace(".tsv", "")
    (dossier / f"{base}-source.cht").write_text(texte, encoding="utf-8")
    ecrit_liste(dossier / f"{base}-liste.txt", "PS1",
                [f"0 {n}" for n in noms] + ([f"# {ignores} code(s) non convertibles"] if ignores else []))
    print(f"{len(lignes)} code(s) PS1 ecrits dans {cible} (desactives)")
    if source:
        print(f"fichier d'origine : {dossier / (base + '-source.cht')} ({source})")
    if ignores:
        print(f"{ignores} code(s) ignore(s) : 8 bits non geres, format Xploder ou valeur incomplete")
    if len(list(dossier.glob("*.exe"))) > 1 and nom_sortie == "phantom-codes.tsv":
        print("attention : ce dossier contient plusieurs jeux, phantom-codes.tsv est commun")
    for nom in noms[:12]:
        print(f"    {nom}")
    if len(noms) > 12:
        print(f"    ... et {len(noms) - 12} autre(s)")
    return len(lignes)


def traite_ps1(texte: str, dossier: Path, jeu: str, nom_sortie: str = "phantom-codes.tsv") -> int:
    lignes, noms, ignores = convertit_ps1(texte)
    return ecrit_ps1(texte, dossier, lignes, noms, ignores, nom_sortie)


def traite_cht(texte: str, dossier: Path, console: str, jeu: str) -> int:
    if console == "DC":
        (dossier / "phantom-cheats.cht").write_text(texte, encoding="utf-8")
        noms = [c.get("desc", "(sans nom)") for c in entrees(texte)]
        cible = dossier / "phantom-cheats.txt"
        cible.write_text(
            "# Retire le # devant un numero pour activer le code. F11 coupe et remet.\n"
            + "".join(f"# {i} {n}\n" for i, n in enumerate(noms)), encoding="utf-8")
        print(f"{len(noms)} code(s) Dreamcast ecrits dans {dossier / 'phantom-cheats.cht'}")
        print(f"liste active/desactive : {cible}")
        return 0

    nom_fichier = re.sub(r"[^A-Za-z0-9 ._()-]", "", jeu).strip() or console
    cible = dossier / f"{nom_fichier}.cht"
    cible.write_text(texte, encoding="utf-8")
    noms = [c.get("desc", "(sans nom)") for c in entrees(texte)]
    ecrit_liste(dossier / f"{nom_fichier}-codes.txt", console, [f"{i:4d} {n}" for i, n in enumerate(noms)])
    print(f"{len(noms)} code(s) {console} ecrits dans {cible}")
    print("le moteur Nintendo n'applique pas encore ces codes : le fichier est pret pour la suite")
    for nom in noms[:10]:
        print(f"    {nom}")
    if len(noms) > 10:
        print(f"    ... et {len(noms) - 10} autre(s)")
    return len(noms)


def main() -> int:
    p = argparse.ArgumentParser(
        description="Recherche en ligne les codes de triche d'un jeu.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--console", required=True, choices=["PS1", "PS2", "NES", "SNES", "DC"])
    p.add_argument("--jeu", required=True, help="nom du jeu, par exemple \"Dino Crisis\"")
    p.add_argument("--dossier", help="ou ecrire le fichier (defaut : le dossier de la console)")
    p.add_argument("--sortie", default="phantom-codes.tsv",
                   help="nom du fichier PS1 ecrit (defaut : phantom-codes.tsv, celui que lit le jeu)")
    args = p.parse_args()

    if args.console == "PS2":
        print("Les codes PS2 sont deja recherches en ligne par le lanceur.")
        print("Au moment de construire un jeu, find_ps2_codes() (phantom_ps2.py) interroge")
        print("deux collections pnach par numero de serie et CRC de l'ELF, puis ecrit")
        print("ps2-codes-defaut.tsv dans le paquet. Il n'existe pas de dossier PS2 dans")
        print("libretro-database, donc il n'y a rien a ajouter ici.")
        print("Pour rafraichir : reconstruis le jeu PS2 depuis le lanceur.")
        return 0

    dossier = Path(args.dossier) if args.dossier else dossier_par_defaut(args.console)
    dossier.mkdir(parents=True, exist_ok=True)
    print(f"console {args.console}, dossier {dossier}")

    noms = catalogue(args.console)
    print(f"{len(noms)} fichier(s) .cht en ligne pour cette console")
    pistes = classement(noms, args.jeu, args.console)[:8] if noms else variantes(args.jeu)
    if not pistes:
        voisins = proches(noms, args.jeu) if noms else []
        if voisins:
            print("Aucune version exacte de ce jeu dans la base. Versions voisines :")
            for voisin in voisins:
                print("    " + voisin)
            print("Relance avec --jeu et le nom exact ci-dessus si c'est bien ce jeu.")
        else:
            print("Aucun fichier correspondant. Essaie le nom exact de la base,")
            print("par exemple \"Super Mario World (USA)\" ou \"Dino Crisis (Europe)\".")
        return 1

    for nom in pistes:
        texte = telecharge(args.console, nom)
        if texte is None:
            print(f"absent : {nom}")
            continue
        if args.console == "PS1":
            lignes, noms_codes, ignores = convertit_ps1(texte)
            if not lignes:
                print(f"trouve : {nom} (aucun code convertible, on essaie un autre fichier)")
                continue
            print(f"trouve : {nom}")
            return 0 if ecrit_ps1(texte, dossier, lignes, noms_codes, ignores,
                                  args.sortie, nom) >= 0 else 1
        print(f"trouve : {nom}")
        return 0 if traite_cht(texte, dossier, args.console, args.jeu) >= 0 else 1

    print("Aucun fichier exploitable pour ce jeu.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
