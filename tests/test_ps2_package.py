import tempfile, sys, zipfile, json
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import phantom_ps2 as ps2
from bibliotheque import game_info
from detection_doublons import fingerprint_selection
with tempfile.TemporaryDirectory() as d:
    root=Path(d); runtime=root/'runtime'; runtime.mkdir(); output=root/'sortie'; output.mkdir()
    (runtime/'ps2run.exe').write_bytes(b'programme-test'); (runtime/'SDL2.dll').write_bytes(b'dll-test')
    iso=root/'mon jeu.iso'; iso.write_bytes(b'iso-test'*3000)
    elf=root/'boot.elf'; elf.write_bytes(b'elf-test')
    seen=[]
    with patch.object(ps2, 'find_ps2_codes', return_value=([], [])):
        result=ps2.package_ps2('Jeu PS2',iso,runtime,elf,'SLES_123.45',output, str(ps2.BASE/'assets/phantomstation.ico'), progress=lambda a,b,c:seen.append((a,b,c)))
    assert result.parent==output and result.is_file() and iso.is_file()
    info=game_info(result)
    assert info['name']=='Jeu PS2' and info['platform']=='PS2' and info['documents_saves']
    assert info['source'] == fingerprint_selection('PS2', [iso])
    with zipfile.ZipFile(result) as z:
        assert z.read('media/disque.iso')==iso.read_bytes() and z.read('ps2-boot.elf')==b'elf-test'
    assert any(x[2]=='Vérification du fichier unique' and x[0]==x[1] for x in seen)
    print('OK : EXE PS2, disque intégré, bibliothèque et progression')
