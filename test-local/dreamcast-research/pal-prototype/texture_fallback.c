/* Opt-in research: host-backed overflow textures for Crazy Taxi PAL only. */
#include "recompiler/sh4_cpu.h"
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#define UNIT 32u
#define EXTRA DC_VRAM_SIZE
#define UNITS (EXTRA/UNIT)
#define MAX_SURFACES 4096u
static uint8_t used[UNITS];
static struct { uint32_t surface, first, count; } surfaces[MAX_SURFACES];

int pal_texture_free(SH4CPU *cpu, uint32_t surface) {
    (void)cpu;
    for (unsigned i=0;i<MAX_SURFACES;i++) if (surfaces[i].surface==surface && surface) {
        memset(used+surfaces[i].first,0,surfaces[i].count);
        surfaces[i].surface=0;
        return 1;
    }
    return 0;
}

int pal_texture_fallback(SH4CPU *cpu, uint32_t surface, uint32_t source) {
    if (!getenv("PHANTOM_DC_TEXTURE_RESERVE") || !cpu || !surface) return 0;
    /* A game-provided texture callback can replace the source; it must not
     * be skipped by this title-specific fallback. */
    if (sh4_read32(cpu,0x0c14a640u)) return 0;
    uint32_t phys=source&0x1fffffffu;
    uint32_t size=sh4_read32(cpu,surface+20u);
    if (phys<DC_RAM_BASE || phys>=DC_RAM_BASE+cpu->ram_size || !size || size>EXTRA) return 0;
    unsigned slot=0;
    while (slot<MAX_SURFACES && surfaces[slot].surface) ++slot;
    if (slot==MAX_SURFACES) return 0;
    uint32_t count=(size+UNIT-1u)/UNIT, start=0, run=0;
    for (uint32_t i=0;i<UNITS;i++) {
        if (used[i]) { run=0; continue; }
        if (!run) start=i;
        if (++run==count) break;
    }
    if (run<count) return 0;
    memset(used+start,1,count);
    surfaces[slot].surface=surface;surfaces[slot].first=start;surfaces[slot].count=count;
    uint32_t at=EXTRA+start*UNIT;
    for (uint32_t i=0;i<size;i++) cpu->vram[at+i]=cpu->ram[(phys+i)&cpu->ram_mask];
    sh4_write32(cpu,surface+28u,at);
    sh4_write32(cpu,surface+40u,0u);
    return 1;
}
