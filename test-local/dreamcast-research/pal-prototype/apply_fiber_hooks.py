"""Install opt-in PAL prototype fiber adapters into freshly translated C.

This modifies generated game-derived C locally. The hooks remain disabled until
DCRECOMP_TEST_SAVE_SCHEDULER is set; they are not a general Dreamcast backend.
"""
from __future__ import annotations
import argparse
from pathlib import Path
import re

HOOKS = {
    0x8C17020A: 'pal_vmsfb_create',
    0x8C170222: 'pal_vmsfb_exec',
    0x8C170232: 'pal_vmsfb_sleep',
    0x8C170238: 'pal_vmsfb_end',
}
MARKER = '/* PhantomStation optional VMU fiber hooks */'
PREFIX = '''/* PhantomStation optional VMU fiber hooks */
#include <stdlib.h>
#include "recompiler/sh4_cpu.h"
void pal_vmsfb_create(SH4CPU *);
void pal_vmsfb_exec(SH4CPU *);
void pal_vmsfb_sleep(SH4CPU *);
void pal_vmsfb_end(SH4CPU *);
'''


def apply_hooks(game_dir: Path) -> int:
    sources = sorted(game_dir.glob('game_code_*.c'))
    if not sources:
        raise ValueError('Aucun fichier C de jeu généré dans ce dossier.')
    contents = {path: path.read_text(encoding='utf-8') for path in sources}
    updated = {}
    for address, hook in HOOKS.items():
        pattern = re.compile(rf'(void\s+func_{address:08X}\s*\(\s*SH4CPU\s*\*\s*cpu\s*\)\s*\{{)')
        matching = [(path, pattern.search(text)) for path, text in contents.items()]
        matching = [(path, m) for path, m in matching if m]
        if len(matching) != 1:
            raise ValueError(f'Fonction {address:08X} introuvable ou en double.')
        path, match = matching[0]
        text = contents[path]
        statement = f' if (getenv("DCRECOMP_TEST_SAVE_SCHEDULER") || getenv("PHANTOM_DC_FIBERS")) {{ {hook}(cpu); return; }}'
        if not text[match.end():].lstrip().startswith(statement.lstrip()):
            text = text[:match.end()] + statement + text[match.end():]
        contents[path] = text
        updated[path] = text
    for path, text in updated.items():
        if MARKER not in text:
            text = PREFIX + text
        path.write_text(text, encoding='utf-8', newline='\n')
    return len(HOOKS)


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('generated_game_dir', type=Path)
    args=parser.parse_args()
    print(f'{apply_hooks(args.generated_game_dir)} adaptateurs VMU activables installés.')


if __name__=='__main__':
    main()
