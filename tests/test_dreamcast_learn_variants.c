#include <stdlib.h>
#include "recompiler/sh4_cpu.h"

void phantom_unresolved(SH4CPU *cpu);

/* The caller checks that exactly two blocks were captured: repeating an
 * address and bytes is one observation; replacing the bytes is another. */
int main(void) {
    SH4CPU cpu = {0};
    cpu.ram_size = DC_RAM_SIZE_16MB;
    cpu.ram = calloc(1, cpu.ram_size);
    if (!cpu.ram) return 2;
    cpu.pc = 0x8C400000u;
    cpu.pr = 0x8C010010u;
    for (int i = 0; i < 32; i++) cpu.ram[0x400000u + i] = (unsigned char)(i + 1);
    phantom_unresolved(&cpu);
    phantom_unresolved(&cpu);
    cpu.ram[0x400000u] ^= 0x80;
    phantom_unresolved(&cpu);
    phantom_unresolved(&cpu);
    cpu.ram[0x400000u] ^= 0x80;
    phantom_unresolved(&cpu);
    free(cpu.ram);
    return 0;
}
