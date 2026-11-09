#include "recompiler/sh4_checkpoint.h"
#include <stddef.h>
#include <string.h>

/* Deliberately private to a matching build. A complete file format must also
 * identify the game, generated code and versions of every device section. */
typedef struct {
    uint8_t magic[8];
    uint32_t version;
    uint32_t cpu_size;
    uint32_t ram_size;
    uint32_t checksum;
} CheckpointHeader;

static const uint8_t magic[8] = {'P','H','D','C','C','P','U',0};

static int valid_cpu(const SH4CPU *cpu) {
    return cpu && cpu->ram && cpu->vram && cpu->aica_ram &&
        (cpu->ram_size == DC_RAM_SIZE_16MB || cpu->ram_size == DC_RAM_SIZE_32MB) &&
        cpu->ram_mask == cpu->ram_size - 1u;
}

static uint32_t checksum(const uint8_t *data, size_t size) {
    uint32_t crc = 0xffffffffu;
    while (size--) {
        crc ^= *data++;
        for (int i = 0; i < 8; ++i)
            crc = (crc >> 1) ^ (0xedb88320u & (uint32_t)-(int32_t)(crc & 1u));
    }
    return ~crc;
}

/* Construct a zero-initialized wire image: never serialize host pointers,
 * padding bytes, or private data accidentally present in an SH4CPU. */
static void copy_registers(SH4CPU *dst, const SH4CPU *src) {
    memset(dst, 0, sizeof(*dst));
    memcpy(dst->r, src->r, sizeof(dst->r));
    memcpy(dst->r_bank, src->r_bank, sizeof(dst->r_bank));
    dst->sr=src->sr; dst->gbr=src->gbr; dst->vbr=src->vbr;
    dst->ssr=src->ssr; dst->spc=src->spc; dst->sgr=src->sgr; dst->dbr=src->dbr;
    dst->mach=src->mach; dst->macl=src->macl; dst->pr=src->pr; dst->pc=src->pc;
    memcpy(dst->fr, src->fr, sizeof(dst->fr));
    memcpy(dst->xf, src->xf, sizeof(dst->xf));
    dst->fpscr=src->fpscr; dst->fpul=src->fpul;
    dst->ram_size=src->ram_size; dst->ram_mask=src->ram_mask;
    memcpy(dst->sq, src->sq, sizeof(dst->sq));
    memcpy(dst->qacr, src->qacr, sizeof(dst->qacr));
    memcpy(dst->dmac_regs, src->dmac_regs, sizeof(dst->dmac_regs));
    memcpy(dst->tmu_regs, src->tmu_regs, sizeof(dst->tmu_regs));
    dst->mmucr=src->mmucr;
    memcpy(dst->utlb_addr, src->utlb_addr, sizeof(dst->utlb_addr));
    memcpy(dst->utlb_data1, src->utlb_data1, sizeof(dst->utlb_data1));
    memcpy(dst->utlb_data2, src->utlb_data2, sizeof(dst->utlb_data2));
    dst->running=src->running; dst->cycles=src->cycles; dst->delay_slot=src->delay_slot;
}

size_t sh4_checkpoint_size(const SH4CPU *cpu) {
    if (!valid_cpu(cpu)) return 0;
    return sizeof(CheckpointHeader) + sizeof(SH4CPU) + (size_t)cpu->ram_size + DC_VRAM_SIZE + DC_AICA_SIZE;
}

int sh4_checkpoint_capture(const SH4CPU *cpu, void *buffer, size_t capacity) {
    size_t size = sh4_checkpoint_size(cpu);
    if (!size || !buffer || capacity < size) return 0;
    uint8_t *bytes = (uint8_t *)buffer;
    CheckpointHeader header = {{0}, 1, sizeof(SH4CPU), cpu->ram_size, 0};
    SH4CPU wire;
    copy_registers(&wire, cpu);
    memcpy(header.magic, magic, sizeof(magic));
    size_t p = sizeof(header);
    memcpy(bytes+p, &wire, sizeof(wire)); p += sizeof(wire);
    memcpy(bytes+p, cpu->ram, cpu->ram_size); p += cpu->ram_size;
    memcpy(bytes+p, cpu->vram, DC_VRAM_SIZE); p += DC_VRAM_SIZE;
    memcpy(bytes+p, cpu->aica_ram, DC_AICA_SIZE);
    header.checksum = checksum(bytes+sizeof(header), size-sizeof(header));
    memcpy(bytes, &header, sizeof(header));
    return 1;
}

int sh4_checkpoint_restore(SH4CPU *cpu, const void *buffer, size_t size) {
    if (!valid_cpu(cpu) || !buffer || size != sh4_checkpoint_size(cpu)) return 0;
    const uint8_t *bytes = (const uint8_t *)buffer;
    CheckpointHeader header;
    SH4CPU wire;
    memcpy(&header, bytes, sizeof(header));
    if (memcmp(header.magic, magic, sizeof(magic)) || header.version != 1 ||
        header.cpu_size != sizeof(SH4CPU) || header.ram_size != cpu->ram_size ||
        header.checksum != checksum(bytes+sizeof(header), size-sizeof(header))) return 0;
    size_t p = sizeof(header);
    memcpy(&wire, bytes+p, sizeof(wire)); p += sizeof(wire);
    if (wire.ram || wire.vram || wire.aica_ram || wire.ram_size != cpu->ram_size ||
        wire.ram_mask != cpu->ram_mask) return 0;
    uint8_t *ram=cpu->ram, *vram=cpu->vram, *aica=cpu->aica_ram;
    copy_registers(cpu, &wire);
    cpu->ram=ram; cpu->vram=vram; cpu->aica_ram=aica;
    memcpy(ram, bytes+p, cpu->ram_size); p += cpu->ram_size;
    memcpy(vram, bytes+p, DC_VRAM_SIZE); p += DC_VRAM_SIZE;
    memcpy(aica, bytes+p, DC_AICA_SIZE);
    return 1;
}
