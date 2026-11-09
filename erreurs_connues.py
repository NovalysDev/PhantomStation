#!/usr/bin/env python3
"""Memoire des erreurs connues du compilateur PhantomStation.

Chaque erreur deja rencontree, sa cause et son correctif sont ecrits une fois,
puis rejoues automatiquement a chaque compilation : la base est lue avant le
build, et le journal affiche la ligne [ERREUR-CONNUE] correspondante. Un echec
nouveau est enregistre pour devenir une entree exploitable.

Fichiers :
  erreurs-connues.json            base livree avec le projet (versionnee)
  %LOCALAPPDATA%/PhantomStation/apprentissage.json
                                  entrees apprises + incidents observes, propres a la machine

Usage :
  python erreurs_connues.py --liste
  python erreurs_connues.py --console DC --jeu "Grandia II"
  python erreurs_connues.py --incidents
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
from pathlib import Path

BASE = Path(__file__).resolve().parent
LIVRE = BASE / "erreurs-connues.json"
PROFIL = (Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
          / "PhantomStation" / "apprentissage.json")


def titre_nu(nom: str) -> str:
    """ "Grandia II -Europe-" -> "Grandia II"."""
    t = re.sub(r"\.(exe|chd|iso|cue|bin|nes|sfc|smc|z64|n64|v64)$", "", (nom or "").strip(), flags=re.I)
    t = re.sub(r"\s*-\s*(France|Europe|USA|Japan|En[^-]*)\s*-\s*", " ", t, flags=re.I)
    t = re.sub(r"\s*\((?:Europe|USA|Japan|France|World)[^)]*\)\s*", " ", t, flags=re.I)
    t = re.sub(r"\s+(France|Europe|USA|Japan)\s*$", "", t, flags=re.I)
    return re.sub(r"\s+", " ", t).strip()


def normalise(texte: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (texte or "").lower())


def empreinte_legere(image) -> str:
    """Empreinte stable et rapide : taille + debut et fin du fichier."""
    chemin = Path(image)
    taille = chemin.stat().st_size
    h = hashlib.sha256(str(taille).encode("ascii"))
    with chemin.open("rb") as flux:
        h.update(flux.read(512 * 1024))
        if taille > 1024 * 1024:
            flux.seek(-512 * 1024, os.SEEK_END)
            h.update(flux.read(512 * 1024))
    return h.hexdigest()[:32]


def signature(message: str) -> str:
    """Forme stable d'un message d'erreur, pour reconnaitre la meme panne."""
    texte = re.sub(r"[A-Za-z]:\\\\?[^\s:]+", "<chemin>", str(message))
    texte = re.sub(r"0x[0-9a-fA-F]+", "<hex>", texte)
    texte = re.sub(r"\d+", "<n>", texte)
    texte = re.sub(r"\s+", " ", texte).strip().lower()
    return texte[:120]


def charge() -> dict:
    """Base livree + base personnelle de la machine."""
    donnees = {"entrees": [], "incidents": {}, "perso": {"entrees": [], "incidents": {}}}
    try:
        livre = json.loads(LIVRE.read_text(encoding="utf-8"))
        donnees["entrees"] = list(livre.get("entrees") or [])
    except (OSError, ValueError):
        pass
    perso = {"entrees": [], "incidents": {}}
    try:
        contenu = json.loads(PROFIL.read_text(encoding="utf-8"))
        if isinstance(contenu, dict):
            perso["entrees"] = list(contenu.get("entrees") or [])
            perso["incidents"] = dict(contenu.get("incidents") or {})
    except (OSError, ValueError):
        pass
    vus = {e.get("id") for e in donnees["entrees"]}
    donnees["entrees"] += [e for e in perso["entrees"] if e.get("id") not in vus]
    donnees["incidents"] = perso["incidents"]
    donnees["perso"] = perso
    return donnees


def sauve(donnees: dict) -> None:
    perso = donnees["perso"]
    PROFIL.parent.mkdir(parents=True, exist_ok=True)
    PROFIL.write_text(json.dumps({"version": 1, "entrees": perso["entrees"],
                                  "incidents": perso["incidents"]},
                                 ensure_ascii=False, indent=2), encoding="utf-8")


def correspond(entree: dict, console: str, image, nom: str) -> bool:
    if entree.get("console") not in (None, "TOUTES", console):
        return False
    cible = normalise(titre_nu(nom))
    for motif in entree.get("jeux") or []:
        if motif == "*" or (cible and cible.startswith(normalise(motif))):
            return True
    if image and entree.get("empreintes"):
        try:
            return empreinte_legere(image) in entree["empreintes"]
        except OSError:
            return False
    return False


def trouve_fichier(nom: str) -> Path | None:
    """Cherche un fichier du moteur sans parcourir tout le disque."""
    candidats = [BASE / nom, BASE / "dcrecomp" / "src" / "hal" / nom,
                 BASE / "dcrecomp" / "src" / nom]
    for racine in ("dcrecomp", "test-local", "psxrecomp", "nintendo-recomp"):
        candidats += list(BASE.glob(f"{racine}/**/{nom}"))
    for chemin in candidats:
        if chemin.is_file():
            return chemin
    return None


def verifie(entree: dict):
    """(True, chemin) correctif present, (False, chemin) absent, None non verifiable."""
    controle = entree.get("controle") or {}
    fichier, motifs = controle.get("fichier"), controle.get("motifs") or []
    if not fichier or not motifs:
        return None
    chemin = trouve_fichier(fichier)
    if chemin is None:
        return None
    try:
        contenu = chemin.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    try:
        relatif = chemin.relative_to(BASE).as_posix()
    except ValueError:
        relatif = chemin.name
    return any(motif in contenu for motif in motifs), relatif


def preparer(console: str, image, nom: str) -> list[str]:
    """Lignes a afficher dans le journal avant de compiler ce jeu."""
    lignes: list[str] = []
    donnees = charge()
    for entree in donnees["entrees"]:
        if not correspond(entree, console, image, nom):
            continue
        lignes.append(f"[ERREUR-CONNUE] {entree.get('titre')} — {entree.get('symptome')}")
        lignes.append(f"                 cause : {entree.get('cause')}")
        lignes.append(f"                 correctif : {entree.get('correctif')}")
        etat = verifie(entree)
        if etat is None:
            lignes.append("                 controle : correctif non verifiable automatiquement")
        elif etat[0]:
            lignes.append(f"                 controle : correctif present ({etat[1]})")
        else:
            lignes.append(f"                 ATTENTION : correctif absent de {etat[1]} — a reappliquer")
    if lignes:
        lignes.insert(0, f"[ERREUR-CONNUE] {len(lignes) // 4} erreur(s) connue(s) pour {titre_nu(nom) or console} :")
    return lignes


def noter_echec(console: str, image, nom: str, message: str) -> str:
    """Enregistre un echec : la meme panne sera reconnue au prochain build."""
    sig = signature(message)
    if not sig:
        return ""
    donnees = charge()
    perso = donnees["perso"]
    cle = f"{console}/{sig}"
    incident = perso["incidents"].setdefault(cle, {
        "console": console, "jeu": titre_nu(nom), "signature": sig,
        "nombre": 0, "premiere": "", "derniere": ""})
    incident["nombre"] = int(incident.get("nombre", 0)) + 1
    maintenant = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    incident["premiere"] = incident.get("premiere") or maintenant
    incident["derniere"] = maintenant
    incident["jeu"] = titre_nu(nom) or incident.get("jeu", "")
    sauve(donnees)
    return (f"[ERREUR-CONNUE] echec enregistre ({incident['nombre']}e fois, {console}) : "
            f"{sig[:80]}")


def main() -> int:
    p = argparse.ArgumentParser(description="Memoire des erreurs connues du compilateur.")
    p.add_argument("--liste", action="store_true", help="affiche toute la base")
    p.add_argument("--console")
    p.add_argument("--jeu")
    p.add_argument("--incidents", action="store_true", help="echecs observes sur cette machine")
    args = p.parse_args()

    donnees = charge()
    if args.incidents:
        incidents = donnees["incidents"]
        if not incidents:
            print("Aucun echec enregistre pour l'instant.")
            return 0
        for cle, incident in sorted(incidents.items(), key=lambda kv: kv[1].get("derniere", ""), reverse=True):
            print(f"{incident.get('nombre')}x  {incident.get('console')}  {incident.get('jeu') or '?'}")
            print(f"     {incident.get('signature')}")
            print(f"     vu : {incident.get('premiere')} -> {incident.get('derniere')}")
        return 0
    if args.liste or not (args.console or args.jeu):
        for entree in donnees["entrees"]:
            etat = verifie(entree)
            marque = "OK " if (etat and etat[0]) else ("!! " if etat else "?  ")
            print(f"{marque}{entree.get('console'):4s} {entree.get('id')}")
            print(f"     {entree.get('titre')}")
            print(f"     jeux : {', '.join(entree.get('jeux') or [])}")
            if etat:
                print(f"     correctif {'present' if etat[0] else 'ABSENT'} dans {etat[1]}")
        return 0
    lignes = preparer(args.console or "", None, args.jeu or "")
    print("\n".join(lignes) if lignes else "Aucune erreur connue pour ce jeu.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
