#include "n64_recomp.h"
#include "n64_sha256.h"
#include <stdio.h>
#include <string.h>

static void compiled(N64Cpu *cpu, uint32_t entry) { (void)cpu; (void)entry; }

int main(void)
{
    uint8_t rom[0x1000] = { 0x80, 0x37, 0x12, 0x40 };
    const uint8_t code[] = { 0x03, 0xE0, 0x00, 0x08, 0x24, 0x01, 0x00, 0x02 };
    char hash[65];
    N64Machine *m = n64_create();
    if (!m || !n64_load_rom(m, rom, sizeof rom)) return 1;
    n64_sha256_hex(rom, sizeof rom, hash);
    const N64RcEntry entry = { 0x00400FFCu, compiled, 0 };
    const N64RcBlock block = { 0x00020FFCu, sizeof code, code };
    const N64RcProgram program = { &entry, 1, &block, 1, sizeof rom, hash, "test", NULL };
    if (!n64_rc_install(m, &program)) return 2;
    memcpy(m->rdram + block.paddr, code, sizeof code);
    m->cpu.cp0_page_mask = 0;
    m->cpu.cp0_entry_hi = 0x00400000;
    m->cpu.cp0_entry_lo[0] = ((0x20000u >> 12) << 6) | 7u;
    m->cpu.cp0_entry_lo[1] = ((0x30000u >> 12) << 6) | 7u;
    m->cpu.cp0_index = 0;
    n64_cpu_tlbwi(&m->cpu);
    if (n64_rc_lookup(m, entry.vaddr) != NULL) {
        fprintf(stderr, "Accepted a block spanning noncontiguous TLB pages\n");
        return 3;
    }
    m->cpu.cp0_entry_lo[1] = ((0x21000u >> 12) << 6) | 7u;
    n64_cpu_tlbwi(&m->cpu);
    if (n64_rc_lookup(m, entry.vaddr) != compiled) {
        fprintf(stderr, "Rejected a block spanning contiguous TLB pages\n");
        return 4;
    }
    /* Repeat after the byte check was cached: a remap must still be checked. */
    m->cpu.cp0_entry_lo[1] = ((0x30000u >> 12) << 6) | 7u;
    n64_cpu_tlbwi(&m->cpu);
    if (n64_rc_lookup(m, entry.vaddr) != NULL) {
        fprintf(stderr, "Accepted a stale cached block after TLB remapping\n");
        return 5;
    }
    n64_destroy(m);
    puts("PASS: compiled blocks respect TLB page mappings and remapping");
    return 0;
}
