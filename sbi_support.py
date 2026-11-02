"""Identify protected discs and obtain their matching SBI data."""
import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent
MAX_SBI = 64 * 1024


def validate_sbi(data):
    if not data.startswith(b'SBI\0') or len(data) <= 4 or len(data) > MAX_SBI or (len(data) - 4) % 14:
        raise ValueError('Le fichier SBI est invalide ou incompatible avec le moteur.')
    seen = set()
    for i in range(4, len(data), 14):
        record = data[i:i + 14]
        if record[3] != 1 or any((v & 15) > 9 or (v >> 4) > 9 for v in record[:3]):
            raise ValueError('Le fichier SBI contient un format non pris en charge.')
        if record[:3] in seen:
            raise ValueError('Le fichier SBI contient des secteurs en double.')
        seen.add(record[:3])
    return data


def read_sbi_file(path):
    path = Path(path)
    if not path.is_file() or path.stat().st_size > MAX_SBI:
        raise ValueError('Le fichier SBI est introuvable ou trop volumineux.')
    return validate_sbi(path.read_bytes())


def identify_disc(cue, python, environment):
    probe = BASE / 'psxrecomp/tools/new_project_layout/probe_disc.py'
    result = subprocess.run([str(python), str(probe), str(cue), '--identity-only'], env=environment,
                            capture_output=True, text=True, encoding='utf-8', errors='replace',
                            creationflags=subprocess.CREATE_NO_WINDOW, timeout=45)
    if result.returncode:
        raise ValueError('Impossible de reconnaître le disque : ' + (result.stderr or result.stdout).strip()[-500:])
    serial = str(json.loads(result.stdout).get('serial', '')).upper()
    return serial if re.fullmatch(r'(?:SLES|SCES|SLUS|SCUS|SLPS|SLPM|SCPS)-\d{5}', serial) else ''


def catalogue():
    return json.loads((BASE / 'assets/sbi-index.json').read_text(encoding='utf-8'))['games']


def manual_match(paths, cue, serial, disc_count):
    matches = []
    needle = re.sub(r'[^A-Z0-9]', '', serial.upper())
    for raw in paths:
        path = Path(raw)
        normalized = re.sub(r'[^A-Z0-9]', '', path.stem.upper())
        embedded = re.search(r'(?:SLES|SCES|SLUS|SCUS|SLPS|SLPM|SCPS)\d{5}', normalized)
        if embedded and needle and embedded.group() != needle:
            continue
        if path.stem.casefold() == Path(cue).stem.casefold() or (needle and needle in normalized):
            matches.append(path)
    if len(matches) == 1:
        return matches[0]
    if not matches and len(paths) == 1 and disc_count == 1:
        normalized = re.sub(r'[^A-Z0-9]', '', Path(paths[0]).stem.upper())
        embedded = re.search(r'(?:SLES|SCES|SLUS|SCUS|SLPS|SLPM|SCPS)\d{5}', normalized)
        if embedded and needle and embedded.group() != needle:
            raise ValueError('Le numéro indiqué dans le fichier SBI ne correspond pas à ce disque.')
        return Path(paths[0])
    return None


def _sevenzip():
    candidates = [shutil.which('7z'), str(Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / '7-Zip/7z.exe'),
                  str(BASE / 'toolchain/bin/7z.exe')]
    return next((p for p in candidates if p and Path(p).is_file()), None)


def download_sbi(serial, urls):
    if len(urls) != 1:
        raise ValueError('Plusieurs éditions existent : sélectionne le SBI correspondant à ton disque.')
    parts = urllib.parse.urlsplit(urls[0])
    if parts.scheme != 'https' or parts.hostname != 'psxdatacenter.com' or not parts.path.startswith('/sbifiles/') or not parts.path.lower().endswith('.7z'):
        raise ValueError('Adresse du fichier SBI non reconnue.')
    sevenzip = _sevenzip()
    if not sevenzip:
        raise ValueError('7-Zip est nécessaire pour extraire automatiquement ce SBI.')
    url = urllib.parse.urlunsplit((parts.scheme, parts.netloc, urllib.parse.quote(urllib.parse.unquote(parts.path), safe='/'), '', ''))
    request = urllib.request.Request(url, headers={'User-Agent': 'PhantomStation/1.0'})
    with urllib.request.urlopen(request, timeout=15) as response:
        blob = response.read(1024 * 1024 + 1)
    if len(blob) > 1024 * 1024:
        raise ValueError('Archive SBI trop volumineuse.')
    with tempfile.TemporaryDirectory(prefix='phantom-sbi-') as tmp:
        archive = Path(tmp) / 'source.7z'
        archive.write_bytes(blob)
        result = subprocess.run([sevenzip, 'l', '-slt', str(archive)], capture_output=True, text=True,
                                encoding='utf-8', errors='replace', creationflags=subprocess.CREATE_NO_WINDOW, timeout=20, check=True)
        listings = result.stdout.split('----------', 1)
        if len(listings) != 2:
            raise ValueError('Contenu de l’archive SBI illisible.')
        entries = []
        for block in re.split(r'\r?\n\s*\r?\n', listings[1].strip()):
            fields = dict(line.split(' = ', 1) for line in block.splitlines() if ' = ' in line)
            name, size = fields.get('Path', ''), fields.get('Size', '')
            if name.lower().endswith('.sbi') and size.isdigit() and 0 < int(size) <= MAX_SBI:
                entries.append(name)
        if not entries or len(entries) > 40:
            raise ValueError('Aucun fichier SBI adapté dans l’archive.')
        needle = re.sub(r'[^A-Z0-9]', '', serial.upper())
        exact = [name for name in entries if needle in re.sub(r'[^A-Z0-9]', '', Path(name).stem.upper())]
        if len(exact) == 1:
            selected = exact[0]
        elif not exact and len(entries) == 1:
            selected = entries[0]
        else:
            raise ValueError('Plusieurs fichiers SBI possibles : sélection manuelle nécessaire.')
        output = subprocess.run([sevenzip, 'x', '-so', '-bd', str(archive), '--', selected], capture_output=True,
                                creationflags=subprocess.CREATE_NO_WINDOW, timeout=20, check=True)
        return validate_sbi(output.stdout)


def resolve_sbi(cue, serial, manual, disc_count, index, log):
    chosen = manual_match(manual, cue, serial, disc_count)
    if chosen:
        log('SBI choisi : ' + str(chosen) + '\n')
        return read_sbi_file(chosen)
    sibling = Path(cue).with_suffix('.sbi')
    if sibling.is_file():
        try:
            data = read_sbi_file(sibling)
        except (OSError, ValueError) as exc:
            log('SBI à côté du disque inutilisable, recherche poursuivie : ' + str(exc) + '\n')
        else:
            log('SBI trouvé à côté du disque : ' + str(sibling) + '\n')
            return data
    if serial not in index:
        return None
    cached = BASE / 'assets/sbi' / (serial + '.sbi')
    if cached.is_file():
        try:
            data = read_sbi_file(cached)
        except (OSError, ValueError) as exc:
            log('SBI local inutilisable, recherche poursuivie : ' + str(exc) + '\n')
        else:
            log('SBI trouvé dans le catalogue local pour ' + serial + '.\n')
            return data
    urls = index[serial].get('urls', [])
    if len(urls) != 1:
        raise ValueError('Plusieurs versions existent ou aucun téléchargement fiable n’est répertorié.')
    log('Recherche automatique du SBI pour ' + serial + ' auprès de PSX Data Center…\n')
    return download_sbi(serial, urls)
