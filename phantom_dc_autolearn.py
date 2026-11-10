"""Prepare Dreamcast learning candidates without changing a running game.

A candidate is tried on the next launch and kept after it has been used.
Launching a game does not prove that a missing boss or scene now works.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
import zipfile
from contextlib import contextmanager

import bibliotheque

BASE = Path(__file__).resolve().parent
LAB = BASE / 'test-local/dreamcast-research'


def _local():
    return Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData/Local') / 'PhantomStation'


def _identity(game):
    ident = str(game.get('id', ''))
    if game.get('platform') != 'DC' or not re.fullmatch(r'[0-9a-f]{32}', ident):
        raise ValueError('Jeu Dreamcast invalide.')
    return ident


def _paths(game):
    ident = _identity(game)
    local = _local()
    cache = local / 'Jeux' / ident
    state = local / 'Apprentissage-candidats' / ident
    return cache, state


def _sha(path):
    with Path(path).open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def _boot_id(boot):
    with Path(boot).open('rb') as source:
        return hashlib.file_digest(source, 'sha1').hexdigest()


def _learn_module():
    if str(LAB) not in sys.path: sys.path.insert(0, str(LAB))
    import apprentissage
    return apprentissage


def _baseline(cache):
    original = cache / 'dreamcast.exe'
    original_hash = _sha(original)
    marker, fixed = cache / 'dreamcast-correction.sha256', cache / 'dreamcast-correction.exe'
    if not marker.exists(): return original_hash, original_hash
    if marker.is_symlink() or marker.stat().st_size > 256: raise ValueError('Correction existante invalide.')
    lines = marker.read_text(encoding='ascii').splitlines()
    if len(lines) != 2 or any(not re.fullmatch(r'[0-9a-f]{64}', x) for x in lines):
        raise ValueError('Correction existante invalide.')
    if lines[0] != original_hash: return original_hash, original_hash
    if not fixed.is_file() or fixed.is_symlink() or _sha(fixed) != lines[1]:
        raise ValueError('Correction existante endommagée.')
    return original_hash, lines[1]


def _status(game, state, detail=''):
    try:
        folder = _local() / 'Apprentissage-etat'
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / (_identity(game) + '.json')
        tmp = folder / (path.name + '.' + uuid.uuid4().hex + '.tmp')
        try:
            tmp.write_text(json.dumps({'etat': state, 'detail': detail}, ensure_ascii=False), encoding='utf-8')
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)
    except (OSError, ValueError):
        pass


@contextmanager
def _lock(game):
    import msvcrt
    folder = _local() / 'Apprentissage-etat'
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (_identity(game) + '.lock')
    with path.open('a+b') as stream:
        stream.seek(0)
        try:
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            yield False
            return
        try:
            yield True
        finally:
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


def _delta(before, after):
    old_main = before.main
    old_blocks = {b['file']: b for b in before.blocks}
    items = [('principal', a) for a in sorted(after.main - old_main)]
    for b in after.blocks:
        old = old_blocks.get(b['file'])
        previous = old['targets'] if old and old['data'] == b['data'] else set()
        items.extend((b['file'], t) for t in sorted(b['targets'] - previous))
    fingerprint = hashlib.sha256(json.dumps(items, separators=(',', ':')).encode('ascii')).hexdigest()
    return items, fingerprint


def _snapshot_profile(learned, destination):
    destination.mkdir(parents=True, exist_ok=False)
    learned.root = destination
    learned.save()


def _check_snapshot(folder):
    if not folder.is_dir() or folder.is_symlink() or getattr(folder.lstat(), 'st_file_attributes', 0) & 0x400:
        raise ValueError('Dossier du moteur d’essai invalide.')
    for item in folder.iterdir():
        if (item.is_symlink() or not item.is_file() or getattr(item.lstat(), 'st_file_attributes', 0) & 0x400 or
            not (item.name in ('profil.json', 'metadata.json') or re.fullmatch(r'bloc-[0-9a-f]{12}\.bin', item.name))):
            raise ValueError('Contenu inattendu dans le dossier d’apprentissage.')


def _snapshot_digest(folder):
    digest = hashlib.sha256()
    for item in sorted(folder.iterdir(), key=lambda p: p.name):
        if item.name == 'metadata.json': continue
        digest.update(item.name.encode('ascii') + b'\0')
        digest.update(bytes.fromhex(_sha(item)))
    return digest.hexdigest()


def _remove_snapshot(folder):
    parent = (_local() / 'Apprentissage-candidats').resolve(strict=True)
    resolved = folder.resolve(strict=True)
    if resolved.parent != parent or not re.fullmatch(r'[0-9a-f]{32}', resolved.name):
        raise ValueError('Suppression du candidat refusée.')
    _check_snapshot(resolved)
    for item in resolved.iterdir(): item.unlink()
    resolved.rmdir()


def candidate(game):
    """Return verified candidate metadata, or None when no trial is ready."""
    cache, state = _paths(game)
    marker, exe = cache / 'dreamcast-candidat.sha256', cache / 'dreamcast-candidat.exe'
    if not marker.is_file() or not exe.is_file() or not state.is_dir(): return None
    if marker.is_symlink() or exe.is_symlink() or marker.stat().st_size > 256: return None
    _check_snapshot(state)
    data = json.loads((state / 'metadata.json').read_text(encoding='utf-8'))
    if data.get('id') != _identity(game) or data.get('version') != 1: return None
    if _snapshot_digest(state) != data.get('profil'): return None
    lines = marker.read_text(encoding='ascii').splitlines()
    if len(lines) != 3 or lines != [data.get('original'), data.get('base'), data.get('candidat')]: return None
    original, base = _baseline(cache)
    if (original, base, _sha(exe)) != tuple(lines): return None
    if _boot_id(cache / '1ST_READ.BIN') != data.get('disque'): return None
    return data


def _rejected(game):
    path = _local() / 'Apprentissage-etat' / (_identity(game) + '-refuse.json')
    try: return json.loads(path.read_text(encoding='utf-8')).get('empreinte')
    except (OSError, ValueError, AttributeError): return None


def prepare(game, builder=None, smoke=None):
    """Build an isolated candidate; never replace the player's current engine."""
    cache, state = _paths(game)
    with _lock(game) as acquired:
        if not acquired: return False
        if bibliotheque.game_running(game):
            _status(game, 'attente', 'Le jeu est ouvert ; réessai à sa fermeture.')
            return False
        if candidate(game) is not None:
            _status(game, 'pret', 'Version d’essai prête pour la prochaine partie.')
            return False
        if state.exists() or (cache / 'dreamcast-candidat.exe').exists() or (cache / 'dreamcast-candidat.sha256').exists():
            raise ValueError('Un ancien candidat incomplet doit être vérifié.')
        boot, disc = cache / '1ST_READ.BIN', cache / 'disque.chd'
        if not boot.is_file() or not disc.is_file(): return False
        boot_id = _boot_id(boot)
        source = _local() / 'Dreamcast-apprentissage' / boot_id
        learn = _learn_module()
        before, merged = learn.Learned(source), learn.Learned(source)
        for session in sorted(source.glob('partie-*')):
            if session.is_dir() and not session.is_symlink():
                merged.merge_run(session, 0x8c010000 + boot.stat().st_size)
        if bibliotheque.game_running(game):
            _status(game, 'attente', 'Le jeu est ouvert ; réessai à sa fermeture.')
            return False
        entries, fingerprint = _delta(before, merged)
        if not entries or fingerprint == _rejected(game):
            _status(game, 'aucun', 'Aucune nouvelle fonction à préparer.')
            return False
        original_hash, base_hash = _baseline(cache)
        _status(game, 'preparation', 'Une version d’essai est en cours de compilation.')
        scratch_parent = _local() / 'Apprentissage-temp'
        scratch_parent.mkdir(parents=True, exist_ok=True)
        if scratch_parent.is_symlink() or getattr(scratch_parent.lstat(), 'st_file_attributes', 0) & 0x400:
            raise ValueError('Dossier temporaire invalide.')
        scratch_root = scratch_parent.resolve(strict=True)
        folder = Path(tempfile.mkdtemp(prefix='jeu-' + _identity(game) + '-', dir=scratch_root)).resolve(strict=True)
        if folder.parent != scratch_root: raise ValueError('Dossier temporaire hors emplacement.')
        previous_local = os.environ.get('LOCALAPPDATA')
        previous_rounds = os.environ.get('PHANTOM_DC_LEARN_ROUNDS')
        previous_strict = os.environ.get('PHANTOM_DC_STRICT_LEARN')
        previous_keep = os.environ.get('PHANTOM_DC_KEEP_WORK')
        pending = cache / ('candidat-' + uuid.uuid4().hex + '.tmp')
        pending_marker = cache / ('candidat-' + uuid.uuid4().hex + '.sha.tmp')
        stage = state.with_name(state.name + '-' + uuid.uuid4().hex + '.part')
        published_state = published_exe = published_marker = False
        try:
            sandbox_local = folder / 'local'
            _snapshot_profile(merged, sandbox_local / 'PhantomStation/Dreamcast-apprentissage' / boot_id)
            os.environ['LOCALAPPDATA'] = str(sandbox_local)
            os.environ['PHANTOM_DC_LEARN_ROUNDS'] = '1'
            os.environ['PHANTOM_DC_STRICT_LEARN'] = '1'
            os.environ['PHANTOM_DC_KEEP_WORK'] = str(folder / 'travail')
            if builder is None:
                import build_pal_prototype
                builder = build_pal_prototype.build
            result = builder(disc, folder / 'resultat', log=lambda _: None)
            binary = result / 'Crazy Taxi - prototype.exe'
            if not binary.is_file(): raise ValueError('Moteur d’essai manquant.')
            table = (folder / 'travail/game/src/game/learned_table.c').read_text(encoding='utf-8')
            dispatch = (folder / 'travail/game/src/game/dispatch_table.c').read_text(encoding='utf-8')
            for owner, addr in entries:
                marker = f'0x{addr:08X}u'
                if owner == 'principal':
                    if marker not in dispatch: raise ValueError('Une entrée du programme principal manque.')
                else:
                    key = owner[5:-4]
                    if f'{marker}, L{key}_func_{addr:08X}' not in table:
                        raise ValueError('Une fonction découverte manque dans le moteur d’essai.')
            if smoke is None:
                smoke_dir = folder / 'verification'
                learn.learning_run(binary, boot, disc, smoke_dir, seconds=75)
                log = (smoke_dir / 'journal.log').read_text(encoding='utf-8', errors='replace')
                if '[FRAME] 120 rendered' not in log or '[PLATFORM] SDL2 shutdown' not in log:
                    raise ValueError('Le moteur d’essai n’a pas passé la vérification de démarrage.')
            else:
                smoke(binary, boot, disc)
            if _baseline(cache) != (original_hash, base_hash): raise ValueError('Le moteur de départ a changé.')
            if _boot_id(boot) != boot_id: raise ValueError('Le disque a changé.')
            candidate_hash = _sha(binary)
            snapshot = sandbox_local / 'PhantomStation/Dreamcast-apprentissage' / boot_id
            state.parent.mkdir(parents=True, exist_ok=True)
            stage.mkdir(exist_ok=False)
            for item in snapshot.iterdir():
                if item.name == 'profil.json' or re.fullmatch(r'bloc-[0-9a-f]{12}\.bin', item.name):
                    if item.is_symlink() or not item.is_file(): raise ValueError('Bloc d’apprentissage invalide.')
                    shutil.copy2(item, stage / item.name)
            metadata = {'version': 1, 'id': _identity(game), 'disque': boot_id, 'original': original_hash,
                        'base': base_hash, 'candidat': candidate_hash, 'empreinte': fingerprint,
                        'profil': _snapshot_digest(stage)}
            (stage / 'metadata.json').write_text(json.dumps(metadata), encoding='utf-8')
            _check_snapshot(stage)
            with binary.open('rb') as src, pending.open('xb') as dst: shutil.copyfileobj(src, dst, 1024*1024)
            if _sha(pending) != candidate_hash: raise ValueError('Copie du candidat incomplète.')
            pending_marker.write_text('\n'.join((original_hash, base_hash, candidate_hash)) + '\n', encoding='ascii')
            # The marker is published last: no launcher can see an incomplete candidate.
            os.rename(stage, state); published_state = True
            os.rename(pending, cache / 'dreamcast-candidat.exe'); published_exe = True
            os.rename(pending_marker, cache / 'dreamcast-candidat.sha256'); published_marker = True
            if previous_local is None: os.environ.pop('LOCALAPPDATA', None)
            else: os.environ['LOCALAPPDATA'] = previous_local
            _status(game, 'pret', 'Version d’essai prête pour la prochaine partie.')
            return True
        except Exception:
            if previous_local is None: os.environ.pop('LOCALAPPDATA', None)
            else: os.environ['LOCALAPPDATA'] = previous_local
            if published_marker: (cache / 'dreamcast-candidat.sha256').unlink(missing_ok=True)
            if published_exe: (cache / 'dreamcast-candidat.exe').unlink(missing_ok=True)
            if published_state: _remove_snapshot(state)
            raise
        finally:
            for key, value in (('LOCALAPPDATA', previous_local), ('PHANTOM_DC_LEARN_ROUNDS', previous_rounds),
                               ('PHANTOM_DC_STRICT_LEARN', previous_strict), ('PHANTOM_DC_KEEP_WORK', previous_keep)):
                if value is None: os.environ.pop(key, None)
                else: os.environ[key] = value
            pending.unlink(missing_ok=True); pending_marker.unlink(missing_ok=True)
            # Only remove our newly created work directory after checking its resolved parent.
            if folder.exists():
                resolved = folder.resolve(strict=True)
                if resolved != folder or resolved.parent != scratch_root: raise ValueError('Nettoyage temporaire refusé.')
                shutil.rmtree(resolved)
            if stage.exists():
                _check_snapshot(stage)
                for item in stage.iterdir(): item.unlink()
                stage.rmdir()


def _merge_confirmed_profile(game, state, boot_id):
    """Merge the retained candidate, preserving later discoveries."""
    actual = _local() / 'Dreamcast-apprentissage' / boot_id
    actual.mkdir(parents=True, exist_ok=True)
    path = actual / 'profil.json'
    prior = path.read_bytes() if path.exists() else None
    old = json.loads(prior) if prior is not None else {'principal': [], 'blocs': []}
    incoming = json.loads((state / 'profil.json').read_text(encoding='utf-8'))
    if not isinstance(old.get('principal'), list) or not isinstance(old.get('blocs'), list):
        raise ValueError('Profil d’apprentissage invalide.')
    old['principal'] = sorted(set(old['principal']) | set(incoming['principal']))
    by_name = {b['fichier']: b for b in old['blocs']}
    for entry in incoming['blocs']:
        name = entry.get('fichier', '')
        if not re.fullmatch(r'bloc-[0-9a-f]{12}\.bin', name): raise ValueError('Nom de bloc invalide.')
        source, target = state / name, actual / name
        if not source.is_file() or source.is_symlink(): raise ValueError('Bloc confirmé manquant.')
        if target.exists():
            if target.is_symlink() or _sha(source) != _sha(target): raise ValueError('Conflit entre blocs enregistrés.')
        else:
            with source.open('rb') as src, target.open('xb') as dst: shutil.copyfileobj(src, dst, 1024*1024)
        previous = by_name.get(name)
        if previous:
            if any(previous.get(k) != entry.get(k) for k in ('base', 'taille', 'source')):
                raise ValueError('Conflit dans le profil d’apprentissage.')
            previous['cibles'] = sorted(set(previous['cibles']) | set(entry['cibles']))
        else:
            old['blocs'].append(entry)
            by_name[name] = entry
    tmp = actual / ('profil-' + uuid.uuid4().hex + '.tmp')
    try:
        tmp.write_text(json.dumps(old, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
        if (path.read_bytes() if path.exists() else None) != prior:
            raise ValueError('Le profil a changé pendant sa vérification.')
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def decide(game, expected_hash, accepted):
    """Keep or discard a candidate after checking its identity and integrity."""
    cache, state = _paths(game)
    with _lock(game) as acquired:
        if not acquired: raise ValueError('L’apprentissage de ce jeu est encore en cours.')
        if bibliotheque.game_running(game): raise ValueError('Ferme le jeu avant de choisir.')
        data = candidate(game)
        if not data or data['candidat'] != expected_hash:
            raise ValueError('La version d’essai a changé ; elle n’a pas été modifiée.')
        trial = cache / 'dreamcast-candidat.exe'
        trial_marker = cache / 'dreamcast-candidat.sha256'
        if accepted:
            _merge_confirmed_profile(game, state, data['disque'])
            fixed, fixed_marker = cache / 'dreamcast-correction.exe', cache / 'dreamcast-correction.sha256'
            old_marker = fixed_marker.read_bytes() if fixed_marker.exists() else None
            backup = cache / ('ancien-' + uuid.uuid4().hex + '.tmp')
            pending = cache / ('nouveau-' + uuid.uuid4().hex + '.tmp')
            pending_marker = cache / ('empreinte-' + uuid.uuid4().hex + '.tmp')
            existed = fixed.exists()
            swapped = False
            try:
                if existed: shutil.copy2(fixed, backup)
                with trial.open('rb') as src, pending.open('xb') as dst: shutil.copyfileobj(src, dst, 1024*1024)
                if _sha(pending) != expected_hash: raise ValueError('Copie du moteur d’essai invalide.')
                pending_marker.write_text(data['original'] + '\n' + expected_hash + '\n', encoding='ascii')
                if bibliotheque.game_running(game) or _baseline(cache) != (data['original'], data['base']):
                    raise ValueError('Le jeu ou son moteur a changé.')
                os.replace(pending, fixed); swapped = True
                os.replace(pending_marker, fixed_marker)
                if _sha(fixed) != expected_hash: raise ValueError('Vérification du moteur conservé impossible.')
                swapped = False
            except Exception:
                if swapped:
                    if existed: os.replace(backup, fixed)
                    else: fixed.unlink(missing_ok=True)
                    if old_marker is None:
                        fixed_marker.unlink(missing_ok=True)
                    else:
                        pending_marker.write_bytes(old_marker)
                        os.replace(pending_marker, fixed_marker)
                raise
            finally:
                backup.unlink(missing_ok=True); pending.unlink(missing_ok=True); pending_marker.unlink(missing_ok=True)
            _status(game, 'confirme', 'Version utilisée conservée ; le passage reste à vérifier en jeu.')
        else:
            rejected = _local() / 'Apprentissage-etat' / (_identity(game) + '-refuse.json')
            tmp = rejected.with_name(rejected.name + '.' + uuid.uuid4().hex + '.tmp')
            try:
                tmp.write_text(json.dumps({'empreinte': data['empreinte']}), encoding='utf-8')
                os.replace(tmp, rejected)
            finally:
                tmp.unlink(missing_ok=True)
            _status(game, 'refuse', 'Le moteur précédent est conservé.')
        # The current runtime was never changed during the trial. Remove only
        # the verified candidate and the specifically owned snapshot files.
        _check_snapshot(state)
        trial_marker.unlink(); trial.unlink(); _remove_snapshot(state)


def queue(game):
    """Start a detached worker, leaving the game and UI responsive."""
    if game.get('platform') != 'DC': return
    ident = _identity(game)
    _status(game, 'verification', 'Recherche de nouvelles fonctions à préparer.')
    try:
        return subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--worker', str(game['path']), ident],
                                cwd=str(BASE), creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        _status(game, 'echec', 'Le travail d’apprentissage n’a pas pu démarrer.')
        raise


def _worker(path, ident):
    game = {'id': ident, 'platform': 'DC'}
    try:
        current = bibliotheque.game_info(path, include_cover=False)
        if current['id'] != _identity(game) or current['platform'] != 'DC':
            raise ValueError('Le fichier du jeu a changé.')
        game = current
        prepare(game)
    except Exception as exc:
        _status(game, 'echec', type(exc).__name__ + ' : ' + str(exc)[:300])


if __name__ == '__main__':
    if len(sys.argv) == 4 and sys.argv[1] == '--worker': _worker(Path(sys.argv[2]), sys.argv[3])
