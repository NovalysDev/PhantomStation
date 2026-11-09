"""Install opt-in overflow texture adapters in the verified Crazy Taxi PAL build."""
from pathlib import Path

LOAD = ('    cpu->pr = 0x8C073972u; cpu->pc = cpu->r[3]; /* jsr @r3 */\n'
        '    cpu->r[4] = cpu->r[13]; /* delay slot */\n'
        '    sh4_call_indirect(cpu);\n')
LOAD_HOOK = ('    if (cpu->r[0] == 3 && pal_texture_fallback(cpu,cpu->r[13],'
             'sh4_read32(cpu,cpu->r[15]+4))) { cpu->r[0]=0; goto L_8C0739B2; }\n')
FREE = 'void func_8C15C228(SH4CPU *cpu) {\n'
FREE_HOOK = '    if (pal_texture_free(cpu,cpu->r[4])) { cpu->r[0]=0; return; }\n'
HEADER = '#include "game/game_functions.h"'
MARKER = '/* PhantomStation overflow texture adapters */'


def apply_hooks(source: Path) -> None:
    files = sorted(source.glob('game_code_*.c'))
    texts = {p: p.read_text(encoding='utf8') for p in files}
    load = [p for p, t in texts.items() if LOAD in t]
    free = [p for p, t in texts.items() if FREE in t]
    if len(load) != 1 or len(free) != 1:
        raise ValueError('Adaptateurs de textures introuvables ou en double.')
    for path, needle, hook in ((load[0], LOAD, LOAD_HOOK), (free[0], FREE, FREE_HOOK)):
        text = texts[path]
        if needle + hook not in text:
            text = text.replace(needle, needle + hook, 1)
        if MARKER not in text:
            if HEADER not in text:
                raise ValueError('En-tête de code du jeu introuvable.')
            declarations = (HEADER + '\n' + MARKER + '\n'
                            'int pal_texture_fallback(SH4CPU *, unsigned, unsigned);\n'
                            'int pal_texture_free(SH4CPU *, unsigned);')
            text = text.replace(HEADER, declarations, 1)
        texts[path] = text
    for path in set(load + free):
        path.write_text(texts[path], encoding='utf8', newline='\n')
