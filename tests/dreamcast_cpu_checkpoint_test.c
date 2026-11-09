#include "recompiler/sh4_checkpoint.h"
#include <assert.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>

int main(void) {
    SH4CPU cpu = {0};
    cpu.ram_size = DC_RAM_SIZE_16MB;
    cpu.ram_mask = DC_RAM_MASK_16MB;
    cpu.ram = calloc(1, cpu.ram_size);
    cpu.vram = calloc(1, DC_VRAM_SIZE);
    cpu.aica_ram = calloc(1, DC_AICA_SIZE);
    assert(cpu.ram && cpu.vram && cpu.aica_ram);
    uint8_t *ram=cpu.ram, *vram=cpu.vram, *aica=cpu.aica_ram;
    cpu.r[3]=0x12345678; cpu.pc=0x8c012345; cpu.cycles=987654321;
    cpu.fr[4]=1.5f; cpu.utlb_data2[13]=0x76543210; cpu.running=1;
    ram[19]=42; vram[53]=91; aica[21]=73;
    size_t size=sh4_checkpoint_size(&cpu);
    uint8_t *image=malloc(size);
    assert(image && size);
    assert(!sh4_checkpoint_capture(&cpu, image, size-1));
    assert(sh4_checkpoint_capture(&cpu, image, size));
    cpu.r[3]=0; cpu.pc=0; cpu.cycles=0; cpu.fr[4]=0; cpu.utlb_data2[13]=0;
    ram[19]=1; vram[53]=2; aica[21]=3;
    assert(!sh4_checkpoint_restore(&cpu, image, size-1));
    image[size-1] ^= 1;
    assert(!sh4_checkpoint_restore(&cpu, image, size));
    assert(cpu.pc==0 && ram[19]==1 && vram[53]==2 && aica[21]==3);
    image[size-1] ^= 1;
    assert(sh4_checkpoint_restore(&cpu, image, size));
    assert(cpu.ram==ram && cpu.vram==vram && cpu.aica_ram==aica);
    assert(cpu.r[3]==0x12345678 && cpu.pc==0x8c012345 && cpu.cycles==987654321);
    assert(cpu.fr[4]==1.5f && cpu.utlb_data2[13]==0x76543210 && cpu.running);
    assert(ram[19]==42 && vram[53]==91 && aica[21]==73);
    free(image); free(ram); free(vram); free(aica);
    puts("CPU checkpoint round-trip and corruption rejection passed");
    return 0;
}
