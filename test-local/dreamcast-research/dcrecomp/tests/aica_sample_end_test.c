/* Exercise the actual channel decoder at the boundary of a short sound. */
#include "../src/hal/aica_pcm.c"
#include <assert.h>

int main(void) {
    uint8_t ram[32] = { 0x10, 0x00, 0, 0, 0, 0, 0x30, 0x00 };
    Flux f = {0};
    Voie c = {0};
    f.fmt = 0; f.lea = 4; f.lsa = 0; c.ca = 3;
    decode(ram, &c, &f, 1);
    assert(c.s0 == 0x30 && c.s1 == 0x30);
    f.lpctl = 1;
    decode(ram, &c, &f, 1);
    assert(c.s0 == 0x30 && c.s1 == 0x10);

    memset(&c, 0, sizeof c);
    memset(ram, 0x77, sizeof ram);
    c.quant = 127; c.ca = 3;
    f.fmt = 2; f.lpctl = 0;
    decode(ram, &c, &f, 1);
    assert(c.s0 != 0 && c.s1 == c.s0);
    return 0;
}
