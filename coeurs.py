"""Nombre de cœurs du processeur utilisés pour compiler, pour toutes les consoles.

Choisi sur l'écran de création (clé « coeurs » de compilateur.json : « auto »
ou un nombre), ou automatique. Jamais tous les cœurs : un cinquième des threads
(au moins 2) reste toujours à Windows, PhantomStation et un jeu lancé pendant
la compilation.

  maximum     = threads moins la réserve de Windows, et pas plus de 1
                compilation C par 2 Go de mémoire ;
  automatique = les trois quarts des threads, dans la limite du maximum.

PHANTOM_JOBS (ou PHANTOM_DC_JOBS) le remplace pour un essai.
"""
from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path

REGLAGES = Path(os.environ.get('LOCALAPPDATA') or (Path.home() / 'AppData' / 'Local')) / 'PhantomStation' / 'compilateur.json'
GO_PAR_COMPILATION = 2


def processeur() -> int:
    """Cœurs logiques (threads) du processeur."""
    return max(1, os.cpu_count() or 1)


def nom_processeur() -> str:
    """« Intel Core i9-10900K · 3,70 GHz » (registre Windows), sinon ''."""
    if os.name != 'nt':
        return ''
    try:
        import re
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'HARDWARE\DESCRIPTION\System\CentralProcessor\0') as cle:
            nom = str(winreg.QueryValueEx(cle, 'ProcessorNameString')[0])
    except OSError:
        return ''
    nom = re.sub(r'\((R|TM|tm|r)\)', '', nom)
    frequence = re.search(r'@\s*([\d.]+)\s*GHz', nom)
    nom = re.sub(r'\s*(CPU|Processor)?\s*@.*$', '', nom)
    nom = re.sub(r'\s+\d+-Core Processor$', '', nom)
    nom = ' '.join(nom.split())
    if frequence:
        nom += ' · ' + frequence.group(1).replace('.', ',') + ' GHz'
    return nom


def coeurs_physiques() -> int | None:
    """Cœurs physiques (sans l'hyper-threading), None si inconnu."""
    if os.name != 'nt':
        return None
    taille = ctypes.c_ulong(0)
    k = ctypes.windll.kernel32
    k.GetLogicalProcessorInformationEx(0, None, ctypes.byref(taille))   # RelationProcessorCore
    if not taille.value:
        return None
    tampon = ctypes.create_string_buffer(taille.value)
    if not k.GetLogicalProcessorInformationEx(0, tampon, ctypes.byref(taille)):
        return None
    nombre, position = 0, 0
    while position < taille.value:
        nombre += 1
        position += int.from_bytes(tampon.raw[position + 4:position + 8], 'little')   # champ Size
    return nombre or None


def description() -> str:
    """Ligne affichée : nom, cœurs, threads, mémoire."""
    physiques = coeurs_physiques()
    memoire = memoire_go()
    morceaux = [nom_processeur() or 'Processeur']
    if physiques and physiques != processeur():
        morceaux.append(f'{physiques} cœurs, {processeur()} threads')
    else:
        morceaux.append(f'{processeur()} cœurs')
    if memoire:
        morceaux.append(f'{round(memoire)} Go de mémoire')
    return ' — '.join(morceaux)


def memoire_go() -> float | None:
    """Mémoire vive totale, en Go (Windows) ; None si inconnue."""
    if os.name != 'nt':
        return None
    class Etat(ctypes.Structure):
        _fields_ = [('taille', ctypes.c_ulong), ('charge', ctypes.c_ulong), ('total', ctypes.c_ulonglong),
                    ('libre', ctypes.c_ulonglong)] + [(f'r{i}', ctypes.c_ulonglong) for i in range(5)]
    etat = Etat(); etat.taille = ctypes.sizeof(Etat)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(etat)):
        return None
    return etat.total / 2**30


def reserve_windows() -> int:
    """Threads toujours laissés à Windows : un cinquième, au moins 2 (1 sur
    un processeur à 2 threads, aucun sur un seul)."""
    n = processeur()
    if n <= 2:
        return n - 1
    return max(2, n // 5)


def maximum() -> int:
    """Le plus grand choix permis : sans la réserve de Windows, et pas plus
    de compilations que la mémoire n'en porte."""
    valeur = processeur() - reserve_windows()
    memoire = memoire_go()
    if memoire:
        valeur = min(valeur, int(memoire // GO_PAR_COMPILATION))
    return max(1, valeur)


def recommande() -> int:
    """Réglage automatique : 3/4 des cœurs, dans la limite du maximum."""
    return max(1, min(processeur() * 3 // 4, maximum()))


def choix() -> str | int:
    """Ce que l'utilisateur a choisi : « auto » ou un nombre."""
    try:
        valeur = json.loads(REGLAGES.read_text(encoding='utf-8')).get('coeurs', 'auto')
    except (OSError, ValueError, AttributeError):
        return 'auto'
    if isinstance(valeur, int) and not isinstance(valeur, bool) and valeur > 0:
        return valeur
    return 'auto'


def nombre() -> int:
    """Nombre de compilations en parallèle à utiliser maintenant."""
    for variable in ('PHANTOM_JOBS', 'PHANTOM_DC_JOBS'):
        try:
            force = int(os.environ.get(variable, '0'))
        except ValueError:
            force = 0
        if force > 0:
            return min(force, processeur())
    valeur = choix()
    return recommande() if valeur == 'auto' else max(1, min(valeur, maximum()))


def enregistrer(valeur: str | int) -> None:
    """Enregistre « auto » ou un nombre de cœurs (borné) dans compilateur.json."""
    if valeur != 'auto':
        valeur = max(1, min(int(valeur), maximum()))
    REGLAGES.parent.mkdir(parents=True, exist_ok=True)
    try:
        reglages = json.loads(REGLAGES.read_text(encoding='utf-8'))
        if not isinstance(reglages, dict):
            reglages = {}
    except (OSError, ValueError):
        reglages = {}
    reglages['coeurs'] = valeur
    temporaire = REGLAGES.with_name(REGLAGES.name + '.tmp')
    temporaire.write_text(json.dumps(reglages, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temporaire, REGLAGES)
