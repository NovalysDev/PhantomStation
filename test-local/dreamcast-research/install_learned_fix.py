"""Rebuild one of the user's Dreamcast games with the learning compiler and
replace it in place: same identity and save folder, old file kept aside."""
import hashlib
import os
from pathlib import Path
import shutil
import sys
import uuid
import zipfile

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root))
from bibliotheque import game_info, game_running
import phantom_dreamcast

old, disc = Path(sys.argv[1]), Path(sys.argv[2])
original = game_info(old)
if game_running(original): raise RuntimeError('Le jeu est ouvert : installation reportée.')
folder = Path(__file__).resolve().parent / 'correction-apprentissage' / old.stem
folder.mkdir(parents=True, exist_ok=True)
candidate = phantom_dreamcast.build(disc, original['name'], folder, package_id=original['id'])
updated = game_info(candidate)
if original['id'] != updated['id'] or original['name'] != updated['name']:
    raise RuntimeError('Le jeu ou son dossier de sauvegarde ne correspond pas.')
with zipfile.ZipFile(old) as before, zipfile.ZipFile(candidate) as after:
    if after.testzip(): raise RuntimeError('Le fichier créé est endommagé.')
    for name in ('disque.chd', '1ST_READ.BIN'):
        with before.open(name) as a, after.open(name) as b:
            if hashlib.file_digest(a, 'sha256').digest() != hashlib.file_digest(b, 'sha256').digest():
                raise RuntimeError('Les données du disque ne correspondent pas : ' + name)
backup = folder / 'avant-correction.exe'
if not backup.exists(): shutil.copy2(old, backup)
stat = old.stat()
temporary = old.with_name('.phantom-' + uuid.uuid4().hex + '.exe')
try:
    shutil.copy2(candidate, temporary)
    if game_running(original): raise RuntimeError('Le jeu vient de démarrer : installation reportée.')
    current = old.stat()
    if (current.st_size, current.st_mtime_ns) != (stat.st_size, stat.st_mtime_ns):
        raise RuntimeError('Le fichier a changé pendant la vérification : installation reportée.')
    os.replace(temporary, old)
finally:
    temporary.unlink(missing_ok=True)
print('Correction installée ; ancien fichier conservé :', backup)
