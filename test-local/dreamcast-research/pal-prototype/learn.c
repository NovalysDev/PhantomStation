/* Learning support for discs without a hand-written profile.
 *
 * A recompiled game only knows the code that was on the disc's boot program.
 * Anything it loads or unpacks into RAM later has no native translation, and
 * a call to it used to be skipped silently - the usual reason a game sits on
 * the launcher's background forever. With PHANTOM_DC_LEARN_DIR set, the first
 * call to each such address is written down together with a copy of the RAM
 * around it, so the compiler can translate exactly what the game ran and build
 * again. The launcher sets it during normal play too, so what a session meets
 * is picked up by the next compilation of the disc.
 * Learned code is only called back when RAM still holds the same bytes:
 * several programs can be loaded at one address over a game's life. */
#include "recompiler/sh4_cpu.h"
#include <direct.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define LEARN_MAX_TARGETS 512
#define LEARN_MAX_DUMPS   48
#define LEARN_SIGNATURE_BYTES 32u
#define GD_RING           256
#define WINDOW_BEFORE     0x20000u
#define WINDOW_AFTER      0x40000u

static uint32_t g_boot_start = 0x0c010000u, g_boot_end = 0x0c010000u;
static uint32_t g_gd_dest[GD_RING], g_gd_bytes[GD_RING];
static unsigned g_gd_n;
typedef struct {
    uint32_t address;
    uint8_t length;
    uint8_t bytes[LEARN_SIGNATURE_BYTES];
} LearnedSeen;
static LearnedSeen g_seen[LEARN_MAX_TARGETS];
static unsigned g_seen_n, g_dumps;

void phantom_learn_set_boot(uint32_t size) { g_boot_end = g_boot_start + size; }

void phantom_learn_note_gd(uint32_t dest, uint32_t bytes) {
    g_gd_dest[g_gd_n % GD_RING] = dest & 0x1fffffffu;
    g_gd_bytes[g_gd_n % GD_RING] = bytes;
    g_gd_n++;
}

static const char *learn_dir(void) {
    static const char *dir; static int checked;
    if (!checked) { checked = 1; dir = getenv("PHANTOM_DC_LEARN_DIR"); if (dir && !*dir) dir = NULL; }
    return dir;
}

/* The program a load put there, when the disc read that covers the address is
 * still in the recent history; otherwise a window of RAM around it, for code
 * the game unpacked or copied after reading. */
static int block_for(uint32_t phys, uint32_t *start, uint32_t *size) {
    for (unsigned i = 0; i < GD_RING && i < g_gd_n; i++) {
        unsigned k = (g_gd_n - 1 - i) % GD_RING;
        uint32_t d = g_gd_dest[k], n = g_gd_bytes[k];
        if (n && d <= phys && phys < d + n && n <= 0x400000u) {
            /* Overlays are often read in several pieces; extend over the
             * neighbouring reads that continue this one. */
            uint32_t lo = d, hi = d + n;
            for (int grown = 1; grown; ) {
                grown = 0;
                for (unsigned j = 0; j < GD_RING && j < g_gd_n; j++) {
                    uint32_t d2 = g_gd_dest[j], e2 = d2 + g_gd_bytes[j];
                    if (d2 < lo && e2 >= lo && lo - d2 <= 0x400000u) { lo = d2; grown = 1; }
                    if (e2 > hi && d2 <= hi && e2 - hi <= 0x400000u) { hi = e2; grown = 1; }
                }
            }
            *start = lo; *size = hi - lo; return 1;
        }
    }
    uint32_t lo = phys > WINDOW_BEFORE ? phys - WINDOW_BEFORE : 0, hi = phys + WINDOW_AFTER;
    if (phys >= g_boot_end && lo < g_boot_end) lo = g_boot_end;
    /* Code the game copied under the boot program (its exception vectors
     * live there) stops where the boot program starts. */
    if (phys < g_boot_start && hi > g_boot_start) hi = g_boot_start;
    *start = lo; *size = hi - lo;
    return 0;
}

void phantom_unresolved(SH4CPU *cpu) {
    if (cpu->pc == 0 && getenv("PHANTOM_DC_NULL_DIAG")) {
        static int n;
        if (n++ < 12) {
            fprintf(stderr, "[NULL] pr=%08X", cpu->pr);
            for (int i = 0; i < 16; i++) fprintf(stderr, " r%d=%08X", i, cpu->r[i]);
            fprintf(stderr, "\n");
        }
    }
    const char *dir = learn_dir();
    if (!dir) return;
    uint32_t target = cpu->pc, phys = target & 0x1fffffffu;
    if (phys < 0x0c008000u || phys >= 0x0c000000u + cpu->ram_size || (phys & 1)) return;
    if (!cpu->ram) return;
    uint32_t offset = phys - 0x0c000000u;
    uint32_t length = cpu->ram_size - offset;
    if (length > LEARN_SIGNATURE_BYTES) length = LEARN_SIGNATURE_BYTES;
    /* Different overlays may occupy the same address in a single session.
     * Deduplicate by address AND live bytes, not by address alone. */
    for (unsigned i = 0; i < g_seen_n; i++)
        if (g_seen[i].address == phys && g_seen[i].length == length &&
            !memcmp(g_seen[i].bytes, cpu->ram + offset, length)) return;
    if (g_seen_n >= LEARN_MAX_TARGETS) return;
    g_seen[g_seen_n].address = phys;
    g_seen[g_seen_n].length = (uint8_t)length;
    memcpy(g_seen[g_seen_n].bytes, cpu->ram + offset, length);
    g_seen_n++;

    char path[1024];
    /* Created on first use, so a session that met no unknown code leaves
     * nothing behind. */
    snprintf(path, sizeof path, "%s", dir);
    for (char *p = path + 3; *p; p++)
        if (*p == '\\' || *p == '/') { char c = *p; *p = 0; _mkdir(path); *p = c; }
    _mkdir(path);
    snprintf(path, sizeof path, "%s/appels.txt", dir);
    FILE *list = fopen(path, "a");
    if (!list) return;
    if (phys >= g_boot_start && phys < g_boot_end) {
        fprintf(list, "principal 0x%08X 0x%08X\n", phys | 0x80000000u, cpu->pr);
    } else if (g_dumps < LEARN_MAX_DUMPS) {
        uint32_t start, size;
        int from_disc = block_for(phys, &start, &size);
        uint32_t v = cpu->vbr & 0x1fffffffu;
        int vector = v && (phys == v + 0x100u || phys == v + 0x400u || phys == v + 0x600u);
        uint32_t ram_end = 0x0c000000u + cpu->ram_size;
        if (start < 0x0c008000u) { size -= 0x0c008000u - start; start = 0x0c008000u; }
        if (start + size > ram_end) size = ram_end - start;
        snprintf(path, sizeof path, "%s/bloc-%03u.bin", dir, g_dumps);
        FILE *dump = fopen(path, "wb");
        if (dump) {
            fwrite(cpu->ram + (start - 0x0c000000u), 1, size, dump);
            fclose(dump);
            fprintf(list, "bloc 0x%08X 0x%08X 0x%08X 0x%08X bloc-%03u.bin %s\n",
                    phys | 0x80000000u, cpu->pr, start | 0x80000000u, size, g_dumps,
                    from_disc ? "disque" : vector ? "vecteur" : "fenetre");
            g_dumps++;
        }
    }
    fclose(list);
}
