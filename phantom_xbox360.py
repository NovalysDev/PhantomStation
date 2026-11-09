#!/usr/bin/env python3
"""Lance un jeu Xbox 360 avec Xenia Canary, depuis PhantomStation.

L'Xbox 360 n'a pas d'equivalent des moteurs natifs PS1/PS2/Nintendo/Dreamcast
du projet : ce module ne recompile rien, il trouve Xenia, liste les jeux d'un
dossier et lance celui qu'on choisit.

Usage :
    python phantom_xbox360.py --liste
    python phantom_xbox360.py --dossier "D:\\Jeux compile\\Xbox 360" --liste
    python phantom_xbox360.py --jeu "D:\\Jeux\\mon-jeu.iso"
    python phantom_xbox360.py --xenia "C:\\Xenia\\xenia_canary.exe" --jeu ...

Xenia ne demande ni BIOS ni fichiers systeme. Formats acceptes : .iso, .xex,
dossiers GOD (contenant default.xex). La manette passe par XInput, une manette
Xbox 360 est reconnue sans configuration.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
EXTENSIONS = {".iso", ".xex", ".xbox360"}
DOSSIER_DEFAUT = Path(r"D:\Jeux compilé\Xbox 360")


def candidats_xenia() -> list[Path]:
    """Emplacements essayes, du plus probable au plus general."""
    pistes: list[Path] = []
    env = os.environ.get("PHANTOM_XENIA")
    if env:
        pistes.append(Path(env))
    pistes += [
        BASE / "xenia_canary.exe",
        BASE / "xenia.exe",
        BASE / "assets" / "xbox360" / "xenia_canary.exe",
    ]
    local = os.environ.get("LOCALAPPDATA")
    if local:
        racine = Path(local) / "PhantomStation"
        for motif in ("Lanceurs-compatibles", "Lanceurs-volumineux", "Anciens-lanceurs"):
            dossier = racine / motif
            if dossier.is_dir():
                pistes += sorted(dossier.glob("**/xenia*canary*.exe"))
                pistes += sorted(dossier.glob("**/xenia*.exe"))
    pistes += [Path(r"C:\Xenia\xenia_canary.exe"), Path(r"C:\Xenia\xenia.exe")]
    trouve = shutil.which("xenia_canary.exe") or shutil.which("xenia.exe")
    if trouve:
        pistes.append(Path(trouve))
    return pistes


def trouve_xenia(force: str | None) -> Path | None:
    if force:
        chemin = Path(force)
        return chemin if chemin.is_file() else None
    for piste in candidats_xenia():
        if piste.is_file():
            return piste
    return None


def jeux(dossier: Path) -> list[Path]:
    """Images de disque, executables, et dossiers GOD."""
    if not dossier.is_dir():
        return []
    trouves: list[Path] = []
    for entree in sorted(dossier.iterdir(), key=lambda p: p.name.lower()):
        if entree.is_file() and entree.suffix.lower() in EXTENSIONS:
            trouves.append(entree)
        elif entree.is_dir() and (entree / "default.xex").is_file():
            trouves.append(entree)
    return trouves


def affiche(chemin: Path, index: int | None = None) -> None:
    try:
        if chemin.is_file():
            taille = chemin.stat().st_size / (1024 ** 3)
            poids = f"{taille:6.2f} Go"
        else:
            poids = "  dossier"
    except OSError:
        poids = "        ?"
    prefixe = f"{index:3d}  " if index is not None else ""
    print(f"{prefixe}{poids}  {chemin.name}")


def lance(xenia: Path, jeu: Path) -> int:
    if not jeu.exists():
        print(f"Jeu introuvable : {jeu}", file=sys.stderr)
        return 2
    commande = [str(xenia), str(jeu)]
    print("lancement :", " ".join(f'"{c}"' if " " in c else c for c in commande))
    try:
        subprocess.Popen(commande, cwd=str(xenia.parent))
    except OSError as erreur:
        print(f"Impossible de lancer Xenia : {erreur}", file=sys.stderr)
        return 3
    return 0


def main() -> int:
    p = argparse.ArgumentParser(
        description="Lance un jeu Xbox 360 avec Xenia Canary.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--xenia", help="chemin de xenia_canary.exe")
    p.add_argument("--dossier", help="dossier des jeux Xbox 360")
    p.add_argument("--jeu", help="jeu a lancer (.iso, .xex ou dossier GOD)")
    p.add_argument("--liste", action="store_true", help="liste les jeux du dossier")
    args = p.parse_args()

    dossier = Path(args.dossier) if args.dossier else DOSSIER_DEFAUT
    if not dossier.is_dir():
        dossier = BASE

    if args.jeu:
        xenia = trouve_xenia(args.xenia)
        if not xenia:
            print("Xenia introuvable. Telecharge Xenia Canary puis indique-le avec "
                  "--xenia \"C:\\chemin\\xenia_canary.exe\" ou PHANTOM_XENIA.", file=sys.stderr)
            return 1
        print("xenia :", xenia)
        return lance(xenia, Path(args.jeu))

    xenia = trouve_xenia(args.xenia)
    print("xenia :", xenia if xenia else "introuvable (voir --xenia)")
    liste = jeux(dossier)
    print(f"jeux dans {dossier} : {len(liste)}")
    if not liste:
        print("Aucun jeu trouve. Place des .iso ou .xex dans ce dossier, "
              "ou choisis-en un avec --dossier.")
        return 0
    for i, chemin in enumerate(liste):
        affiche(chemin, i)
    if args.liste:
        return 0
    print("\npour lancer : python phantom_xbox360.py --jeu \"<chemin du jeu>\"")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
