#include <assert.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include "hal/aica_pcm.h"

int main(void) {
    uint32_t regs[64 * 32] = {0};
    uint8_t *ram = calloc(1, 0x200000);
    int16_t output[2 * 1024];
    assert(ram);
    aica_pcm_reset();
    aica_pcm_mix(regs, ram, output, 1);
    /* Grandia waveform muted by default; loop and fast envelope release. */
    regs[0] = 0x4000 | 0x0200; regs[1] = 0x3fac; regs[2] = 0;
    regs[3] = 0x54; regs[4] = 31; regs[5] = 31; regs[6] = 15 << 11;
    regs[9] = 15 << 8;
    aica_pcm_key_execute(regs);
    assert(aica_pcm_channel_active(0));
    memset(ram + 0x3fac, 0x7f, 0xa8);
    aica_pcm_mix(regs, ram, output, 1024);
    for (int i=0; i<2*1024; ++i) assert(output[i] == 0);
    regs[0] &= ~0x4000u; aica_pcm_key_execute(regs);
    for (int i=0; i<8; ++i) aica_pcm_mix(regs, ram, output, 1024);
    assert(!aica_pcm_channel_active(0));
    /* The same channel must be available for the next menu selection beep. */
    memset(ram + 0x4070, 0x40, 0xa9);
    regs[0] = 0x4000 | 0x0200 | 0x0080; regs[1] = 0x4070;
    regs[3] = 0xa9; regs[6] = 0;
    aica_pcm_key_execute(regs);
    aica_pcm_mix(regs, ram, output, 1024);
    int audible = 0; for (int i=0; i<2*1024; ++i) audible |= output[i] != 0;
    assert(audible);
    free(ram);
    return 0;
}
