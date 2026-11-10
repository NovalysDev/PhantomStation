"""Grandia II Europe: take the movie player's normal exit on START.

Only the disc with the verified boot hash and the exact translated call site
is changed. The generic input/video detector remains the same for other games.
"""
from pathlib import Path

BOOT_SHA1 = '0cb24122c167a4f199a8aa4408dad4eeb604a0f3'
NEEDLE = (
    '    cpu->pr = 0x8C300B7Eu; cpu->pc = cpu->r[3]; /* jsr @r3 */\n'
    '    cpu->r[4] = cpu->r[12]; /* delay slot */\n'
    '    sh4_call_indirect(cpu);\n'
    '    sh4_set_t(cpu, (int32_t)cpu->r[0] == 3);'
)
HOOK = (
    '    /* START selects the same exit as the movie player finishing. */\n'
    '    if (cpu->r[0] == 2u) dc_cinematic_offer_skip();\n'
    '    if (dc_cinematic_buttons() && cpu->r[0] == 2u) cpu->r[0] = 3u;\n'
)
HEADER = '#include "hal/dc_cinematic.h"\n'


def apply(game: Path, boot_sha1: str) -> int:
    if boot_sha1 != BOOT_SHA1:
        return 0
    changed = 0
    for source in sorted((game / 'src' / 'game').glob('game_code_L*.c')):
        text = source.read_text(encoding='utf-8')
        count = text.count(NEEDLE)
        if not count:
            continue
        if count != 1:
            raise ValueError('Le lecteur vidéo Grandia a plusieurs appels identiques.')
        replacement = NEEDLE.replace('    sh4_set_t(cpu, (int32_t)cpu->r[0] == 3);',
                                     HOOK + '    sh4_set_t(cpu, (int32_t)cpu->r[0] == 3);')
        text = text.replace(NEEDLE, replacement, 1)
        if HEADER not in text:
            text = HEADER + text
        source.write_text(text, encoding='utf-8', newline='\n')
        changed += 1
    return changed
