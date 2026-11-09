"""Link separately translated, non-overlapping programs from the selected disc."""
from pathlib import Path
import os
import re
import shutil

root = Path(os.environ['PHANTOM_DISC_PROFILE_ROOT'])
dst = root / 'game'
src = dst / 'src/game'
header = dst / 'include/game/game_functions.h'
dispatch = src / 'dispatch_table.c'
pattern = re.compile(r'^\s*\{ (0x8C[0-9A-F]{6}u), (func_8C[0-9A-F]{6}) \},$', re.M)
text = dispatch.read_text(encoding='utf-8')
entries = pattern.findall(text)
if not entries:
    raise RuntimeError('Table du programme principal introuvable.')
next_file = max(int(p.stem.rsplit('_',1)[1]) for p in src.glob('game_code_*.c')) + 1
declarations=[]
for overlay in (root/'overlay1', root/'overlay2'):
    extra = pattern.findall((overlay/'src/game/dispatch_table.c').read_text(encoding='utf-8'))
    sources = sorted((overlay/'src/game').glob('game_code_*.c'))
    if not extra or not sources:
        raise RuntimeError('Programme chargé depuis le disque incomplet.')
    entries.extend(extra)
    declarations.extend(f'void {name}(SH4CPU *cpu);' for _,name in extra)
    for source in sources:
        shutil.copy2(source,src/f'game_code_{next_file:03d}.c')
        next_file += 1
if len({addr for addr, _ in entries}) != len(entries):
    raise RuntimeError('Les programmes traduits se chevauchent.')
entries.sort(key=lambda item:int(item[0][:-1],16))
start=text.index('static const FuncEntry func_table[] = {')
end=text.index('\n};',start)+3
text=text[:start]+'static const FuncEntry func_table[] = {\n'+'\n'.join(
    f'    {{ {addr}, {name} }},' for addr,name in entries)+'\n};'+text[end:]
text=re.sub(r'#define FUNC_TABLE_SIZE \d+',f'#define FUNC_TABLE_SIZE {len(entries)}',text,count=1)
dispatch.write_text(text,encoding='utf-8',newline='\n')
header.write_text(header.read_text(encoding='utf-8')+'\n'+'\n'.join(declarations)+'\n',encoding='utf-8')
print(f'{len(entries)} entrées traduites depuis les programmes du disque.')
