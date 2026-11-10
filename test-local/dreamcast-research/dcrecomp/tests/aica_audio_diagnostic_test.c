#include "hal/aica_pcm.h"
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* The same synthetic voices are rendered by the old and new mixer; their
 * resulting PCM must remain identical while diagnostics are collected. */
int main(int argc, char **argv) {
    assert(argc == 2);
    uint32_t regs[64*32] = {0};
    uint8_t *ram = calloc(0x200000, 1);
    int16_t out[735*2];
    assert(ram);
    for (unsigned i=0; i<256; ++i) {
        int16_t value = (i & 1) ? -30000 : 30000;
        ram[0x100+i*2] = (uint8_t)value;
        ram[0x101+i*2] = (uint8_t)((uint16_t)value >> 8);
    }
    for (unsigned ch=0; ch<2; ++ch) {
        uint32_t *v = regs + ch*32;
        v[0]=0xC200; v[1]=0x100; v[3]=256; v[4]=31; v[5]=31; v[9]=15<<8;
    }
    aica_pcm_reset();
    aica_pcm_mix(regs, ram, out, 1);
    aica_pcm_key_execute(regs);
    FILE *file = fopen(argv[1], "wb"); assert(file);
    for (unsigned i=0; i<30; ++i) {
        aica_pcm_mix(regs, ram, out, 735);
        assert(fwrite(out, sizeof(out), 1, file)==1);
    }
    assert(fclose(file)==0);
#ifdef TEST_AUDIO_DIAG
    aica_pcm_diagnostic_flush();
#endif
    free(ram);
    return 0;
}
