"""Rapports de compilation : fichier complet et brouillon dans la messagerie."""
import re
import base64
import json
import urllib.error
import urllib.parse
import urllib.request
import uuid
import traceback
import platform
from datetime import datetime
from pathlib import Path

from bibliotheque import documents_folder

# Existing public contact relay on the owner's server. The destination lives
# exclusively on the server; the client never receives or displays it.
REPORT_ENDPOINT = 'https://gears.novalysdev.fr/api/public/contact'


def report_text(game, discs, journal, when=None):
    when = when or datetime.now().astimezone()
    discs = [str(path) for path in discs]
    header = ['Rapport d’erreur PhantomStation', 'Date : ' + when.strftime('%d/%m/%Y %H:%M:%S %z'),
              'Jeu : ' + (game.strip() or 'Non renseigné'),
              'Disques : ' + (str(len(discs)) if discs else 'aucun')]
    header.extend(f'  CD {i} : {path}' for i, path in enumerate(discs, 1))
    clean = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', journal).replace('\x00', '')
    return '\n'.join(header) + '\n\n--- Journal de compilation ---\n' + (clean or 'Aucun détail disponible.') + '\n'


def launch_report_text(game, executable, error, when=None):
    when = when or datetime.now().astimezone()
    details = ''.join(traceback.format_exception(type(error), error, error.__traceback__))
    return ('Rapport de lancement PhantomStation\nDate : ' + when.strftime('%d/%m/%Y %H:%M:%S %z')
            + '\nJeu : ' + (str(game).strip() or 'Non renseigné')
            + '\nFichier : ' + str(executable) + '\nSystème : ' + platform.platform()
            + '\n\n--- Erreur de lancement ---\n' + details.replace('\x00', '') + '\n')


def save_report(text, folder=None):
    folder = Path(folder) if folder is not None else documents_folder() / 'PhantomStation' / 'Rapports'
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    for _ in range(10):
        path = folder / f'rapport-erreur-{stamp}-{uuid.uuid4().hex[:8]}.txt'
        created = False
        try:
            with path.open('x', encoding='utf-8', newline='\n') as stream:
                created = True
                stream.write(text)
            return path
        except FileExistsError:
            continue
        except Exception:
            if created: path.unlink(missing_ok=True)
            raise
    raise OSError('Impossible de créer un nom de rapport unique.')


def submission_payload(game, text, kind='compilation'):
    """Keep the failure and recent log within the relay's 2000-character limit."""
    limit = 1900
    shortened = len(text) > limit
    if shortened:
        header = re.split(r'\n\n--- (?:Journal de compilation|Erreur de lancement|Dernières lignes du jeu) ---', text, maxsplit=1)[0][:450]
        marker = '\n\n[Journal abrégé : le rapport complet reste sur le PC]\n'
        text = header + marker + text[-(limit-len(header)-len(marker)):]
    subject = re.sub(r'[\r\n\x00-\x1f]+', ' ', game).strip()[:85]
    payload = {'name': 'PhantomStation', 'email': 'noreply@phantomstation.invalid',
               'subject': ('Rapport de jeu' if kind == 'jeu' else 'Rapport de lancement' if kind == 'launch' else 'Rapport de compilation') + (' - ' + subject if subject else ''),
               'message': text, 'website': ''}
    if not 5 <= len(text) <= 2000:
        raise ValueError('Le rapport ne peut pas être préparé.')
    return payload, shortened


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def send_report(payload, endpoint=REPORT_ENDPOINT, timeout=15):
    """Transmit only after the user clicks Send; reject redirects and errors."""
    parsed = urllib.parse.urlsplit(endpoint)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ValueError('Adresse du service de rapport invalide.')
    body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    request = urllib.request.Request(endpoint, data=body, method='POST',
                                     headers={'Content-Type': 'application/json; charset=utf-8',
                                              'Accept': 'application/json', 'User-Agent': 'PhantomStation/1.0'})
    try:
        with urllib.request.build_opener(_NoRedirect()).open(request, timeout=timeout) as response:
            if response.status != 200:
                raise OSError('Le service n’a pas confirmé la réception du rapport.')
            data = response.read(8193)
        if len(data) > 8192:
            raise OSError('Réponse du service invalide.')
        result = json.loads(data.decode('utf-8'))
        if not isinstance(result, dict) or result.get('message') not in ('Message envoyé.', 'Message envoye.'):
            raise OSError('Le service n’a pas confirmé la réception du rapport.')
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            raise OSError('Trop de rapports envoyés ; réessaie dans quelques minutes.') from exc
        raise OSError('Le service de rapport est indisponible (code ' + str(exc.code) + ').') from exc
    except (urllib.error.URLError, TimeoutError, UnicodeError, ValueError) as exc:
        raise OSError('Impossible de joindre le service de rapport.') from exc


def send_game_diagnostic(payload, archive, endpoint=REPORT_ENDPOINT, timeout=30):
    """Send the complete ZIP only after the relay confirms attachment support."""
    archive = Path(archive)
    if not archive.is_file() or archive.stat().st_size > 8 * 1024 * 1024:
        raise ValueError('Diagnostic introuvable ou trop volumineux (8 Mio maximum).')
    parsed = urllib.parse.urlsplit(endpoint)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ValueError('Adresse du service de rapport invalide.')
    opener = urllib.request.build_opener(_NoRedirect())
    headers = {'Content-Type':'application/json; charset=utf-8', 'Accept':'application/json',
               'User-Agent':'PhantomStation/1.0'}
    def request(data):
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        req = urllib.request.Request(endpoint, data=body, headers=headers, method='POST')
        with opener.open(req, timeout=timeout) as response:
            raw = response.read(8193)
            if response.status != 200 or len(raw) > 8192:
                raise OSError('Réponse du service de rapport invalide.')
            return json.loads(raw.decode('utf-8'))
    try:
        probe = request({'diagnostic_probe': True})
        if not isinstance(probe, dict) or probe.get('diagnostic_complet') != 1:
            raise OSError('Le service ne prend pas encore en charge les diagnostics complets.')
        data = dict(payload)
        data['diagnostic_b64'] = base64.b64encode(archive.read_bytes()).decode('ascii')
        result = request(data)
        if not isinstance(result, dict) or result.get('diagnostic_recu') is not True:
            raise OSError('La réception du fichier complet n’a pas été confirmée.')
    except urllib.error.HTTPError as exc:
        raise OSError('Le service a refusé le diagnostic (code ' + str(exc.code) + ').') from exc
    except (urllib.error.URLError, TimeoutError, UnicodeError, ValueError) as exc:
        raise OSError('Impossible de transmettre le diagnostic complet.') from exc
