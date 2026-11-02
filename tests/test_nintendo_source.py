import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

from PIL import Image
import phantom_nintendo as nintendo
from bibliotheque import game_info
from detection_doublons import find_duplicates

with tempfile.TemporaryDirectory(prefix='phantom-source-nintendo-') as folder:
    root = Path(folder)
    rom = root/'Jeu (France).z64'
    rom.write_bytes(b'cartouche' * 20000)
    runtime = root/'nintendo.exe'
    runtime.write_bytes(b'moteur-test')

    def fake_art(platform, rom_path, name, icon, work, log):
        shutil.copyfile(nintendo.BASE/'assets/phantomstation.ico', work/'icone.ico')
        for ext in ('png', 'tga'):
            Image.new('RGB', (12, 18), 'blue').save(work/('boxart.' + ext))

    with patch.object(nintendo, 'make_art', side_effect=fake_art):
        package = nintendo.package_game('N64', rom, 'Jeu', runtime, root/'sortie')
    info = game_info(package, include_cover=False)
    assert info['source']['console'] == 'N64'
    info['name'] = 'Titre changé'
    info['original_name'] = 'Titre ancien'
    matches = find_duplicates('N64', 'Un autre titre', [rom], [info])
    assert len(matches) == 1 and matches[0][1] == 'source'
print('Paquet Nintendo : source retrouvée même après renommage')
