"""Masque la petite image BIOS en double, sans modifier le fichier BIOS."""
from pathlib import Path

MARKER = 'PHANTOM_HIDE_LOW_RES_BIOS_PICTURE_V1'


def install_bios_display_fix(project):
    path = Path(project) / 'psxrecomp' / 'runtime' / 'src' / 'memory.c'
    source = path.read_text(encoding='utf-8')
    if MARKER in source:
        return
    anchor = ('    for (uint32_t i = 0; i < BIOS_ROM_SIZE / 4; i++)\n'
              '        s_bios_checksum += ((const uint32_t*)bios_rom)[i];\n')
    if source.count(anchor) != 1:
        raise ValueError('La structure mémoire du moteur a changé : correction BIOS impossible.')
    fix = '''    /* PHANTOM_HIDE_LOW_RES_BIOS_PICTURE_V1
     * The custom BIOS contains a 256x192 bitmap at ROM offset 0x9440.
     * PhantomStation already displays the HD intro; suppress only this
     * duplicate bitmap in memory after recording the original checksum.
     * The exact BIOS identity gate leaves all other BIOS images untouched. */
    if (psx_bios_image.image_sha256 &&
        strcmp(psx_bios_image.image_sha256,
               "7b3655e7caa3f5e404f361f6068a4690d840ccae901d288e92af517d02edb664") == 0) {
        memset(bios_rom + 0x9440u, 0, 256u * 192u * 2u);
    }
'''
    path.write_text(source.replace(anchor, anchor + fix, 1), encoding='utf-8', newline='\n')
