"""Importe les codes de triche Dreamcast des versions européennes (PAL).

Sources, toutes deux réservées aux jeux PAL / Europe :
  * le fichier « fcdcheats (OpenMenu).txt » de l'archive « CodeBreaker all
    cheats save file » (codes CodeBreaker déjà déchiffrés), --codebreaker ;
  * les pages « CDX Cheat Codes » de bucanero.com.ar (codes chiffrés),
    --bucanero : dossier des pages HTML ; --telecharger les y récupère.

Pour chaque jeu, un fichier .cht (format RetroArch, celui du moteur) est écrit
dans --sortie. Seules les écritures simples en RAM sont gardées (voir
codes_cdx.py) ; tout code qui contient autre chose est écarté en entier.

Exemple :
  python importer_codes_pal.py --codebreaker "fcdcheats (OpenMenu).txt" \\
      --bucanero pages-bucanero --telecharger --sortie codes-pal
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import codes_cdx  # noqa: E402

SITE = 'https://www.bucanero.com.ar'
LISTE = SITE + '/dreamcast/codes/'
AGENT = {'User-Agent': 'Mozilla/5.0 (compatible; PhantomStation-codes)'}
ROMAINS = {'ii': '2', 'iii': '3', 'iv': '4', 'v': '5'}


def cle_jeu(titre: str) -> str:
    """Clé de fusion : sans parenthèses, ponctuation, article initial ; II -> 2."""
    mots = re.sub(r'[^a-z0-9]+', ' ', re.sub(r'\([^)]*\)', ' ', titre.lower().replace('&', ' and '))).split()
    mots = [ROMAINS.get(m, m) for m in mots]
    if mots and mots[0] == 'the':
        mots = mots[1:]
    return ''.join(mots)


def _casse(titre: str) -> str:
    """« SHENMUE II » -> « Shenmue II », « DISNEY'S » -> « Disney's »."""
    def mot(m):
        m = m.group(0)
        return m if re.fullmatch(r'(II|III|IV|VI|F1|GT|UEFA|POD|DCA3|2K\d?)', m) else m[:1] + m[1:].lower()
    return re.sub(r"[A-Z0-9][A-Z0-9']*", mot, titre)


def nom_fichier(titre: str) -> str:
    titre = re.sub(r'\s*\((PAL|EUROPE)\)\s*$', '', titre, flags=re.I).strip()
    if titre.isupper():
        titre = _casse(titre)
    titre = codes_cdx.texte_menu(titre)
    titre = re.sub(r'[<>:"/\\|?*]+', ' ', titre)
    return ' '.join(titre.split()) + ' (Europe).cht'


# --- Fichier CodeBreaker déchiffré ---------------------------------------------

def lire_codebreaker(chemin: Path) -> list[dict]:
    lignes = chemin.read_text(encoding='utf-8', errors='replace').splitlines()
    jeux, i = [], 0
    while i < len(lignes):
        cle = lignes[i].strip()
        if not cle:
            i += 1
            continue
        i += 1
        arobases = []
        while i < len(lignes) and lignes[i].strip().startswith('@'):
            arobases.append(lignes[i].strip()[1:])
            i += 1
        titre = arobases[1] if len(arobases) > 1 else cle
        codes, courant = [], None
        while i < len(lignes) and lignes[i].strip() != '.end':
            ligne = lignes[i].strip()
            if re.fullmatch(r'[0-9A-Fa-f]{8}', ligne) and courant is not None:
                courant[1].append(int(ligne, 16))
            elif ligne:
                courant = (ligne, [])
                codes.append(courant)
            i += 1
        i += 1
        jeux.append({'titre': titre, 'codes': codes})
    return jeux


def codes_codebreaker(chemin: Path) -> dict[str, dict]:
    sortie = {}
    for jeu in lire_codebreaker(chemin):
        if not re.search(r'\(EUROPE\)\s*$', jeu['titre'], re.I):
            continue
        ecrits = [(desc, codes_cdx.ecritures(mots, chiffre=False)) for desc, mots in jeu['codes']]
        entree = sortie.setdefault(cle_jeu(jeu['titre']), {'titre': jeu['titre'], 'codes': [], 'sources': set(), 'ecartes': 0})
        entree['sources'].add('CodeBreaker all cheats 2.8')
        entree['codes'] += [(d, e) for d, e in ecrits if e]
        entree['ecartes'] += sum(1 for _, e in ecrits if not e)
    return sortie


# --- Pages de bucanero.com.ar -------------------------------------------------

def _texte(fragment: str) -> str:
    fragment = html.unescape(re.sub(r'<br\s*/?>', '\n', fragment))
    return ' '.join(re.sub(r'<[^>]+>', ' ', fragment).replace('\xa0', ' ').split())


def telecharger(dossier: Path, log=print) -> None:
    """Récupère la liste puis les pages des jeux PAL (une par seconde)."""
    dossier.mkdir(parents=True, exist_ok=True)
    def lire(url):
        with urllib.request.urlopen(urllib.request.Request(url, headers=AGENT), timeout=60) as r:
            return r.read().decode('utf-8', 'replace')
    premiere = lire(LISTE)
    pages = max([int(n) for n in re.findall(r'href="/dreamcast/codes/(\d+)"', premiere)] or [1])
    liens = []
    for n in range(1, pages + 1):
        texte = premiere if n == 1 else lire(LISTE + str(n))
        liens += re.findall(r'<a [^>]*href="(/dreamcast/codes/[^"/]+/\d+)"[^>]*>.*?</a>\s*(?:&#8211;|–|-)\s*PAL\b', texte, re.S)
        time.sleep(1)
    for lien in dict.fromkeys(liens):
        cible = dossier / (lien.replace('/dreamcast/codes/', '').replace('/', '_') + '.html')
        if cible.is_file() and cible.stat().st_size > 10000:
            continue
        cible.write_text(lire(SITE + lien), encoding='utf-8')
        time.sleep(1)
    log(f'{len(set(liens))} pages PAL dans {dossier}')


def lire_bucanero(dossier: Path) -> list[dict]:
    jeux = []
    for page in sorted(dossier.glob('*.html')):
        texte = page.read_text(encoding='utf-8', errors='replace')
        debut = texte.find('<div class="entry-content">')
        corps = texte[debut:texte.find('Back to games list', debut)]
        titre = re.search(r'<h3>(.*?)</h3>', corps, re.S)
        if debut < 0 or not titre:
            continue
        codes = []
        for bloc in re.finditer(r'<p>(.*?)</p>\s*<blockquote>(.*?)</blockquote>', corps[titre.end():], re.S):
            codes.append((_texte(bloc.group(1)), _texte(bloc.group(2))))
        jeux.append({'titre': _texte(titre.group(1)), 'codes': codes})
    return jeux


def codes_bucanero(dossier: Path) -> dict[str, dict]:
    sortie = {}
    for jeu in lire_bucanero(dossier):
        m = re.fullmatch(r'(.*?)\s*\((PAL)\)\s*', jeu['titre'])
        if not m:
            continue
        base = m.group(1)
        disque = re.search(r'\(([^)]*)\)\s*$', base)     # « Resident Evil 2 (Leon CD) »
        prefixe = disque.group(1) + ' - ' if disque else ''
        entree = sortie.setdefault(cle_jeu(base), {'titre': re.sub(r'\s*\([^)]*\)\s*$', '', base), 'codes': [],
                                                    'sources': set(), 'ecartes': 0})
        entree['sources'].add('bucanero.com.ar (CDX)')
        for desc, texte in jeu['codes']:
            if desc.upper().startswith(('(M)', '[M]')):       # code d'activation du boîtier
                continue
            mots = codes_cdx.mots_du_texte(texte)
            ecrits = codes_cdx.ecritures(mots) if mots else None
            if ecrits:
                entree['codes'].append((prefixe + desc, ecrits))
            else:
                entree['ecartes'] += 1
    return sortie


def main() -> None:
    sys.stdout.reconfigure(encoding='utf-8')
    a = argparse.ArgumentParser(description='Importer les codes Dreamcast PAL')
    a.add_argument('--codebreaker', type=Path)
    a.add_argument('--bucanero', type=Path)
    a.add_argument('--telecharger', action='store_true')
    a.add_argument('--sortie', type=Path, required=True)
    args = a.parse_args()
    if args.telecharger and args.bucanero:
        telecharger(args.bucanero)
    jeux: dict[str, dict] = {}
    for source in ([codes_codebreaker(args.codebreaker)] if args.codebreaker else []) + \
                  ([codes_bucanero(args.bucanero)] if args.bucanero else []):
        for cle, entree in source.items():
            if cle in jeux:
                jeux[cle]['codes'] += entree['codes']
                jeux[cle]['sources'] |= entree['sources']
                jeux[cle]['ecartes'] += entree['ecartes']
            else:
                jeux[cle] = entree
    args.sortie.mkdir(parents=True, exist_ok=True)
    for ancien in args.sortie.glob('*.cht'):
        ancien.unlink()
    index, total = {}, 0
    for cle, entree in sorted(jeux.items()):
        codes = codes_cdx.regroupe(entree['codes'])
        if not codes:
            continue
        nom = nom_fichier(entree['titre'])
        entete = (f"{entree['titre']} - Dreamcast, version européenne (PAL).\n"
                  f"Sources : {', '.join(sorted(entree['sources']))}.\n"
                  "Écritures simples en RAM seulement ; codes désactivés par défaut, effet à vérifier en jeu.")
        (args.sortie / nom).write_text(codes_cdx.cht(codes, entete), encoding='utf-8')
        index[nom] = {'titre': entree['titre'], 'codes': len(codes), 'ecartes': entree['ecartes'],
                      'sources': sorted(entree['sources'])}
        total += len(codes)
    (args.sortie / 'index.json').write_text(json.dumps(index, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    print(f'{len(index)} jeux PAL, {total} codes, dans {args.sortie}')


if __name__ == '__main__':
    main()
