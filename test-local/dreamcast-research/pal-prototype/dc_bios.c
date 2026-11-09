/**
 * Dreamcast BIOS syscall emulation. See dc_bios.h.
 *
 * Deliberately minimal: each syscall returns what a healthy machine with
 * default settings would, and anything unrecognised is logged and failed
 * rather than silently returning success. A game that gets a plausible answer
 * carries on; one that gets silence stalls, which is the failure we are here
 * to remove.
 */

#include "hal/dc_bios.h"
#include "hal/dc_hardware.h"
#include "hal/dc_ralentis.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

void sh4_call_indirect(SH4CPU *cpu);

void phantom_learn_note_gd(uint32_t dest, uint32_t bytes);   /* learn.c */

/* Research-only: serve GD-ROM sectors directly from the user's CHD. */
int pal_chd_sector(uint32_t lba, unsigned char out[2048]);

/* 128KB, the size of a Dreamcast's flash. Erased flash reads as 0xFF; starting
 * from zeros makes a game think there is a corrupt block rather than a blank
 * one. Writes land here so a game can read back what it just wrote - a settings
 * partition gets formatted and re-read during boot, and dropping the write
 * turns that into an endless reformat loop. */
#define FLASH_SIZE 0x20000
static uint8_t g_flash_store[FLASH_SIZE];
static int g_flash_ready = 0;

/* Les partitions de la mémoire flash d'une console du commerce (le BIOS les
 * annonce ainsi) : 0 = réglages d'usine, 1 = réservée, 2 = réglages de
 * l'utilisateur (langue, son), 3 = réglages des jeux, 4 = libre. */
static const struct { uint32_t offset, size; } FLASH_PARTS[] = {
    { 0x1A000, 0x02000 },   /* 0 : usine */
    { 0x18000, 0x02000 },   /* 1 : réservée */
    { 0x1C000, 0x04000 },   /* 2 : réglages de l'utilisateur */
    { 0x10000, 0x08000 },   /* 3 : réglages des jeux */
    { 0x00000, 0x10000 },   /* 4 : libre */
};
#define FLASH_PART_COUNT ((int)(sizeof FLASH_PARTS / sizeof FLASH_PARTS[0]))

/* Langue de la console, comme celle choisie dans le menu de la Dreamcast :
 * 0 japonais, 1 anglais, 2 allemand, 3 français, 4 espagnol, 5 italien.
 * PHANTOM_DC_LANGUE=fr/en/de/es/it/ja l'impose ; sinon la langue de
 * PhantomStation (language.txt), et le français à défaut. */
int dc_bios_console_language(void) {
    static const char *codes[] = { "ja", "en", "de", "fr", "es", "it" };
    char texte[8] = { 0 };
    const char *e = getenv("PHANTOM_DC_LANGUE");
    if (e) snprintf(texte, sizeof texte, "%s", e);
    else {
        const char *local = getenv("LOCALAPPDATA");
        char chemin[1024];
        if (local) {
            snprintf(chemin, sizeof chemin, "%s\\PhantomStation\\language.txt", local);
            FILE *f = fopen(chemin, "rb");
            if (f) { size_t n = fread(texte, 1, sizeof texte - 1, f); texte[n] = 0; fclose(f); }
        }
    }
    for (int i = 0; i < 6; ++i)
        if (texte[0] == codes[i][0] && texte[1] == codes[i][1]) return i;
    return 3;
}

/* Somme de contrôle d'un bloc de 64 octets de la flash (sur les 62 premiers). */
static uint16_t flash_crc(const uint8_t *b) {
    unsigned n = 0xFFFF;
    for (int i = 0; i < 62; ++i) {
        n ^= (unsigned)b[i] << 8;
        for (int c = 0; c < 8; ++c) n = (n & 0x8000) ? (n << 1) ^ 0x1021 : n << 1;
    }
    return (uint16_t)(~n & 0xFFFF);
}

/* Une flash vierge n'a aucun réglage : un jeu européen prend alors l'anglais.
 * Une vraie console a, dans la partition 2, le bloc « réglages système »
 * (numéro 5) que le menu de la Dreamcast écrit : c'est là que Grandia II et
 * House of the Dead 2 lisent la langue. La partition commence par l'en-tête
 * « KATANA_FLASH____ » et finit par une carte des blocs occupés (bit à 0). */
static void flash_format_settings(void) {
    memcpy(g_flash_store + 0x1A000, "00211", 5);          /* usine : Europe, PAL */
    uint8_t *part = g_flash_store + 0x1C000;
    memcpy(part, "KATANA_FLASH____", 16);
    part[16] = 2; part[17] = 0;
    uint8_t *blk = part + 64;                             /* bloc 1 */
    blk[0] = 0x05; blk[1] = 0x00;                         /* réglages système */
    memset(blk + 2, 0, 4);                                /* date du réglage */
    blk[6] = 0xFF;
    blk[7] = (uint8_t)dc_bios_console_language();
    blk[8] = 0;                                           /* stéréo */
    blk[9] = 0;                                           /* démarrage automatique */
    uint16_t crc = flash_crc(blk);
    blk[62] = (uint8_t)crc; blk[63] = (uint8_t)(crc >> 8);
    /* Carte des blocs : un bit par bloc qui suit l'en-tête (le bit 0x80 est le
     * bloc 1). Avec deux bits à 0, les jeux lisaient aussi le bloc 2, vide, et
     * le prenaient pour le réglage le plus récent : anglais. */
    part[0x4000 - 64] = 0x7F;                             /* seul le bloc 1 est occupé */
}

static void flash_init(void) {
    if (g_flash_ready)
        return;
    memset(g_flash_store, 0xFF, sizeof g_flash_store);
    flash_format_settings();
    g_flash_ready = 1;
}

void sh4_bios_set_flashrom(const void *data, uint32_t size) {
    flash_init();
    if (data && size)
        memcpy(g_flash_store, data, size < FLASH_SIZE ? size : FLASH_SIZE);
}

void sh4_bios_install_vectors(SH4CPU *cpu) {
    /* Each vector holds its own address, so a call through the table lands
     * somewhere sh4_bios_syscall() can identify. */
    for (uint32_t v = BIOS_VEC_FIRST; v <= BIOS_VEC_LAST; v += 4)
        sh4_write32(cpu, v, v);
    printf("[BIOS] syscall vectors installed (0x%08X-0x%08X)\n",
           BIOS_VEC_FIRST, BIOS_VEC_LAST);
}

static int syscall_flashrom(SH4CPU *cpu) {
    uint32_t sel = cpu->r[7];
    { static int on = -1; static int n;
      if (on < 0) on = getenv("PHANTOM_DC_FLASH_TRACE") != NULL;
      /* Relevé : ce que le jeu demande à la mémoire flash (langue, réglages). */
      if (on && n++ < 200)
          printf("[FLASH] appel %u : %08X %08X %08X\n", sel, cpu->r[4], cpu->r[5], cpu->r[6]); }
    switch (sel) {
    case 0: {   /* info(part, &offset, &size) -> 0 ok, -1 bad part */
        int part = (int)cpu->r[4];
        if (part < 0 || part >= FLASH_PART_COUNT)
            return -1;
        sh4_write32(cpu, cpu->r[5], FLASH_PARTS[part].offset);
        sh4_write32(cpu, cpu->r[5] + 4, FLASH_PARTS[part].size);
        return 0;
    }
    case 1: {   /* read(offset, dest, count) -> bytes read, or -1 */
        flash_init();
        uint32_t off = cpu->r[4], dest = cpu->r[5], count = cpu->r[6];
        for (uint32_t i = 0; i < count; i++) {
            uint8_t b = (off + i < FLASH_SIZE) ? g_flash_store[off + i] : 0xFF;
            sh4_write8(cpu, dest + i, b);
        }
        return (int)count;
    }
    case 2: {   /* write(offset, src, count) -> bytes written */
        flash_init();
        uint32_t off = cpu->r[4], src = cpu->r[5], count = cpu->r[6];
        for (uint32_t i = 0; i < count; i++) {
            if (off + i < FLASH_SIZE)
                g_flash_store[off + i] = sh4_read8(cpu, src + i);
        }
        return (int)count;
    }
    case 3: {   /* delete(offset): erase the partition containing it */
        flash_init();
        uint32_t off = cpu->r[4];
        for (int i = 0; i < FLASH_PART_COUNT; i++) {
            if (off >= FLASH_PARTS[i].offset &&
                off < FLASH_PARTS[i].offset + FLASH_PARTS[i].size) {
                memset(g_flash_store + FLASH_PARTS[i].offset, 0xFF,
                       FLASH_PARTS[i].size);
                break;
            }
        }
        return 0;
    }
    default:
        printf("[BIOS] flashrom: unknown selector %u\n", sel);
        return -1;
    }
}

static int syscall_sysinfo(SH4CPU *cpu) {
    switch (cpu->r[7]) {
    case 0:     /* init - copies the unit ID out of flash */
        return 0;
    case 2:     /* id() -> pointer to the 8-byte unit ID */
        return (int)0x8C000068u;
    case 3:     /* ID block used by kbCfgGetId */
        return (int)0x8C000068u;
    default:
        printf("[BIOS] sysinfo: unknown selector %u\n", cpu->r[7]);
        return -1;
    }
}

static int syscall_romfont(SH4CPU *cpu) {
    switch (cpu->r[7]) {
    case 0:     /* address of the ROM font. We have none; say so. */
        return 0;
    case 1:     /* lock */
        return 0;
    case 2:     /* unlock */
        return 0;
    default:
        return 0;
    }
}

/* ---- GD-ROM (GDC) ------------------------------------------------------
 *
 * Games reach the drive through vector 0x8C0000BC, with r7 selecting a call:
 *
 *   0 ReqCmd(cmd, param)     queue a command, returns a request id
 *   1 GetCmdStat(id, stat)   0 = done, 1 = still processing
 *   2 ExecServer()           let the drive make progress
 *   3 InitSystem()           reset the driver
 *   4 GetDrvStat(stat)       stat[0] = drive state, stat[1] = disc type
 *   5 G1DmaEnd(func, param)  from the G1 DMA interrupt: acknowledge it
 *
 * We complete commands synchronously inside ReqCmd - there is no drive to wait
 * for - and report "done" from the first GetCmdStat. Sector reads are served
 * from the disc's data track, which is what the game actually wants: its files
 * live at absolute LBAs on the disc, not in the extracted directory.
 */

#define GDC_CMD_PIOREAD   16
#define GDC_CMD_DMAREAD   17
#define GDC_CMD_GETTOC    18
#define GDC_CMD_GETTOC2   19
#define GDC_CMD_INIT      24
#define GDC_CMD_GETSES    35

#define GDC_SECTOR_SIZE   2048
#define GDC_RAW_SECTOR    2352
#define GDC_DATA_OFFSET   16      /* MODE1: sync + header before user data */

/* Sectors served since we last charged for them. A drive reads about 900 a
 * second. */
static unsigned gdc_sectors_read = 0;
#define GDC_SECTORS_PER_SECOND 900

#define GDC_MAX_TRACKS 24

static struct {
    FILE *f;
    uint32_t start_lba;
    uint32_t sectors;
} g_tracks[GDC_MAX_TRACKS];
static int g_track_count = 0;
static uint32_t g_disc_start_lba = 0;   /* first data track, for the TOC */
static uint32_t g_gdc_next_id = 1;
static uint32_t g_gdc_last_id = 0;
static uint32_t g_gdc_reported_id = 0xFFFFFFFFu;
/* Used only by the isolated House of the Dead 2 overlay experiment. */
static int g_test_house_overlay = 0;
int dc_test_house_overlay(void) { return g_test_house_overlay; }

void sh4_bios_set_gdrom_track(const char *path, uint32_t start_lba) {
    if (g_track_count >= GDC_MAX_TRACKS) {
        printf("[BIOS] gdrom: too many tracks, ignoring %s\n", path);
        return;
    }
    FILE *f = fopen(path, "rb");
    if (!f) {
        printf("[BIOS] gdrom: cannot open %s\n", path);
        return;
    }
    fseek(f, 0, SEEK_END);
    long size = ftell(f);
    fseek(f, 0, SEEK_SET);

    g_tracks[g_track_count].f = f;
    g_tracks[g_track_count].start_lba = start_lba;
    g_tracks[g_track_count].sectors = (uint32_t)(size / GDC_RAW_SECTOR);
    if (g_track_count == 0)
        g_disc_start_lba = start_lba;
    printf("[BIOS] gdrom: track %d = %s, LBA %u..%u\n",
           g_track_count, path, start_lba,
           start_lba + g_tracks[g_track_count].sectors - 1);
    g_track_count++;
}

/* Copy `count` 2048-byte sectors starting at absolute `lba` into guest memory. */
static int gdc_read_sectors_disque(SH4CPU *cpu, uint32_t lba, uint32_t count, uint32_t dest);
/* La lecture du disque est chronométrée pour la surveillance des ralentissements. */
static int gdc_read_sectors(SH4CPU *cpu, uint32_t lba, uint32_t count, uint32_t dest) {
    dc_ralentis_debut(RAL_DISQUE);
    int r = gdc_read_sectors_disque(cpu, lba, count, dest);
    dc_ralentis_fin(RAL_DISQUE);
    return r;
}

static int gdc_read_sectors_disque(SH4CPU *cpu, uint32_t lba, uint32_t count, uint32_t dest) {
    static uint8_t sector[GDC_RAW_SECTOR];
    for (uint32_t i = 0; i < count; i++) {
        memset(sector, 0, sizeof sector);
        gdc_sectors_read++;
        uint32_t want = lba + i;
        for (int t = 0; t < g_track_count; t++) {
            if (want < g_tracks[t].start_lba ||
                want >= g_tracks[t].start_lba + g_tracks[t].sectors)
                continue;
            long off = (long)(want - g_tracks[t].start_lba) * GDC_RAW_SECTOR;
            if (fseek(g_tracks[t].f, off, SEEK_SET) == 0)
                fread(sector, 1, GDC_RAW_SECTOR, g_tracks[t].f);
            break;
        }
        /* GD-ROM commands use FAD, while CHD track addresses use LBA. */
        if (want < 150 || pal_chd_sector(want - 150, sector + GDC_DATA_OFFSET) != 0) {
            fprintf(stderr, "[PAL-CHD] FAD %u unavailable\n", want);
            return -1;
        }
        const uint8_t *user = sector + GDC_DATA_OFFSET;
        uint32_t at = dest + i * GDC_SECTOR_SIZE;

        /* Straight into RAM when the destination is RAM, which it always is for
         * a disc transfer. Going through sh4_write8 a byte at a time made a
         * game that streams assets - this one issues 20k reads a minute -
         * spend all its time in the copy. */
        uint8_t *ram = sh4_get_ram_ptr();
        uint32_t phys = at & 0x1FFFFFFF;
        if (ram && phys >= DC_RAM_BASE &&
            phys + GDC_SECTOR_SIZE <= DC_RAM_BASE + sh4_get_ram_size()) {
            const char *watched = getenv("DCRECOMP_TEST_GD_WATCH");
            if (watched) {
                uint32_t wa=(uint32_t)strtoul(watched,NULL,0)&0x1fffffffu;
                if (phys <= wa && wa < phys + GDC_SECTOR_SIZE)
                    fprintf(stderr,"[TEST-GD-WATCH] fad=%u dest=%08X watch=%08X\n",want,at,wa);
            }
            memcpy(ram + (phys & sh4_get_ram_mask()), user, GDC_SECTOR_SIZE);
        } else {
            for (uint32_t b = 0; b < GDC_SECTOR_SIZE; b++)
                sh4_write8(cpu, at + b, user[b]);
        }
    }
    return 0;
}

static int gdc_exec(SH4CPU *cpu, uint32_t cmd, uint32_t param) {
    static int logged = 0;
    switch (cmd) {
    case GDC_CMD_PIOREAD:
    case GDC_CMD_DMAREAD: {
        /* param -> { lba, count, buffer, ... } */
        uint32_t lba   = sh4_read32(cpu, param);
        uint32_t count = sh4_read32(cpu, param + 4);
        uint32_t dest  = sh4_read32(cpu, param + 8);
        dc_rythme("lecture-disque");
        if (getenv("DCRECOMP_TEST_GD_TRACE"))
            fprintf(stderr, "[TEST-GD] command=%u fad=%u count=%u dest=%08X\n", cmd, lba, count, dest);
        { static unsigned long n = 0, sectors = 0;
          n++; sectors += count;
          if ((n % 200) == 0)
              printf("[BIOS] gdrom: %lu reads, %lu sectors (%lu KB)\n", n, sectors, sectors * 2); }
        if (logged < 8) {
            logged++;
            printf("[BIOS] gdrom read: lba %u x%u -> 0x%08X\n", lba, count, dest);
        }
        int r = gdc_read_sectors(cpu, lba, count, dest);
        if (!r) phantom_learn_note_gd(dest, count * GDC_SECTOR_SIZE);
        if (!r && getenv("DCRECOMP_TEST_HOUSE_OVERLAY2") && (dest & 0x1fffffffu) == 0x0c42e800u) {
            if (lba == 254523u) g_test_house_overlay = 1;
            if (lba == 254270u) g_test_house_overlay = 2;
            fprintf(stderr, "[TEST-OVERLAY] fad=%u active=%d\n", lba, g_test_house_overlay);
        }
        if (gdc_sectors_read) {
            sh4_credit_elapsed_ms(gdc_sectors_read * 1000u / GDC_SECTORS_PER_SECOND);
            gdc_sectors_read = 0;
        }
        return r;
    }
    case GDC_CMD_INIT:
        return 0;
    case GDC_CMD_GETTOC:
    case GDC_CMD_GETTOC2:
        /* A TOC the game can parse: one data track starting where ours does. */
        if (param) {
            uint32_t out = sh4_read32(cpu, param + 4);
            if (out) {
                for (int i = 0; i < 99; i++)
                    sh4_write32(cpu, out + i * 4, 0xFFFFFFFFu);
                sh4_write32(cpu, out + 0, 0x41000000u | g_disc_start_lba);
                sh4_write32(cpu, out + 99 * 4, 0x41000000u | g_disc_start_lba);
            }
        }
        return 0;
    default: {
        /* Once per command: the read log above used to use up the shared
         * count, and later commands went unreported. */
        static uint64_t seen;
        if (cmd < 64 && !(seen & (1ull << cmd))) {
            seen |= 1ull << cmd;
            printf("[BIOS] gdrom: unhandled command %u (param 0x%08X)\n", cmd, param);
        }
        return 0;
    }
    }
}

static int syscall_gdrom(SH4CPU *cpu) {
    switch (cpu->r[7]) {
    case 0: {   /* ReqCmd(cmd, param) -> request id, 0 on refusal */
        static int reqlog = 0;
        if (reqlog < 8) {
            reqlog++;
            printf("[BIOS] gdrom ReqCmd cmd=%u param=0x%08X\n", cpu->r[4], cpu->r[5]);
        }
        g_gdc_last_id = g_gdc_next_id++;
        uint32_t cmd = cpu->r[4];
        gdc_exec(cpu, cmd, cpu->r[5]);
        /* Tell the driver its command finished. It registered a completion
         * callback and is waiting on the drive's interrupt, so finishing
         * quietly leaves it waiting forever. */
        dc_gdrom_signal_command_end(sh4_get_hardware(), cmd == GDC_CMD_DMAREAD);
        return (int)g_gdc_last_id;
    }
    case 1: {   /* GetCmdStat(id, stat[4]).
                 * 0 NO_ACTIVE, 1 PROCESSING, 2 COMPLETED, 3 ABORTED. Callers
                 * spin while PROCESSING and treat anything but COMPLETED as a
                 * failure - returning 0 here made every command look like it
                 * had never been queued, so the game retried CMD_INIT forever. */
        if (cpu->r[5]) {
            for (int i = 0; i < 4; i++)
                sh4_write32(cpu, cpu->r[5] + i * 4, 0);
        }
        /* Report PROCESSING once, then COMPLETED. A driver written against a
         * real drive watches for the busy->done transition; handing it
         * COMPLETED on the first ask means that edge never happens, and the
         * ones that latch state on it just reissue the command forever. */
        if (cpu->r[4] != g_gdc_reported_id) {
            g_gdc_reported_id = cpu->r[4];
            return 1;   /* PROCESSING */
        }
        return 2;   /* COMPLETED */
    }
    case 2:     /* ExecServer - nothing to advance, we finish synchronously */
        return 0;
    case 3:     /* InitSystem */
        g_gdc_next_id = 1;
        return 0;
    case 5:     /* G1DmaEnd(callback, param), from the game's G1 DMA interrupt:
                 * the BIOS acknowledges the interrupt and runs the callback.
                 * Ignored, the bit stayed raised and the handler ran again on
                 * every poll. */
        dc_hw_write32(sh4_get_hardware(), 0x005F6900, 1u << 14);
        if (cpu->r[4]) { cpu->pc = cpu->r[4]; cpu->r[4] = cpu->r[5]; sh4_call_indirect(cpu); }
        return 0;
    case 4:     /* GetDrvStat(stat[2]): standby, and a GD-ROM in the drive */
        if (cpu->r[4]) {
            /* Drive status: 0 busy, 1 pause, 2 standby, 3 play, 4 seek,
             * 5 scan, 6 open, 7 no disc, 8 retry, 9 error.
             * Disc type: 0x00 CDDA, 0x10 CDROM, 0x20 XA, 0x30 CDI, 0x80 GDROM. */
            sh4_write32(cpu, cpu->r[4], 2);        /* standby: disc in, idle */
            sh4_write32(cpu, cpu->r[4] + 4, 0x80); /* GD-ROM */
        }
        return 0;
    default: {
        static unsigned seen;
        if (cpu->r[7] < 32 && !(seen & (1u << cpu->r[7]))) {
            seen |= 1u << cpu->r[7];
            printf("[BIOS] gdrom: unhandled function %u (r4=%08X r5=%08X)\n", cpu->r[7], cpu->r[4], cpu->r[5]);
        }
        return 0;
    }
    }
}

bool sh4_bios_syscall(SH4CPU *cpu) {
    uint32_t pc = cpu->pc & 0x1FFFFFFF;
    uint32_t vec = pc | 0x8C000000u;
    if (vec < BIOS_VEC_FIRST || vec > BIOS_VEC_LAST)
        return false;

    int result;
    switch (vec) {
    case BIOS_VEC_SYSINFO:  result = syscall_sysinfo(cpu);  break;
    case BIOS_VEC_ROMFONT:  result = syscall_romfont(cpu);  break;
    case BIOS_VEC_FLASHROM: result = syscall_flashrom(cpu); break;
    case BIOS_VEC_GDROM:    result = syscall_gdrom(cpu);    break;
    default:                return false;
    }

    cpu->r[0] = (uint32_t)result;
    /* The thunk reached us with `jmp`, so the return address is still in pr and
     * our C caller returns there naturally. */
    return true;
}
