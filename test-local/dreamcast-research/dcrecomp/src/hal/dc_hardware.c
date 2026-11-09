/**
 * Dreamcast Hardware Abstraction Layer Implementation
 *
 * Maps Dreamcast hardware registers and subsystems to modern equivalents.
 * GPU rendering is handled via OpenGL, input via SDL2, sound via SDL2 audio.
 */

#include "hal/dc_hardware.h"
#include "hal/dc_ralentis.h"
#include "hal/arm7.h"
#include "hal/pvr2.h"
#include "hal/dc_vmu.h"
#include "hal/aica_pcm.h"
#include "hal/dc_cinematic.h"
#include "recompiler/sh4_cpu.h"
#include "platform/platform.h"
#include <stdlib.h>
#include <string.h>
#include <stdio.h>

/* Internal hardware state */
struct DCHardware {
    /* Hardware registers (mapped 0x005F6800 - 0x005FA000) */
    uint32_t hw_regs[0x3800 / 4];

    /* System Board state */
    uint32_t sb_istnrm;    /* Normal interrupt status */
    uint32_t sb_istext;    /* External interrupt status */
    uint32_t sb_isterr;    /* Error interrupt status */

    /* PVR state */
    uint32_t pvr_fb_addr1;
    uint32_t pvr_fb_addr2;
    uint32_t pvr_render_addr;
    bool pvr_rendering;
    int frame_count;

    /* Maple (controller) state */
    MapleController controllers[4];
    bool controller_connected[4];

    /* AICA state */
    bool aica_arm_running;

    /* GD-ROM state */
    int gdrom_status;

    /* TA (Tile Accelerator) FIFO buffer */
    uint32_t ta_fifo[32];
    int ta_fifo_pos;

    /* Timing */
    uint64_t vblank_count;
    uint64_t last_vblank_time;
};

/* Register offset calculation */
static inline uint32_t hw_reg_idx(uint32_t addr) {
    if (addr >= 0x005F6800 && addr < 0x005FA000)
        return (addr - 0x005F6800) / 4;
    return 0;
}

DCHardware* dc_hw_init(void) {
    DCHardware *hw = (DCHardware *)calloc(1, sizeof(DCHardware));
    if (!hw) return NULL;

    /* Set PVR ID (Holly chip) */
    hw->hw_regs[hw_reg_idx(PVR_ID)] = 0x17FD11DB;

    /* Default framebuffer setup (640x480) */
    hw->pvr_fb_addr1 = 0x00000000;
    hw->pvr_fb_addr2 = 0x00000000;

    /* Connect controller in port 0 */
    hw->controller_connected[0] = true;
    hw->controllers[0].buttons = 0xFFFF; /* All buttons released (active low) */
    hw->controllers[0].ltrig = 0;
    hw->controllers[0].rtrig = 0;
    hw->controllers[0].joyx = 0;
    hw->controllers[0].joyy = 0;

    printf("[HAL] Dreamcast hardware initialized\n");
    return hw;
}

void dc_hw_destroy(DCHardware *hw) {
    if (hw) {
        dc_vmu_close();
        printf("[HAL] Hardware destroyed (frames rendered: %d)\n", hw->frame_count);
        free(hw);
    }
}

/* Polling detector state */
static uint32_t poll_last_addr = 0;
static int poll_count = 0;
static int poll_reported = 0;

uint32_t dc_hw_read32(DCHardware *hw, uint32_t addr) {
    if (!hw) return 0;

    /* Strip P2 area bits if present */
    uint32_t phys = addr & 0x1FFFFFFF;

    /* Global read counter for heartbeat */
    static uint64_t hw_read_count = 0;
    hw_read_count++;
    if ((hw_read_count & 0xFFFFFFF) == 0) {
        printf("[HEARTBEAT] %llu HW reads, last=0x%08X\n",
               (unsigned long long)hw_read_count, phys);
    }

    /* Detect polling loops */
    if (phys == poll_last_addr) {
        poll_count++;
        if (poll_count == 10 && poll_reported < 50) {
            poll_reported++;
            printf("[POLL] register 0x%08X read 10+ times\n", phys);
        }
        if (poll_count == 1000 && poll_reported < 60) {
            poll_reported++;
            printf("[POLL] register 0x%08X read 1000+ times â€” likely stuck\n", phys);
        }
    } else {
        if (poll_count >= 10 && poll_reported < 60) {
            poll_reported++;
            printf("[POLL] register 0x%08X polled %d times, moving to 0x%08X\n",
                   poll_last_addr, poll_count, phys);
        }
        poll_last_addr = phys;
        poll_count = 1;
    }

    dc_g2_retire_finished(hw);

    /* PHANTOM_DC_HW_READ_STATS: the most-read registers, every two seconds. */
    { static int on = -1; static uint32_t addr[64], hits[64]; static uint64_t since;
      if (on < 0) on = getenv("PHANTOM_DC_HW_READ_STATS") != NULL;
      if (on) {
          unsigned k = (phys >> 2) & 63;
          if (addr[k] != phys) { addr[k] = phys; hits[k] = 0; }
          hits[k]++;
          uint64_t now = platform_get_ticks_ms();
          if (!since) since = now;
          if (now - since >= 2000) {
              printf("[HW-READS]");
              for (int i = 0; i < 64; i++) if (hits[i] > 1000) printf(" %08X:%u", addr[i], hits[i]);
              printf("\n"); memset(hits, 0, sizeof hits); since = now;
          } } }

    switch (phys) {
    case PVR_ID:
        return 0x17FD11DB;

    case PVR_REVISION:
        return 0x00000011;  /* Revision 1.1 */

    case 0x005F689C:
        /* SB_SBREV, the system bus revision: 0x0B on retail units. Kamui
         * picks its capabilities from it, and read as 0 (early hardware) it
         * keeps textures to one 4MB bank of VRAM: Crazy Taxi's town ran out
         * of room and drew white. */
        return 0x0000000B;

    case SB_ISTNRM:
        /* VBlank is raised by sh4_poll_irq, which owns the 60Hz clock.
         * Generating it here as well gave us two competing timebases. */
        return hw->sb_istnrm;

    case SB_ISTEXT:
        return hw->sb_istext;

    case SB_ISTERR:
        return hw->sb_isterr;

    case PVR_FB_ADDR1:
        return hw->pvr_fb_addr1;

    case PVR_FB_ADDR2:
        return hw->pvr_fb_addr2;

    case 0x005F688C: { /* SB_FFST - FIFO Status (always report empty/ready) */
        static int ffst_log = 0;
        if (ffst_log < 3) { ffst_log++; printf("[FFST] returning 0\n"); }
        return 0;
    }

    case 0x005F810C: { /* SPG_STATUS - sync pulse generator status */
        /* One line per read.
         *
         * Deriving the line from the millisecond clock looks more honest but
         * is not: the value then changes once per millisecond, so it steps by
         * about 31 lines at a time and a game waiting for one particular line
         * mostly steps straight over it. ChuChu Rocket's frame loop spent
         * 300,000 reads per frame that way.
         *
         * ponytail: the line count no longer measures time, so a game that
         * counts lines to time something will be wrong. None seen yet - they
         * wait for a line or for vblank, and both arrive promptly. Frame pace
         * still comes from the 60Hz VBlank interrupt, not from here. Anchor
         * this to a microsecond clock if a game ever needs the real rate. */
    uint64_t now = platform_get_ticks_ms();
        static uint32_t line = 0;
        line = (line + 1) % 525;
        uint32_t scanline = line;
        uint32_t vsync = (scanline >= 480) ? 1 : 0;  /* VSync during lines 480-524 */
        uint32_t blank = vsync;  /* Blank during VBlank */
        uint32_t fieldnum = (uint32_t)(now / 16) & 1; /* Alternate fields */
        uint32_t result = (scanline & 0x3FF) | (fieldnum << 10) | (blank << 11) | (vsync << 13);
        static int spg_log = 0;
        static uint32_t spg_last = 0;
        if (result != spg_last && spg_log < 20) {
            spg_log++;
            printf("[SPG] scanline=%u vsync=%u blank=%u field=%u val=0x%08X\n",
                   scanline, vsync, blank, fieldnum, result);
            spg_last = result;
        }
        return result;
    }

    default:
        if (phys >= 0x005F6800 && phys < 0x005FA000) {
            return hw->hw_regs[hw_reg_idx(phys)];
        }
        break;
    }

    return 0;
}


/* ---- G2 and PVR DMA ------------------------------------------------------
 *
 * Four G2 channels (AICA, Ext1, Ext2, Dev) and the PVR channel share a layout:
 * seven registers 4 bytes apart holding the device-side address, the root-bus
 * (system RAM) address, the length, direction, trigger select, enable and
 * start. Writing 1 to start runs the transfer; hardware clears it when done
 * and raises a bit in SB_ISTNRM. A game polls the start register to wait, so
 * the clear is the part that matters - a transfer we cannot place still has to
 * finish.
 *
 * ponytail: transfers complete inside the write. Real DMA overlaps with the
 * CPU, and a game that reads the destination before polling would see it early
 * - none do, because on hardware the data is not there yet either. If timing
 * ever matters, this is where a completion deadline would go.
 */
#define DMA_OFF_DEVICE 0x00   /* device-side address (AICA RAM, VRAM, TA) */
#define DMA_OFF_ROOT   0x04   /* system RAM address */
#define DMA_OFF_LEN    0x08
#define DMA_OFF_DIR    0x0C   /* 0 = root -> device, 1 = device -> root */
#define DMA_OFF_START  0x18

/* Where a DMA address points, as a pointer into the memory we own. Returns
 * NULL for anything unmapped, and clamps `len` to what actually fits. */
static uint8_t *dma_resolve(uint32_t addr, uint32_t *len, const char **what) {
    uint32_t phys = addr & 0x1FFFFFFF;

    if (phys >= 0x00800000 && phys < 0x00A00000) {
        uint8_t *aica = sh4_get_aica_ram_ptr();
        uint32_t off = phys - 0x00800000;
        if (!aica) return NULL;
        if (off + *len > DC_AICA_SIZE) *len = DC_AICA_SIZE - off;
        *what = "AICA";
        return aica + off;
    }
    /* 0x04000000 is VRAM as the CPU sees it, 0x11000000 the TA texture path;
     * both land in the same 8MB. The 32-bit area (0x05000000) interleaves the
     * banks, so it is not one block: run_dma copies it word by word. */
    uint32_t vram_off = 0xFFFFFFFF;
    if (phys >= 0x04000000 && phys < 0x04800000)      vram_off = phys - 0x04000000;
    else if (phys >= 0x11000000 && phys < 0x11800000) vram_off = phys - 0x11000000;
    if (vram_off != 0xFFFFFFFF) {
        uint8_t *vram = sh4_get_vram_ptr();
        if (!vram) return NULL;
        if (vram_off + *len > DC_VRAM_SIZE) *len = DC_VRAM_SIZE - vram_off;
        *what = "VRAM";
        return vram + vram_off;
    }
    if (phys >= DC_RAM_BASE && phys < DC_RAM_BASE + 0x01000000) {
        uint8_t *ram = sh4_get_ram_ptr();
        if (!ram) return NULL;
        *what = "RAM";
        return ram + ((phys - DC_RAM_BASE) & sh4_get_ram_mask());
    }
    return NULL;
}

/* A G2 channel that has copied its data but is not admitting to being done
 * yet. The wait is the point: a driver starts a channel and then records the
 * transfer, and clearing the start bit before it gets that far means the
 * transfer is never in its list to complete.
 *
 * The rate has to be roughly honest, because games time real work against it.
 * ChuChu Rocket locks the descriptor holding its sound driver while the upload
 * to sound RAM is in flight and only releases it from the completion
 * interrupt, then reuses that descriptor for its tone bank about three and a
 * half milliseconds later without waiting - so a transfer that pretends to
 * take longer than the hardware would is a transfer that breaks it. 4MB/s put
 * the 36KB driver upload at nearly nine milliseconds. G2 sustains well over
 * 20MB/s, which is under 1.5ms. This did not on its own fix that game, but
 * the old figure was simply wrong. */
#define G2_BYTES_PER_MS 25000

/* ...and the duration is counted in polls, not milliseconds.
 *
 * A millisecond of ours is not a millisecond of the machine's. The SH-4 side
 * is compiled, not emulated, and nothing paces it, so it covers far more game
 * work per wall-clock millisecond than the hardware would. A transfer told to
 * take one millisecond therefore finishes far later in *game* terms than it
 * ever did on a Dreamcast - and a game that starts a transfer and then does a
 * bounded amount of work before relying on it will lose that race every time.
 *
 * ChuChu Rocket does exactly that: the 36KB sound-driver upload holds its
 * descriptor locked, and the game reuses that descriptor for its tone bank
 * after a short, fixed amount of work. Measured, the completion was delivered
 * 0ms after being raised - delivery was never the problem - but the raise
 * itself waited a wall-clock millisecond, by which time the game had already
 * been past and failed.
 *
 * Polls are a better clock for this. One poll is 256 SH-4 memory accesses,
 * which on hardware is on the order of ten microseconds, so G2 moves roughly
 * this many bytes in one. It scales with what the game is doing rather than
 * with how fast the host happens to be. */
#define G2_BYTES_PER_POLL 250
static struct {
    uint32_t base;
    int      bit;
    uint64_t done_poll;
    int      busy;
} g_g2_busy[5];

static uint64_t g_g2_polls;   /* retire calls, the clock the busy period uses */

static void g2_start_busy(uint32_t base, int bit, uint32_t len) {
    /* DCRECOMP_G2_BPP overrides the rate, for bisecting a game that races its
     * own transfers. */
    static int bpp = -1;
    if (bpp < 0) { const char *e = getenv("DCRECOMP_G2_BPP");
                   bpp = e ? atoi(e) : G2_BYTES_PER_POLL;
                   if (bpp < 1) bpp = G2_BYTES_PER_POLL; }
    uint64_t polls = len / (uint32_t)bpp;
    if (polls < 1) polls = 1;
    for (int i = 0; i < 5; i++) {
        if (g_g2_busy[i].busy && g_g2_busy[i].base != base)
            continue;
        g_g2_busy[i].base = base;
        g_g2_busy[i].bit = bit;
        g_g2_busy[i].done_poll = g_g2_polls + polls;
        g_g2_busy[i].busy = 1;
        return;
    }
}

/* Called from the register read and from the interrupt poll: retire any
 * channel whose time is up, clear its start bit and raise its interrupt. */
/* Called from the tick gate in sh4_poll_irq, not from every poll - it reads
 * the clock, which is the expensive part. */
/* When the render started by the last STARTRENDER is due to finish (0: none
 * pending). PHANTOM_DC_RENDER_US sets how long a render takes, 5 ms by
 * default - a light scene on the real chip; 0 answers at once as before. */
static uint64_t g_render_done_us;
/* PHANTOM_DC_RYTHME=ms : pendant une seconde à partir de cet instant, note à
 * la microseconde les tops d'écran, les débuts et fins de dessin. */
void dc_rythme(const char *quoi) {
    static long debut = -2, duree = 1000; static uint64_t t0;
    if (debut == -2) { const char *e = getenv("PHANTOM_DC_RYTHME"), *d = getenv("PHANTOM_DC_RYTHME_MS");
                       debut = e ? atol(e) : -1; if (d) duree = atol(d); }
    if (debut < 0) return;
    uint64_t now = platform_get_ticks_us();
    if (!t0) t0 = now;
    uint64_t ms = (now - t0) / 1000u;
    if (ms >= (uint64_t)debut && ms < (uint64_t)(debut + duree))
        printf("[RYTHME] %.2f %s\n", (now - t0) / 1000.0, quoi);
}
static uint64_t render_time_us(void) {
    static long us = -1;
    if (us < 0) { const char *e = getenv("PHANTOM_DC_RENDER_US"); us = e ? atol(e) : 5000; if (us < 0) us = 0; }
    return (uint64_t)us;
}

void dc_g2_retire_finished(DCHardware *hw) {
    if (!hw) return;
    if (g_render_done_us && platform_get_ticks_us() >= g_render_done_us) {
        g_render_done_us = 0;
        hw->sb_istnrm |= 7u;   /* render done: video, ISP, TSP */
        dc_rythme("fin-dessin");
    }
    uint64_t now = platform_get_ticks_ms();
    g_g2_polls++;
    for (int i = 0; i < 5; i++) {
        if (!g_g2_busy[i].busy || g_g2_polls < g_g2_busy[i].done_poll)
            continue;
        g_g2_busy[i].busy = 0;
        hw->hw_regs[hw_reg_idx(g_g2_busy[i].base + DMA_OFF_START)] = 0;
        hw->hw_regs[hw_reg_idx(g_g2_busy[i].base + DMA_OFF_LEN)] = 0;
        hw->sb_istnrm |= (1u << g_g2_busy[i].bit);
    }
}

static void run_dma(DCHardware *hw, uint32_t base, int istnrm_bit,
                    const char *name) {
    uint32_t device = hw->hw_regs[hw_reg_idx(base + DMA_OFF_DEVICE)];
    uint32_t root   = hw->hw_regs[hw_reg_idx(base + DMA_OFF_ROOT)];
    uint32_t len    = hw->hw_regs[hw_reg_idx(base + DMA_OFF_LEN)] & 0x1FFFFFFF;
    uint32_t dir    = hw->hw_regs[hw_reg_idx(base + DMA_OFF_DIR)] & 1;

    static int dma_log = 0;
    int log = dma_log < 24;

    /* The TA FIFO is a register, not memory - feed it packet by packet. */
    if (!dir && (device & 0x1FFFFFFF) >= 0x10000000
             && (device & 0x1FFFFFFF) < 0x10800000) {
        uint32_t srclen = len;
        const char *sw = "?";
        uint8_t *src = dma_resolve(root, &srclen, &sw);
        int packets = 0;
        if (src) {
            for (uint32_t off = 0; off + 32 <= srclen; off += 32) {
                pvr2_ta_write((const uint32_t *)(src + off));
                packets++;
            }
        }
        if (log) {
            dma_log++;
            printf("[%s-DMA] %d packets (%u bytes) to TA FIFO\n",
                   name, packets, len);
        }
    } else if (((device & 0x1FFFFFFF) >= 0x05000000 && (device & 0x1FFFFFFF) < 0x05800000) ||
               ((root & 0x1FFFFFFF) >= 0x05000000 && (root & 0x1FFFFFFF) < 0x05800000)) {
        /* To or from VRAM's 32-bit area: the banks alternate every word. */
        int dev_side = (device & 0x1FFFFFFF) >= 0x05000000 && (device & 0x1FFFFFFF) < 0x05800000;
        uint32_t v = ((dev_side ? device : root) & 0x1FFFFFFF) - 0x05000000, olen = len;
        const char *ow = "?";
        uint8_t *op = dma_resolve(dev_side ? root : device, &olen, &ow), *vram = sh4_get_vram_ptr();
        int to_vram = dev_side ? !dir : dir;   /* dir 0: root to device */
        if (op && vram) {
            if (v + olen > DC_VRAM_SIZE) olen = v < DC_VRAM_SIZE ? DC_VRAM_SIZE - v : 0;
            for (uint32_t i = 0; i < olen; i++) {
                uint8_t *vp = vram + dc_vram32(v + i);
                if (to_vram) *vp = op[i]; else op[i] = *vp;
            }
        }
        if (log) {
            dma_log++;
            printf("[%s-DMA] %u bytes %s VRAM 32-bit +0x%06X (%s)\n", name, olen,
                   to_vram ? "to" : "from", v, ow);
        }
    } else {
        uint32_t copy = len;
        const char *dw = "?", *sw = "?";
        uint8_t *devp = dma_resolve(device, &copy, &dw);
        uint8_t *rootp = dma_resolve(root, &copy, &sw);
        if (devp && rootp && copy) {
            const char *watch = getenv("DCRECOMP_TEST_DMA_WATCH");
            if (watch) {
                uint32_t w = (uint32_t)strtoul(watch,NULL,0) & 0x1fffffffu;
                uint32_t dest = (dir ? root : device) & 0x1fffffffu;
                if (dest <= w && (uint64_t)dest + copy > w)
                    fprintf(stderr,"[TEST-DMA-WATCH] %s dest=%08X bytes=%u dir=%u\n",name,dest,copy,dir);
            }
            if (dir) memcpy(rootp, devp, copy);
            else     memcpy(devp, rootp, copy);
            if (log) {
                dma_log++;
                printf("[%s-DMA] %u bytes %s 0x%08X(%s) %s 0x%08X(%s)\n",
                       name, copy, dir ? "from" : "to", device, dw,
                       dir ? "to" : "from", root, sw);
            }
        } else if (log) {
            dma_log++;
            printf("[%s-DMA] unmapped: device 0x%08X dest %s, root 0x%08X %s, len %u\n",
                   name, device, devp ? "ok" : "no", root, rootp ? "ok" : "no", len);
        }
    }

    /* The data has moved, but the channel is still busy as far as the game
     * is concerned - see the note on g2_busy. */
    g2_start_busy(base, istnrm_bit, len);
}

/* ---- YUV converter ----
 * Movie players (Sofdec, and Grandia II's intro through it) write YUV420
 * macroblocks to 0x10800000, usually by channel-2 DMA. Holly turns them into
 * a YUV422 texture at TA_YUV_TEX_BASE and raises SB_ISTNRM bit 6 once the
 * picture TA_YUV_TEX_CTRL describes is complete; the player waits for that
 * before it decodes the next frame. Without it the data was dropped and the
 * movie stood still on a blank texture. */
#define TA_YUV_TEX_BASE 0x005F8148
#define TA_YUV_TEX_CTRL 0x005F814C
#define TA_YUV_TEX_CNT  0x005F8150
static struct { uint32_t dest, w, h, done; unsigned fill; uint8_t block[512]; } g_yuv;
static unsigned g_yuv_pictures;
static uint64_t g_yuv_last_picture_us;

#define YUV_MOVIE_GAP_US 300000u

static void yuv_init(DCHardware *hw) {
    uint32_t ctrl = hw->hw_regs[hw_reg_idx(TA_YUV_TEX_CTRL)];
    g_yuv.dest = hw->hw_regs[hw_reg_idx(TA_YUV_TEX_BASE)] & 0x00FFFFF8u;
    g_yuv.w = (ctrl & 0x3Fu) + 1; g_yuv.h = ((ctrl >> 8) & 0x3Fu) + 1;
    g_yuv.done = g_yuv.fill = 0;
    hw->hw_regs[hw_reg_idx(TA_YUV_TEX_CNT)] = 0;
}

/* One macroblock: U and V at half resolution (8x8, or 8x16 for YUV422
 * input), then Y as four 8x8 blocks, left to right and top to bottom. The
 * output is a pair of texels per U/V sample: U Y0 V Y1. */
static void yuv_block(DCHardware *hw) {
    uint32_t ctrl = hw->hw_regs[hw_reg_idx(TA_YUV_TEX_CTRL)];
    int in422 = (ctrl >> 24) & 1, separate = (ctrl >> 16) & 1;
    const uint8_t *u = g_yuv.block, *v = u + (in422 ? 128 : 64), *y = v + (in422 ? 128 : 64);
    uint32_t n = g_yuv.done, pitch, at;
    /* One texture of the whole picture, or one 16x16 texture per block. */
    if (separate) { pitch = 32; at = g_yuv.dest + n * 512u; }
    else { pitch = g_yuv.w * 32u; at = g_yuv.dest + (n / g_yuv.w) * 16u * pitch + (n % g_yuv.w) * 32u; }
    uint8_t *vram = sh4_get_vram_ptr();
    uint32_t mask = sh4_get_vram_capacity() - 1u;
    if (vram)
        for (unsigned py = 0; py < 16; py++)
            for (unsigned px = 0; px < 16; px += 2) {
                unsigned c = (in422 ? py : py / 2) * 8 + px / 2;
                unsigned q = (py >= 8) * 2 + (px >= 8), k = q * 64 + (py & 7) * 8 + (px & 7);
                uint32_t o = at + py * pitch + px * 2u;
                vram[o & mask] = u[c]; vram[(o + 1) & mask] = y[k];
                vram[(o + 2) & mask] = v[c]; vram[(o + 3) & mask] = y[k + 1];
            }        hw->hw_regs[hw_reg_idx(TA_YUV_TEX_CNT)] = ++g_yuv.done;
    if (g_yuv.done >= g_yuv.w * g_yuv.h) {
        uint64_t now_us = platform_get_ticks_us();
        if (!g_yuv_last_picture_us || now_us - g_yuv_last_picture_us > YUV_MOVIE_GAP_US)
            g_yuv_pictures = 0;
        g_yuv_last_picture_us = now_us;
        g_yuv_pictures++;
        { static unsigned n; static int trace = -1;
          if (trace < 0) trace = getenv("PHANTOM_DC_YUV_TRACE") != NULL;
          if (trace && n++ < 2000)
              printf("[YUV] picture %u t=%llums: %ux%u blocks at VRAM 0x%06X ctrl=%08X\n", n,
                     (unsigned long long)(platform_get_ticks_us() / 1000), g_yuv.w, g_yuv.h, g_yuv.dest, ctrl); }
        /* PHANTOM_DC_YUV_DUMP=prefix: pictures 200-215 as PPM, straight from
         * the converter's output. */
        { static unsigned n; const char *ramdump = getenv("PHANTOM_DC_YUV_RAMDUMP");
          if (ramdump && ++n == 300) {
              FILE *f = fopen(ramdump, "wb");
              if (f) { fwrite(sh4_get_ram_ptr(), 1, sh4_get_ram_size(), f); fclose(f); } } }
        { static unsigned n; const char *dump = getenv("PHANTOM_DC_YUV_DUMP");
          if (dump && vram && ++n >= 200 && n < 216 && !separate) {
              char name[1024]; snprintf(name, sizeof name, "%s-%03u.ppm", dump, n);
              FILE *f = fopen(name, "wb");
              if (f) {
                  unsigned w = g_yuv.w * 16, h = g_yuv.h * 16;
                  fprintf(f, "P6 %u %u 255\n", w, h);
                  for (unsigned py = 0; py < h; py++) for (unsigned px = 0; px < w; px++) {
                      uint32_t o = g_yuv.dest + py * pitch + (px & ~1u) * 2u;
                      int Y = vram[(o + 1 + (px & 1) * 2) & mask], U = vram[o & mask] - 128, V = vram[(o + 2) & mask] - 128;
                      int rgb[3] = { Y + V * 11 / 8, Y - U * 11 / 32 - V * 11 / 16, Y + U * 55 / 32 };
                      for (int c = 0; c < 3; c++) fputc(rgb[c] < 0 ? 0 : rgb[c] > 255 ? 255 : rgb[c], f);
                  }
                  fclose(f);
              } } }
        hw->sb_istnrm |= 1u << 6;
        yuv_init(hw);
    }
}

static void yuv_feed(DCHardware *hw, const uint8_t *src, uint32_t len) {
    unsigned size = ((hw->hw_regs[hw_reg_idx(TA_YUV_TEX_CTRL)] >> 24) & 1) ? 512 : 384;
    while (len) {
        unsigned take = size - g_yuv.fill;
        if (take > len) take = len;
        memcpy(g_yuv.block + g_yuv.fill, src, take);
        g_yuv.fill += take; src += take; len -= take;
        if (g_yuv.fill == size) { g_yuv.fill = 0; yuv_block(hw); }
    }
}

int dc_video_movie_active(void) {
    if (g_yuv_pictures < 2 || !g_yuv_last_picture_us)
        return 0;
    return platform_get_ticks_us() - g_yuv_last_picture_us <= YUV_MOVIE_GAP_US;
}

unsigned dc_video_movie_frames(void) {
    return g_yuv_pictures;
}

void dc_video_movie_size(int *width, int *height) {
    if (width) *width = (int)(g_yuv.w * 16u);
    if (height) *height = (int)(g_yuv.h * 16u);
}

static int hw_write_log = 0;

/* SH4 DMAC register indices (0xFFA00000 base, 4 bytes each) */
#define DMAC_SAR2_IDX  8   /* Source Address Register ch2 (0xFFA00020) */

void dc_hw_write32(DCHardware *hw, uint32_t addr, uint32_t val) {
    if (!hw) return;

    uint32_t phys = addr & 0x1FFFFFFF;

    if (hw_write_log < 100) {
        hw_write_log++;
        printf("[HW] write32 phys=0x%08X val=0x%08X\n", phys, val);
    }
    /* PHANTOM_DC_VIDEO_TRACE=1 : toute écriture du générateur de synchronisation
     * et de la sortie vidéo (0x005F80C0-0x005F80F4, 0x005F8044), changements
     * seulement. */
    { static int trace = -1; static uint32_t last[16];
      if (trace < 0) trace = getenv("PHANTOM_DC_VIDEO_TRACE") != NULL;
      if (trace && ((phys >= 0x005F80C0 && phys <= 0x005F80F4) || phys == 0x005F8044)) {
          unsigned i = phys == 0x005F8044 ? 15u : (phys - 0x005F80C0) / 4u;
          if (last[i] != val || !val) { last[i] = val; printf("[VIDEO] %08X = %08X\n", phys, val); } } }
    /* Always log DMA-related writes */
    if (phys >= 0x005F7C00 && phys <= 0x005F7C18) {
        printf("[PVR-DMA] write 0x%08X = 0x%08X\n", phys, val);
    }

    /* SB_MDST - Maple DMA start/status */
    if (phys == 0x005F6C18) {
        if (val & 1) {
            uint32_t mdstar = hw->hw_regs[hw_reg_idx(0x005F6C04)];
            dc_maple_dma(hw, mdstar);
            hw->hw_regs[hw_reg_idx(0x005F6C18)] = 0;
            hw->sb_istnrm |= (1 << 12); /* Maple DMA complete */
        }
        return;
    }

    switch (phys) {
    case PVR_SOFTRESET:
        if (val & 1) {
            printf("[PVR] Soft reset\n");
        }
        break;

    case PVR_STARTRENDER:
        dc_rythme("debut-dessin");
        hw->pvr_rendering = true;
        hw->frame_count++;
        if (getenv("DCRECOMP_TEST_FRAME_TRACE") && (hw->frame_count % 300) == 0) {
            int packets, vertices, polygons;
            pvr2_ta_get_stats(&packets, &vertices, &polygons);
            fprintf(stderr, "[TEST-FRAME] frame=%d packets=%d vertices=%d polygons=%d\n",
                    hw->frame_count, packets, vertices, polygons);
        }
        if (hw->frame_count <= 3) {
            int pkts, verts, polys;
            pvr2_ta_get_stats(&pkts, &verts, &polys);
            printf("[PVR] STARTRENDER frame %d: %d packets, %d verts, %d polys\n",
                   hw->frame_count, pkts, verts, polys);
        }
        int use_saved = getenv("DCRECOMP_TEST_TA_BUFFER") && pvr2_ta_test_select_saved(1);
        /* Render submitted geometry via OpenGL */
        { static int skip = -1; if (skip < 0) skip = getenv("DCRECOMP_NO_RENDER") ? 1 : 0;
          if (!skip) { dc_ralentis_debut(RAL_DESSIN); pvr2_render_frame(); dc_ralentis_fin(RAL_DESSIN); } }

        /* Take the picture before the swap, while the frame is still the back
         * buffer. */
        { static const char *shot = NULL, *series = NULL; static long want = 0; static long n = 0, every = 0;
          static int checked = 0;
          if (!checked) {
              checked = 1;
              shot = getenv("DCRECOMP_SCREENSHOT");
              const char *w = getenv("DCRECOMP_SCREENSHOT_FRAME");
              want = w ? atol(w) : 1;
              series = getenv("DCRECOMP_TEST_CAPTURE_PREFIX");
              const char *interval = getenv("DCRECOMP_TEST_CAPTURE_EVERY");
              every = interval ? atol(interval) : 0;
          }
          ++n;
          if (shot && n == want) {
              pvr2_screenshot(shot);
              shot = NULL;
          }
          if (series && every > 0 && n % every == 0) {
              char name[2048];
              int len = snprintf(name, sizeof(name), "%s-%06ld.ppm", series, n);
              if (len > 0 && (size_t)len < sizeof(name)) pvr2_screenshot(name);
          } }
        dc_ralentis_debut(RAL_AFFICHAGE); platform_swap_buffers(); dc_ralentis_fin(RAL_AFFICHAGE);
        dc_ralentis_image();
        if (use_saved) pvr2_ta_test_select_saved(0); else pvr2_ta_reset();
        /* Poll input events to keep window responsive */
        if (!platform_poll_events(hw)) {
            printf("[PLATFORM] Window closed by user; stopping game.\n");
            dc_vmu_close(); platform_shutdown();
            exit(0);
        }
        /* Signal render complete via interrupt */
        { static unsigned frames = 0; static uint64_t t0 = 0;
          uint64_t now = platform_get_ticks_ms();
          if (!t0) t0 = now;
          if (++frames % 60 == 0)
              printf("[FRAME] %u rendered, %.1f fps\n", frames,
                     frames * 1000.0 / (double)(now - t0 + 1)); }
        /* Render done (video, ISP, TSP) once the chip would have finished, not
         * as soon as it starts: we draw instantly, but a game waits on this
         * interrupt and gives the wait to other work - Grandia II's movie
         * decoder runs in exactly that time, and with an instant answer it
         * got one slice a frame and missed half its pictures. */
        if (g_render_done_us) hw->sb_istnrm |= 7u;   /* a render still pending finishes now */
        g_render_done_us = platform_get_ticks_us() + render_time_us();
        dc_rythme("dessin-calcule");
        hw->pvr_rendering = false;
        break;

    case PVR_FB_ADDR1:
        hw->pvr_fb_addr1 = val;
        break;

    case PVR_FB_ADDR2:
        hw->pvr_fb_addr2 = val;
        break;

    case PVR_FB_RENDER:
        hw->pvr_render_addr = val;
        break;

    case SB_ISTNRM:
        /* Writing 1 bits clears them */
        hw->sb_istnrm &= ~val;
        break;

    case SB_ISTEXT:
        hw->sb_istext &= ~val;
        break;

    case SB_ISTERR:
        hw->sb_isterr &= ~val;
        break;

    case AICA_ARM_RESET:
        hw->aica_arm_running = !(val & 1);
        printf("[AICA] ARM %s\n", hw->aica_arm_running ? "started" : "reset");
        break;

    case TA_LIST_CONT:
        hw->hw_regs[hw_reg_idx(phys)] = val;
        pvr2_ta_list_continue();
        break;

    case TA_LIST_INIT:
        { static int on = -1; if (on < 0) on = getenv("PHANTOM_DC_TA_EOL_TRACE") != NULL;
          if (on) printf("[TA-INIT] t=%llums frame=%d\n", (unsigned long long)platform_get_ticks_ms(), hw->frame_count); }
        if (getenv("DCRECOMP_TEST_TA_BUFFER")) pvr2_ta_test_save_list();
        hw->ta_fifo_pos = 0;
        pvr2_ta_reset();
        break;

    /* G2 channels: AICA, Ext1, Ext2, Dev - and the PVR channel. */
    case 0x005F7818: if (val & 1) run_dma(hw, 0x005F7800, 15, "AICA"); return;
    case 0x005F7838: if (val & 1) run_dma(hw, 0x005F7820, 16, "EXT1"); return;
    case 0x005F7858: if (val & 1) run_dma(hw, 0x005F7840, 17, "EXT2"); return;
    case 0x005F7878: if (val & 1) run_dma(hw, 0x005F7860, 18, "DEV");  return;

    case SB_C2DST:
        if (val & 1) {
            /* CH2-DMA: SH4 DMAC Channel 2 â†’ PVR (TA FIFO or VRAM)
             * Source: SH4 DMAC SAR2 register (system RAM address)
             * Dest:   SB_C2DSTAT (PVR address, default 0x10000000 = TA FIFO)
             * Length: SB_C2DLEN */
            uint32_t c2d_dest = hw->hw_regs[hw_reg_idx(SB_C2DSTAT)];
            uint32_t c2d_len  = hw->hw_regs[hw_reg_idx(SB_C2DLEN)];
            uint32_t c2d_src  = sh4_get_dmac_reg(DMAC_SAR2_IDX);
            uint32_t ram_mask = sh4_get_ram_mask();

            static int ch2_log = 0;
            { static int on = -1; if (on < 0) on = getenv("PHANTOM_DC_TA_EOL_TRACE") != NULL;
              uint8_t *r = sh4_get_ram_ptr();
              if (on && r && (c2d_dest & 0x1FFFFFFF) < 0x10800000)
                  printf("[CH2-TA] src=%08X len=%u dmatcr2=%08X chcr2=%08X pcw=%08X\n", c2d_src, c2d_len,
                         sh4_get_dmac_reg(10), sh4_get_dmac_reg(11),
                         *(uint32_t *)(r + ((c2d_src & 0x1FFFFFFF) & sh4_get_ram_mask()))); }
            if (ch2_log < 20) {
                ch2_log++;
                printf("[CH2-DMA] TRIGGER: src=0x%08X dest=0x%08X len=%u\n",
                       c2d_src, c2d_dest, c2d_len);
            }

            if (c2d_len > 0) {
                uint8_t *ram = sh4_get_ram_ptr();
                if (ram) {
                    uint32_t src_offset = (c2d_src & 0x1FFFFFFF) & ram_mask;
                    uint32_t dest_phys = c2d_dest & 0x1FFFFFFF;

                    if (dest_phys >= 0x10000000 && dest_phys < 0x10800000) {
                        /* DMA to TA FIFO â€” send as 32-byte packets */
                        int packets = 0;
                        for (uint32_t off = 0; off + 32 <= c2d_len; off += 32) {
                            const uint32_t *pkt = (const uint32_t *)(ram + ((src_offset + off) & ram_mask));
                            { static int on = -1, n; if (on < 0) on = getenv("PHANTOM_DC_TA_SOURCE") != NULL;
                              if (on && n < 60 && (pkt[0] >> 29) == 4 && ((pkt[3] >> 27) & 7) != 1 && (pkt[0] & 8)) {
                                  n++; printf("[TA-SRC] %08X: pcw=%08X isp=%08X tsp=%08X tcw=%08X\n",
                                              (c2d_src + off) | 0x80000000u, pkt[0], pkt[1], pkt[2], pkt[3]); } }
                            pvr2_ta_write(pkt);
                            packets++;
                        }
                        if (ch2_log <= 20) {
                            printf("[CH2-DMA] Sent %d packets (%u bytes) to TA FIFO\n",
                                   packets, c2d_len);
                        }
                    } else if (dest_phys >= 0x10800000 && dest_phys < 0x11000000) {
                        for (uint32_t fed = 0; fed < c2d_len;) {
                            uint32_t source = (src_offset + fed) & ram_mask;
                            uint32_t chunk = ram_mask + 1u - source;
                            if (chunk > c2d_len - fed) chunk = c2d_len - fed;
                            yuv_feed(hw, ram + source, chunk);
                            fed += chunk;
                        }
                    } else if (dest_phys >= 0x11000000 && dest_phys < 0x12000000) {
                        /* DMA to TA texture path â†’ VRAM write */
                        uint8_t *vram = sh4_get_vram_ptr();
                        if (vram) {
                            uint32_t vram_offset = dest_phys - 0x11000000;
                            uint32_t copy_len = c2d_len;
                            uint32_t capacity = sh4_get_vram_capacity();
                            if (vram_offset >= capacity) copy_len = 0;
                            else if (copy_len > capacity - vram_offset) copy_len = capacity - vram_offset;
                            /* RAM is mirrored: preserve wrapped DMA reads without
                             * walking off the allocated buffer. */
                            for (uint32_t copied=0; copied<copy_len;) {
                                uint32_t source=(src_offset+copied)&ram_mask;
                                uint32_t chunk=ram_mask+1u-source;
                                if (chunk>copy_len-copied) chunk=copy_len-copied;
                                memcpy(vram+vram_offset+copied,ram+source,chunk);
                                copied+=chunk;
                            }
                            if (ch2_log <= 20)
                                printf("[CH2-DMA] Texture upload: %u bytes to VRAM[0x%06X]\n",
                                       copy_len, vram_offset);
                        }
                    } else if (dest_phys >= DC_VRAM_BASE && dest_phys < DC_VRAM_BASE + DC_VRAM_SIZE) {
                        /* DMA to VRAM (direct) */
                        uint8_t *vram = sh4_get_vram_ptr();
                        if (vram) {
                            uint32_t vram_offset = dest_phys - DC_VRAM_BASE;
                            uint32_t copy_len = c2d_len;
                            if (vram_offset + copy_len > DC_VRAM_SIZE)
                                copy_len = DC_VRAM_SIZE - vram_offset;
                            memcpy(vram + vram_offset, ram + src_offset, copy_len);
                            if (ch2_log <= 20)
                                printf("[CH2-DMA] Direct VRAM: %u bytes to VRAM[0x%06X]\n",
                                       copy_len, vram_offset);
                        }
                    } else {
                        if (ch2_log <= 20)
                            printf("[CH2-DMA] Unknown dest 0x%08X (len=%u)\n",
                                   c2d_dest, c2d_len);
                    }
                }
            }

            /* Update SB_C2DSTAT to reflect end position */
            hw->hw_regs[hw_reg_idx(SB_C2DSTAT)] = c2d_dest + c2d_len;
            hw->hw_regs[hw_reg_idx(SB_C2DLEN)] = 0;
            hw->sb_istnrm |= (1 << 19); /* CH2-DMA complete interrupt */
            hw->hw_regs[hw_reg_idx(SB_C2DST)] = 0;
        }
        break;

    case SB_SDST:
        if (val & 1) {
            /* Sort-DMA: system RAM â†’ TA with sorting */
            uint32_t sd_tag  = hw->hw_regs[hw_reg_idx(SB_SDSTAG)];
            uint32_t sd_star = hw->hw_regs[hw_reg_idx(SB_SDSTAR)];
            uint32_t sd_len  = hw->hw_regs[hw_reg_idx(SB_SDLEN)];
            uint32_t sd_dir  = hw->hw_regs[hw_reg_idx(SB_SDDIR)];
            printf("[SORT-DMA] TRIGGER: tag=0x%08X star=0x%08X len=%u dir=%u\n",
                   sd_tag, sd_star, sd_len, sd_dir);
            if (sd_dir == 0 && sd_len > 0) {
                uint8_t *ram = sh4_get_ram_ptr();
                if (ram) {
                    /* Sort-DMA uses a table of pointers (sort table) to
                     * traverse linked list entries and send to TA in sorted order.
                     * For now, do a simple linear DMA from sd_star to TA. */
                    uint32_t ram_offset = sd_star & 0x00FFFFFF;
                    int packets = 0;
                    for (uint32_t off = 0; off + 32 <= sd_len; off += 32) {
                        const uint32_t *pkt = (const uint32_t *)(ram + ram_offset + off);
                        pvr2_ta_write(pkt);
                        packets++;
                    }
                    printf("[SORT-DMA] Sent %d packets (%u bytes) to TA\n",
                           packets, sd_len);
                }
            }
            hw->sb_istnrm |= (1 << 20); /* Sort-DMA complete */
            hw->hw_regs[hw_reg_idx(SB_SDST)] = 0;
        }
        break;

    /* SB_MDST handled above the switch via if-check */

    case SB_PDST:
        if (val & 1) {
            /* PVR DMA trigger - transfer data from system RAM to TA FIFO */
            uint32_t pvr_addr = hw->hw_regs[hw_reg_idx(SB_PDSTAP)];
            uint32_t sys_addr = hw->hw_regs[hw_reg_idx(SB_PDSTAR)];
            uint32_t length   = hw->hw_regs[hw_reg_idx(SB_PDLEN)];
            uint32_t dir      = hw->hw_regs[hw_reg_idx(SB_PDDIR)];
            printf("[PVR-DMA] TRIGGER: sys=0x%08X pvr=0x%08X len=%u dir=%u\n",
                   sys_addr, pvr_addr, length, dir);
            if (dir == 0 && length > 0) {
                /* DMA to PVR (TA FIFO or VRAM) */
                uint8_t *ram = sh4_get_ram_ptr();
                if (ram) {
                    /* System address needs P1/P2 strip */
                    uint32_t ram_offset = sys_addr & 0x00FFFFFF;
                    uint32_t bytes = length;
                    int packets = 0;
                    if (pvr_addr >= 0x10000000 && pvr_addr < 0x10800000) {
                        /* DMA to TA FIFO - send as 32-byte packets */
                        for (uint32_t off = 0; off + 32 <= bytes; off += 32) {
                            const uint32_t *pkt = (const uint32_t *)(ram + ram_offset + off);
                            pvr2_ta_write(pkt);
                            packets++;
                        }
                        printf("[PVR-DMA] Sent %d packets (%u bytes) to TA FIFO\n",
                               packets, bytes);
                    } else {
                        printf("[PVR-DMA] Non-TA destination 0x%08X (len=%u) - TODO\n",
                               pvr_addr, bytes);
                    }
                }
            }
            /* Signal DMA complete */
            hw->sb_istnrm |= (1 << 11); /* PVR-DMA complete */
            hw->hw_regs[hw_reg_idx(SB_PDST)] = 0; /* Clear start bit */
        }
        break;

    default:
        if (phys >= 0x005F6800 && phys < 0x005FA000) {
            hw->hw_regs[hw_reg_idx(phys)] = val;
            if (phys == TA_YUV_TEX_BASE || phys == TA_YUV_TEX_CTRL) {
                { static int n; if (getenv("PHANTOM_DC_YUV_TRACE") && n++ < 40)
                      printf("[YUV] %s = %08X (%u bytes pending)\n", phys == TA_YUV_TEX_BASE ? "base" : "ctrl", val, g_yuv.fill); }
                yuv_init(hw);
            }
        }
        if (phys >= 0x10800000 && phys < 0x11000000) {   /* store queues */
            yuv_feed(hw, (const uint8_t *)&val, 4);
            break;
        }
        /* TA FIFO writes (0x10000000 - 0x107FFFFF)
         * Direct word-by-word writes accumulate into 32-byte packets.
         * NOTE: Only process if TA list has been initialized (ta_fifo_pos >= 0).
         * Scattered writes to random offsets are likely VRAM/texture ops, not TA. */
        if (phys >= 0x10000000 && phys < 0x10800000) {
            static int ta_direct_count = 0;
            ta_direct_count++;
            if (ta_direct_count <= 5) {
                printf("[TA-DIRECT] word #%d to 0x%08X val=0x%08X (fifo_pos=%d)\n",
                       ta_direct_count, phys, val, hw->ta_fifo_pos);
            } else if (ta_direct_count == 1000) {
                printf("[TA-DIRECT] 1000 direct writes to TA range (suppressing)\n");
            }
            /* Accumulate into FIFO and dispatch 32-byte packets */
            hw->ta_fifo[hw->ta_fifo_pos & 7] = val;
            hw->ta_fifo_pos++;
            if ((hw->ta_fifo_pos & 7) == 0) {
                pvr2_ta_write(hw->ta_fifo);
            }
        }
        break;
    }
}

/* ========== PVR GPU ========== */

void dc_pvr_init(DCHardware *hw) {
    printf("[PVR] PowerVR2 initialized\n");
    hw->hw_regs[hw_reg_idx(0x005f811cu)] = 0xffu;
    hw->pvr_rendering = false;
    hw->frame_count = 0;
}

void dc_pvr_start_render(DCHardware *hw) {
    hw->pvr_rendering = true;
    hw->frame_count++;
}

void dc_pvr_submit_vertex(DCHardware *hw, const PVRVertex *vtx) {
    (void)hw;
    (void)vtx;
    /* TODO: Buffer vertex for rendering via OpenGL */
}

void dc_pvr_begin_list(DCHardware *hw, PVRListType type) {
    (void)hw;
    (void)type;
    /* TODO: Begin polygon list submission */
}

void dc_pvr_end_list(DCHardware *hw) {
    (void)hw;
    /* TODO: End polygon list */
}

/* ---- AICA registers ----------------------------------------------------
 *
 * 0x00700000-0x00707FFF. Mostly channel state the ARM7 owns; the one that
 * matters to us is 0x00702C00, whose bit 0 holds that processor in reset. */
#define AICA_REG_SIZE   0x8000
#define AICA_ARM_RESET_OFF  0x2C00

/* The AICA has an interrupt controller of its own. SCI* is the ARM's side and
 * MCI* the SH-4's, both using the same bit numbering. The driver reports a
 * command finished by raising a bit in MCIPD, which reaches the SH-4 as the G2
 * AICA interrupt - that reply is the thing a game sits waiting for. */
#define AICA_TIMER_A    0x2890
#define AICA_TIMER_B    0x2894
#define AICA_TIMER_C    0x2898
#define AICA_SCIEB      0x289C
#define AICA_SCIPD      0x28A0
#define AICA_SCIRE      0x28A4
#define AICA_MCIEB      0x28B4
#define AICA_MCIPD      0x28B8
#define AICA_MCIRE      0x28BC
#define AICA_SCILV0     0x28A8
#define AICA_SCILV1     0x28AC
#define AICA_SCILV2     0x28B0
#define AICA_INTREQ     0x2D00
#define AICA_INTCLEAR   0x2D04
#define AICA_INT_TIMER_A  6

static unsigned g_timer_a_fires;   /* Timer A overflows, ever */
static unsigned g_aica_keyons;   /* channels actually started, ever */
static uint32_t g_aica_regs[AICA_REG_SIZE / 4];
static ARM7     g_arm7;
static bool     g_arm7_wired;

static uint32_t *aica_reg(uint32_t off) {
    return &g_aica_regs[(off & (AICA_REG_SIZE - 1)) / 4];
}

/* Timer A counts the AICA's 44.1kHz sample clock - 44.1 samples per
 * millisecond - and it does that whether or not the processor beside it is
 * getting any work done. Driving it from the microsecond clock keeps the two
 * independent, which is how the hardware is: an earlier version advanced it
 * from instructions retired, and then a processor running at a fifth of its
 * clock got a timer running at a fifth of its rate.
 *
 * The millisecond clock is no good for this. On Windows it is GetTickCount64
 * and moves in 15.6ms steps, so a timer driven off it fires sixty-odd times a
 * second at best. ChuChu Rocket's driver programs Timer A for a thousand and
 * counts those interrupts to sequence its own initialisation.
 *
 * A period can be as short as 44 samples, so catching up across a long gap
 * means several overflows; the driver only sees one interrupt for them, which
 * is what the hardware would do too - SCIPD is a bit, not a count. */
static void aica_advance_timer_a(void) {
    static uint64_t last_us;
    static uint32_t frac_us;

    uint64_t now_us = platform_get_ticks_us();
    if (!last_us) { last_us = now_us; return; }

    uint64_t elapsed = now_us - last_us + frac_us;
    uint32_t samples = (uint32_t)(elapsed * 441 / 10000);   /* 44.1 per ms */
    if (!samples)
        return;
    frac_us = (uint32_t)(elapsed - (uint64_t)samples * 10000 / 441);
    last_us = now_us;

    uint32_t *ta = aica_reg(AICA_TIMER_A);
    uint32_t count = (*ta & 0xFF) + (samples >> ((*ta >> 8) & 7));
    if (count > 0xFF) {
        g_timer_a_fires++;
        *aica_reg(AICA_SCIPD) |= 1u << AICA_INT_TIMER_A;
        arm7_set_fiq(&g_arm7, (*aica_reg(AICA_SCIPD) & *aica_reg(AICA_SCIEB)) != 0);
        count &= 0xFF;
    }
    *ta = (*ta & ~0xFFu) | count;
}

/* FIQ tracks pending-and-enabled continuously: the driver clears SCIPD through
 * SCIRE and expects the line to drop with it. */
static void aica_refresh_fiq(void) {
    arm7_set_fiq(&g_arm7, (*aica_reg(AICA_SCIPD) & *aica_reg(AICA_SCIEB)) != 0);
}

#define AICA_INT_DMA 4

/* AICA internal DMA (registers 0x2880-0x288C). The ARM7 sound driver uses it
 * to move sample data from sound RAM to the channel registers and back. It is
 * separate from the SH-4's G2 DMA at 0x005F7800. Without it, a driver that
 * uploads a tone bank or copies a sample sees stale data, and the note it
 * plays is not the one it programmed. */
static void aica_internal_dma(void) {
    static int in_dma;
    uint32_t ctl, hi, lo, drga, len, ddir, dgate, addr, i;
    uint8_t *ram;
    static int traced = -1, traces;

    if (in_dma) return;
    ctl = *aica_reg(0x288C);
    if (!(ctl & 1u)) return;
    in_dma = 1;

    hi   = (*aica_reg(0x2880) >> 9) & 0x7Fu;
    lo   = (*aica_reg(0x2884) >> 2) & 0x3FFFu;
    drga = (*aica_reg(0x2888) >> 2) & 0x1FFFu;
    dgate= (*aica_reg(0x2888) >> 15) & 1u;
    ddir = (ctl >> 15) & 1u;
    len  = (ctl >> 2) & 0x1FFFu;
    addr = ((hi << 16) | (lo << 2)) & 0x1FFFFFu;
    ram  = sh4_get_aica_ram_ptr();

    if (ram && addr + len * 4u > 0x200000u)
        len = (0x200000u - addr) / 4u;
    if (ram && len) {
        if (dgate) {
            if (ddir)
                memset(ram + addr, 0, len * 4u);
            else
                for (i = 0; i < len; ++i)
                    dc_aica_reg_write((drga + i) * 4u, 0);
        } else if (ddir) {
            for (i = 0; i < len; ++i) {
                uint32_t r = drga + i;
                uint32_t v;
                if (r * 4u >= 0x8000u) break;
                v = dc_aica_reg_read(r * 4u);
                memcpy(ram + addr + i * 4u, &v, 4);
            }
        } else {
            for (i = 0; i < len; ++i) {
                uint32_t v;
                if ((drga + i) * 4u >= 0x8000u) break;
                memcpy(&v, ram + addr + i * 4u, 4);
                dc_aica_reg_write((drga + i) * 4u, v);
            }
        }
    }

    if (traced < 0) traced = getenv("PHANTOM_DC_AICA_DMA_TRACE") != NULL;
    if (traced && traces++ < 32)
        printf("[AICA-DMA] %s %s addr=%05X reg=%04X len=%u\n",
               dgate ? "gate" : "copy", ddir ? "regs->ram" : "ram->regs",
               addr, drga * 4u, len);

    *aica_reg(0x288C) &= ~1u;             /* DEXE is cleared when the DMA ends */
    *aica_reg(AICA_MCIPD) |= 1u << AICA_INT_DMA;
    *aica_reg(AICA_SCIPD) |= 1u << AICA_INT_DMA;
    aica_refresh_fiq();
    if ((*aica_reg(AICA_MCIPD) & *aica_reg(AICA_MCIEB)) != 0)
        dc_hw_signal_aica(sh4_get_hardware());
    in_dma = 0;
}

/* The three SCILV registers are bit-planes, not values: for pending interrupt
 * N, its priority level is bit N of each, low plane first. The driver's FIQ
 * handler reads that level out of INTRequest to decide what actually fired, so
 * a handler that always reads zero has nothing to dispatch on and spins. */
static uint32_t aica_int_level(void) {
    uint32_t pending = g_aica_regs[AICA_SCIPD / 4] & g_aica_regs[AICA_SCIEB / 4];
    if (!pending)
        return 0;
    int n = 0;
    while (!(pending & (1u << n)))
        n++;
    return ((g_aica_regs[AICA_SCILV0 / 4] >> n) & 1) |
           (((g_aica_regs[AICA_SCILV1 / 4] >> n) & 1) << 1) |
           (((g_aica_regs[AICA_SCILV2 / 4] >> n) & 1) << 2);
}

static int g_in_arm7;
int dc_aica_access_width = 32;

static void aica_status_trace(uint32_t off) {
    static int on = -1;
    static unsigned n;
    if (on < 0) on = getenv("PHANTOM_DC_AICA_KEYS") != NULL;
    if (on && (off == 0x2810 || off == 0x2811) && (n++ & 4095) == 0)
        printf("[AICA-ETAT] lecture #%u de l'etat de la voie %u par %s (%d bits, +%04X)\n", n,
               (g_aica_regs[0x280c / 4] >> 8) & 63u, g_in_arm7 ? "ARM" : "SH-4",
               dc_aica_access_width, off);
}

uint32_t dc_aica_reg_read(uint32_t off) {
    off &= AICA_REG_SIZE - 1;
    if (off == AICA_INTREQ)
        return aica_int_level();
    aica_status_trace(off);
    /* État de la voie choisie par MSLC : fin de boucle, étape et niveau de
     * l'enveloppe. Le pilote son de Grandia II le relit sans arrêt pour savoir
     * quelles voies sont libres ; il lisait jusqu'ici ce qu'on y avait écrit. */
    if ((off & ~3u) == 0x2810)
        return aica_pcm_status((g_aica_regs[0x280c / 4] >> 8) & 63u);
    if (off == 0x2814) {
        unsigned channel=(g_aica_regs[0x280c / 4] >> 8) & 63u;
        { static int on = -1; static unsigned n;
          if (on < 0) on = getenv("DCRECOMP_AICA_POS_TRACE") != NULL;
          if (on && (n++ & 1023) == 0)
              printf("[AICA] read #%u position ch%u = %04X (from %s)\n", n, channel, aica_pcm_position(channel),
                     g_in_arm7 ? "ARM" : "SH-4"); }
        return aica_pcm_position(channel);
    }
    return g_aica_regs[off / 4];
}

uint8_t dc_aica_reg_read8(uint32_t off) {
    off &= AICA_REG_SIZE - 1;
    if ((off & ~3u) == 0x2810) {
        unsigned channel = (g_aica_regs[0x280c / 4] >> 8) & 63u;
        aica_status_trace(off);
        /* L'octet bas peut être lu sans consommer LP ; seul l'octet haut
         * l'efface, comme sur la vraie puce. */
        uint32_t v = aica_pcm_status_ex(channel, (off & 1) != 0);
        return (uint8_t)(v >> ((off & 1) * 8));
    }
    uint32_t v = dc_aica_reg_read(off & ~3u);
    return (uint8_t)(v >> ((off & 3) * 8));
}

/* Le mot tel qu'il est rangé, sans les effets de bord de dc_aica_reg_read. */
uint32_t dc_aica_reg_peek(uint32_t off) {
    return g_aica_regs[(off & (AICA_REG_SIZE - 1)) / 4];
}

/* Set while the sound processor is the one running, so the trace below can say
 * which side of the mailbox a write came from. */
static int g_in_arm7;

void dc_aica_reg_write(uint32_t off, uint32_t val) {
    off &= AICA_REG_SIZE - 1;

    /* Sonde temporaire : qui ecrit quoi. Sans elle on ne peut pas distinguer
     * une commande du SH-4 perdue d'un pilote ARM7 qui ne la traite pas. */
    {
        static int trace = -1;
        if (trace < 0) { const char *e = getenv("PHANTOM_DC_SON_TRACE");
                         trace = (e && *e && atoi(e) == 0) ? 0 : 1; }
        if (trace && off < 0x2000 && (off & 0x7f) == 0) {
            static unsigned n;
            if (n < 400) {
                uint32_t sa = ((val & 0x7fu) << 16) | (g_aica_regs[(off + 4) / 4] & 0xffffu);
                printf("[SKEY] %s ch=%02u val=%04X kyonb=%u kyonex=%u sa=%05X lsa=%04X lea=%04X fmt=%u lp=%u",
                       g_in_arm7 ? "ARM7" : "SH4 ", off >> 7, val & 0xffffu,
                       (val >> 14) & 1u, (val >> 15) & 1u, sa,
                       g_aica_regs[(off + 8) / 4] & 0xffffu,
                       g_aica_regs[(off + 12) / 4] & 0xffffu,
                       (val >> 7) & 3u, (val >> 9) & 1u);
                putchar(10);
                n++;
            }
        }
        if (trace && (off == 0x2808 || off == 0x280c || off == 0x288c ||
                      (off >= 0x2810 && off <= 0x2816) ||
                      off == 0x28a0 || off == 0x28a4)) {
            static unsigned n;
            if (n < 200) {
                printf("[SREG] %s %04X = %08X", g_in_arm7 ? "ARM7" : "SH4 ", off, val);
                putchar(10);
                n++;
            }
        }
    }

    /* Channel registers are 64 blocks of 0x80 below 0x2000. Bit 15 of a
     * block's first word is KEY_ON_EX: writing it with bit 14 set starts the
     * channel playing. If nothing here ever fires, there is nothing to mix. */
    if (off < 0x2000 && (off & 0x7F) == 0 && (val & 0x8000)) {
        if (val & 0x4000 && ++g_aica_keyons == 1)
            printf("[AICA] first channel started - the driver is playing something\n");
        static int keyons;
        static int on = -1;
        if (on < 0) on = getenv("DCRECOMP_AICATRACE") ? 1 : 0;
        if (on && (val & 0x4000) && keyons < 24) {
            uint32_t ch = off >> 7;
            uint32_t sa = ((val & 0x7F) << 16) | (g_aica_regs[(off + 4) / 4] & 0xFFFF);
            printf("[AICA] ch%02u KEY%s  fmt=%u loop=%u start=%06X "
                   "lsa=%04X lea=%04X\n",
                   ch, (val & 0x4000) ? "ON " : "OFF",
                   (val >> 7) & 3, (val >> 9) & 1, sa,
                   g_aica_regs[(off + 8) / 4] & 0xFFFF,
                   g_aica_regs[(off + 12) / 4] & 0xFFFF);
            keyons++;
        }
    }

    if (off >= 0x2800 && off <= 0x2D10) {
        static int traced;
        static int on = -1;
        if (on < 0) on = getenv("DCRECOMP_AICATRACE") ? 1 : 0;
        if (on && traced < 60) {
            printf("[AICA] %s writes %04X = %08X\n",
                   g_in_arm7 ? "driver" : "SH-4 ", off, val);
            traced++;
        }
    }
    uint32_t prev = g_aica_regs[off / 4];
    g_aica_regs[off / 4] = val;
    if (off == 0x288C && (val & 1u))
        aica_internal_dma();
    if (off < 0x2000 && (off & 0x7f) == 0 && (val & 0x8000)) {
        /* Relevé PHANTOM_DC_AICA_KEYS=N : pour chaque voie qui démarre ou
         * s'arrête, tous ses réglages (enveloppe, filtre, effets, volume). */
        { static int reste = -2;
          if (reste == -2) { const char *e = getenv("PHANTOM_DC_AICA_KEYS"); reste = e ? atoi(e) : 40; }
          for (int ch = 0; reste > 0 && ch < 64; ++ch) {
              const uint32_t *v = &g_aica_regs[ch * 32];
              int avant = aica_pcm_channel_active(ch), apres = (v[0] & 0x4000u) != 0;
              if (avant == apres) continue;
              printf("[AICA-VOIE] %llu ms ch%02d %s", (unsigned long long)(platform_get_ticks_us() / 1000),
                     ch, apres ? "MARCHE" : "ARRET ");
              for (int i = 0; i < 18; ++i) printf(" %04X", v[i] & 0xFFFFu);
              printf("\n"); --reste;
              { static char dump[512]; static int dumped;
                if (!dump[0]) { const char *de = getenv("PHANTOM_DC_AICA_DUMP_KEY");
                    if (de && *de) snprintf(dump, sizeof dump, "%s", de);
                    else { const char *la = getenv("LOCALAPPDATA");
                           if (la) snprintf(dump, sizeof dump,
                                "%s/PhantomStation/Dreamcast-apprentissage/aica.bin", la); } }
                if (dump[0] && !dumped && apres && (v[1] & 0xFFFFu) == 0x4070u) {
                    FILE *f = fopen(dump, "wb");
                    if (f) { fwrite(sh4_get_aica_ram_ptr(), 1, 2u * 1024u * 1024u, f); fclose(f);
                             printf("[AICA-DUMP] key-on ch%02d SA=%04X -> %s\n", ch, v[1] & 0xFFFFu, dump); }
                    dumped = 1;
                } }
          } }
        aica_pcm_key_execute(g_aica_regs);
        g_aica_regs[off / 4] &= ~0x8000u;
    }
    if (off < 0x2000) aica_pcm_reg_write(g_aica_regs, off);
    if (off == 0x2800) aica_pcm_set_master(val & 15u, (val >> 15) & 1u);   /* MVOL, MONO */

    if (off == AICA_ARM_RESET_OFF && (prev & 1) && !(val & 1)) {
        /* Released. The driver the game uploaded into sound RAM starts running
         * for real, on the processor it was built for. */
        if (!g_arm7_wired) {
            arm7_init(&g_arm7, sh4_get_aica_ram_ptr(), 2 * 1024 * 1024);
            g_arm7_wired = true;
        }
        arm7_set_reset(&g_arm7, false);
        printf("[AICA] sound processor released - running its driver\n");
        sh4_aica_arm_released();
    } else if (off == AICA_ARM_RESET_OFF && (val & 1)) {
        arm7_set_reset(&g_arm7, true);
    } else if (off == AICA_SCIRE) {
        static int acks;
        if (getenv("DCRECOMP_ARM7") && acks < 8) {
            printf("[ARM7] driver acknowledges SCIPD bits %04X (pending %04X)%s\n",
                   val, *aica_reg(AICA_SCIPD),
                   ++acks == 8 ? " - servicing normally, quietening" : "");
        }
        *aica_reg(AICA_SCIPD) &= ~val;      /* SCIPD clears through SCIRE */
        aica_refresh_fiq();
    } else if (off == AICA_INTCLEAR) {
        aica_refresh_fiq();
    } else if (off == AICA_MCIRE) {
        *aica_reg(AICA_MCIPD) &= ~val;
    } else if (off == AICA_SCIPD || off == AICA_SCIEB) {
        if (off == AICA_SCIEB && getenv("DCRECOMP_ARM7"))
            printf("[ARM7] driver enables SCIEB=%04X  timers A=%04X B=%04X C=%04X\n",
                   val, *aica_reg(AICA_TIMER_A), *aica_reg(AICA_TIMER_B),
                   *aica_reg(AICA_TIMER_C));
        aica_refresh_fiq();
    } else if (off == AICA_MCIPD && (val & *aica_reg(AICA_MCIEB))) {
        /* The driver answering the SH-4. This is an *external* interrupt -
         * SB_ISTEXT bit 1, summarised into SB_ISTNRM bit 30 - the same shape
         * as the GD-ROM's completion signal above.
         *
         * It was wired to SB_ISTNRM bit 15 before, which is the G2 channel-0
         * DMA-end bit and belongs to the transfer, not the chip. Nothing the
         * game does clears bit 15 on its behalf, so raising it there stalled
         * the SH-4 in its handler and had to be kept behind a switch. On the
         * external line the game's own dispatcher acknowledges it. */
        dc_hw_signal_aica(sh4_get_hardware());
    }
}

uint32_t dc_hw_get_istnrm(DCHardware *hw) {
    return hw ? hw->sb_istnrm : 0;
}

uint32_t dc_hw_get_istext(DCHardware *hw) {
    return hw ? hw->sb_istext : 0;
}

void dc_hw_raise_istnrm(int bit) {
    DCHardware *hw = sh4_get_hardware();
    if (hw)
        hw->sb_istnrm |= (1u << bit);
}

void dc_pvr_wait_vblank(DCHardware *hw) {
    hw->vblank_count++;
    dc_rythme("top");

    /* Scan out whatever is in the framebuffer. A game fills VRAM either through
     * the tile accelerator or by writing it directly, and until it reaches its
     * render loop only the latter has happened - so presenting here is what
     * makes early frames visible at all. */
    static int nopresent = -1;
    if (nopresent < 0) nopresent = getenv("DCRECOMP_NO_PRESENT") ? 1 : 0;
    if (!nopresent && !pvr2_ta_has_drawn() &&
        pvr2_present_framebuffer(hw->pvr_fb_addr1,
                                 hw->hw_regs[hw_reg_idx(PVR_FB_R_CTRL)],
                                 DC_SCREEN_W, DC_SCREEN_H) > 0)
        platform_swap_buffers();
    platform_poll_events(hw);
    /* Set VBLANK interrupt */
    hw->sb_istnrm |= (1 << 3); /* VBlank-IN */
}

/* ========== Maple (Controllers) ========== */

void dc_maple_init(DCHardware *hw) {
#ifdef _WIN32
    /* La carte memoire doit exister par defaut : sans elle le jeu accepte la
     * sauvegarde puis repart du debut au lancement suivant. Le lanceur fournit
     * PHANTOM_GAME_DIR, un dossier qui survit aux relances. */
    static wchar_t vmu_path[1024];
    const wchar_t *vmu = _wgetenv(L"PHANTOM_DC_VMU");
    if (!vmu || !*vmu) vmu = _wgetenv(L"DCRECOMP_TEST_VMU");
    if (!vmu || !*vmu) {
        const wchar_t *dir = _wgetenv(L"PHANTOM_GAME_DIR");
        if (!dir || !*dir) dir = L".";
        swprintf(vmu_path, 1024, L"%s/vmu-a1.bin", dir);
        vmu = vmu_path;
    }
    int exact_card = _wgetenv(L"PHANTOM_DC_VMU_EXACT") != NULL;
    int opened = exact_card ? dc_vmu_open_w(vmu) : dc_vmu_autoopen_w(vmu);
    printf("[VMU] Carte memoire %s\n", opened ? "ouverte (sauvegardes persistantes)" : "indisponible");
#else
    static char vmu_path[1024];
    const char *vmu = getenv("PHANTOM_DC_VMU");
    if (!vmu || !*vmu) vmu = getenv("DCRECOMP_TEST_VMU");
    if (!vmu || !*vmu) {
        const char *dir = getenv("PHANTOM_GAME_DIR");
        if (!dir || !*dir) dir = ".";
        snprintf(vmu_path, sizeof vmu_path, "%s/vmu-a1.bin", dir);
        vmu = vmu_path;
    }
    printf("[VMU] Carte memoire %s\n", dc_vmu_open(vmu) ? "ouverte (sauvegardes persistantes)" : "indisponible");
#endif
    printf("[MAPLE] Controller bus initialized\n");
    hw->controller_connected[0] = true;
}

void dc_maple_poll(DCHardware *hw) {
    /* TODO: Read from SDL2 game controller */
    /* For now, controllers maintain their current state */
    (void)hw;
}

/* Process Maple DMA command table */
void dc_maple_dma(DCHardware *hw, uint32_t mdstar) {
    uint8_t *ram = sh4_get_ram_ptr();
    if (!ram) return;

    uint32_t addr = mdstar & 0x1FFFFFFF;
    bool last = false;
    int cmd_count = 0;
    static int maple_log = 0;

    while (!last && cmd_count < 32) {
        cmd_count++;

        uint32_t ram_size = sh4_get_ram_size(), mask = sh4_get_ram_mask();
        if ((addr & 0x1f000000u) != 0x0c000000u || (addr & mask) > ram_size-4) break;
        uint32_t header_1 = *(uint32_t *)(ram + (addr & mask));
        last = (header_1 >> 31) & 1;
        uint32_t plen = (header_1 & 0xFF) + 1;
        uint32_t maple_op = (header_1 >> 8) & 7;
        uint32_t bus = (header_1 >> 16) & 3;
        /* Non-transfer descriptors occupy one word, not 8 + payload bytes. */
        if (maple_op != 0) { addr += 4; continue; }
        if ((addr & mask) > ram_size-(8u+plen*4u)) break;
        uint32_t header_2 = *(uint32_t *)(ram + ((addr + 4) & mask)) & 0x1FFFFFE0;
        /* Never let a malformed descriptor write into RAM address zero. */
        if ((header_2 & 0x1f000000u) != 0x0c000000u || (header_2 & mask) > ram_size-532u) break;

        if (maple_op == 0) {
            /* MP_Start - process Maple frame */
            uint32_t *p_data = (uint32_t *)(ram + ((addr + 8) & mask));
            uint32_t frame_header = p_data[0];
            if ((frame_header >> 24) + 1u > plen) break;
            uint32_t command = frame_header & 0xFF;
            uint32_t reci = (frame_header >> 8) & 0xFF;
            uint32_t port = reci >> 6;

            /* Response destination */
            uint32_t *resp = (uint32_t *)(ram + (header_2 & mask));
            const char *watch = getenv("DCRECOMP_TEST_DMA_WATCH");
            if (watch) {
                uint32_t w = (uint32_t)strtoul(watch,NULL,0)&0x1fffffffu;
                if (header_2 <= w && header_2 + 532u > w)
                    fprintf(stderr,"[TEST-MAPLE-WATCH] dest=%08X cmd=%u\n",header_2,command);
            }

            if (maple_log < 40) {
                maple_log++;
                printf("[MAPLE] bus=%u cmd=0x%02X port=%u reci=0x%02X resp_before=0x%08X resp_addr=0x%08X\n",
                       bus, command, port, reci, resp[0], header_2);
            }

            /* Maple response: byte0=response_code, byte1=src, byte2=dst, byte3=len_words */
            /* Response recipient is the request sender; response sender is
             * the addressed peripheral (with its attached-unit bits). */
            uint32_t src_addr = (frame_header >> 16) & 0xFF;
            uint32_t dst_addr = reci;
            uint8_t *resp_bytes = (uint8_t *)resp;
            uint8_t *in_bytes = (uint8_t *)p_data;

            /* The opt-in diagnostic VMU occupies slot 1 of port A. */
            if (port == 0 && (reci & 0x1f) >= 1 && (reci & 0x1f) <= 5 && dc_vmu_active()) {
                static unsigned vmu_logged;
                if (getenv("DCRECOMP_TEST_VMU_TRACE") && vmu_logged++ < 100)
                    printf("[VMU-CMD] command=%02x words=%u func=%08x\n",command,frame_header>>24,(frame_header>>24) ? p_data[1] : 0);
                uint32_t head = (src_addr << 8) | (dst_addr << 16);
                unsigned nwords = frame_header >> 24;
                const uint32_t storage = 0x02000000u;
                if (command == 0x01) {
                    memset(resp,0,29*4);
                    resp[0]=head | 0x05u | (28u<<24);
                    resp[1]=storage; resp[2]=0x00410f00u;
                    /* Descripteur complet d'une VMU (memoire + ecran +
                     * horloge), annonce par defaut : sans lui le jeu ne voit
                     * aucune carte et la sauvegarde part dans le vide. */
                    resp[1]=0x0e000000u;
                    resp[2]=0x403f7e7eu;
                    resp[3]=0x00100500u;
                    resp[4]=0x00410f00u;
                    uint8_t *details=(uint8_t *)&resp[1];
                    details[16]=0xff; details[17]=0;
                    memset(details+18,' ',90);
                    memcpy(details+18,"PhantomStation VMU",18);
                } else if (getenv("DCRECOMP_TEST_FULL_VMU") && nwords >= 1 && p_data[1] == 0x04000000u) {
                    static uint8_t lcd[192];
                    if (command == 0x0a && nwords >= 2 && p_data[2] == 0) {
                        resp[0]=head|0x08u|(2u<<24); resp[1]=0x04000000u;
                        resp[2]=47u|(31u<<8)|(0x10u<<16)|(2u<<24);
                    } else if (command == 0x0b && nwords >= 2) {
                        resp[0]=head|0x08u|(50u<<24); resp[1]=0x04000000u; resp[2]=p_data[2];
                        memcpy(&resp[3],lcd,sizeof lcd);
                    } else if (command == 0x0c && nwords >= 50) {
                        memcpy(lcd,&p_data[3],sizeof lcd); resp[0]=head|0x07u;
                    } else resp[0]=head|0xfeu;
                } else if (command == 0x0a && nwords >= 1 && p_data[1] == storage) {
                    uint8_t root[512]; dc_vmu_read(255,root);
                    resp[0]=head|0x08u|(7u<<24); resp[1]=storage;
                    memcpy(&resp[2],root+0x40,24);
                } else if (command == 0x0b && nwords >= 2 && p_data[1] == storage) {
                    uint32_t arg=p_data[2];
                    unsigned block=(arg>>24)|((arg>>8)&0xff00u);
                    if (getenv("DCRECOMP_TEST_VMU_TRACE")) printf("[VMU-READ] block=%u\n",block);
                    resp[0]=head|0x08u|(130u<<24); resp[1]=storage; resp[2]=arg;
                    if (!dc_vmu_read(block,(uint8_t *)&resp[3])) resp[0]=head|0xfbu;
                } else if (command == 0x0c && nwords >= 3 && nwords <= 34 && p_data[1] == storage) {
                    uint32_t arg=p_data[2];
                    unsigned block=((arg & 0xffu)<<8)|((arg>>8)&0xffu);
                    unsigned swapped=((arg&0xffu)<<24)|((arg&0xff00u)<<8)|((arg>>8)&0xff00u)|(arg>>24);
                    block=swapped&0xffffu; unsigned phase=(swapped>>16)&0xffu;
                    if (getenv("DCRECOMP_TEST_VMU_TRACE")) printf("[VMU-WRITE] block=%u phase=%u\n",block,phase);
                    resp[0]=head|(dc_vmu_write(block,phase,(uint8_t *)&p_data[3],(nwords-2)*4u)?0x07u:0xfbu);
                } else if (command == 0x0d) {
                    resp[0]=head|0x07u;
                } else resp[0]=head|0xfeu;
                addr += 8 + plen*4;
                continue;
            }
            if (port == 0 && (reci & 0x1f) == 0 && dc_vmu_active()) dst_addr |= 1u;

            /* Check if port has a connected device */
            bool has_device = (port < 4) && hw->controller_connected[port];

            /* Non-main-unit subdevices (reci & 0x1F != 0) or no device â†’ -1 */
            if (!has_device || (reci & 0x1F) != 0) {
                resp[0] = (uint32_t)-1;
            } else {
                MapleController *ctrl = &hw->controllers[port];
                switch (command) {
                case 0x01: { /* MDC_DeviceRequest - get device info */
                    if (getenv("DCRECOMP_TEST_CONTROLLER_DESCRIPTOR")) {
                        memset(resp, 0, 29 * 4);
                        resp[0] = 0x05 | (src_addr << 8) | (dst_addr << 16) | (28u << 24);
                        resp[1] = 0x01000000u;
                        resp[2] = 0xfe060f00u;
                        uint8_t *details = (uint8_t *)&resp[1];
                        details[16] = 0xff; details[17] = 0;
                        memset(details + 18, ' ', 90);
                        memcpy(details + 18, "Dreamcast Controller", 20);
                        details[108] = 0xae; details[109] = 0x01;
                        details[110] = 0xf4; details[111] = 0x01;
                        break;
                    }
                    /* MDRS_DeviceStatus (0x05), 28 words of device info */
                    resp[0] = 0x05 | (src_addr << 8) | (dst_addr << 16) | (0x1C << 24);
                    /* Function types: controller (bit 0) */
                    resp[1] = 0x01000000; /* ft0 = Controller */
                    resp[2] = 0x00000000; /* ft1 = none */
                    resp[3] = 0x00000000; /* ft2 = none */
                    /* Function definition block 0: controller capabilities
                     * Bits[31:24] = 0xFF = all standard buttons
                     * Bits[23:20] = 0x0  = R trigger present
                     * Bits[19:16] = 0x6  = L trigger + analog axes */
                    resp[4] = 0xFE060000;
                    resp[5] = 0x00000000;
                    resp[6] = 0x00000000;
                    /* Product name (30 bytes) + license (60 bytes) + current */
                    memset(&resp[7], 0x20, 84);
                    memcpy(&resp[7], "Dreamcast Controller          ", 30);
                    memcpy(&resp[15], "Produced By or Under License From SEGA ENTERPRISES,LTD.   ", 58);
                    resp[29] = 0x01AE; /* standby 430mA / max 430mA */
                    break;
                }

                case 0x03: /* MDC_DeviceReset */
                    resp[0] = 0x07 | (src_addr << 8) | (dst_addr << 16) | (0x00 << 24);
                    break;

                case 0x05: /* MDC_AllStatusReq */
                    resp[0] = 0x07 | (src_addr << 8) | (dst_addr << 16) | (0x00 << 24);
                    break;

                case 0x09: { /* MDC_GetCondition */
                    uint16_t buttons = (uint16_t)(ctrl->buttons & (uint16_t)~dc_cinematic_buttons());
                    if (getenv("DCRECOMP_TEST_MAPLE_INPUT") && buttons != 0xffffu)
                        fprintf(stderr,"[TEST-MAPLE] port=%u buttons=%04X\n",port,buttons);
                    /* Request data[1] = function type to query */
                    uint32_t func_type = p_data[1];
                    if ((func_type & 0x01000000) || func_type == 0x01) {
                        /* Controller condition: 3 data words */
                        resp[0] = 0x08 | (src_addr << 8) | (dst_addr << 16) | (0x03 << 24);
                        resp[1] = 0x01000000; /* ft = Controller */
                        /* Condition word 1 (mc.pp.se/dc/controller.html et
                         * Flycast maple_devs.cpp) : int16 buttons en bits 0-15
                         * (actifs a 0), puis octet 2 = gachette droite, octet 3
                         * = gachette gauche. Placer les boutons ailleurs faisait
                         * lire 0xFFFF au jeu : START et toutes les touches
                         * restaient muets. */
                        resp[2] = (uint32_t)buttons
                                | ((uint32_t)(uint8_t)ctrl->rtrig << 16)
                                | ((uint32_t)(uint8_t)ctrl->ltrig << 24);
                        { static unsigned nlog = 0;
                          if (nlog < 300 && buttons != 0xffffu) {
                              nlog++;
                              printf("[MAPLE-IN] port=%u reci=%02X func=%08X buttons=%04X resp2=%08X",
                                     port, reci, func_type, buttons, resp[2]);
                              putchar(10);
                          } }
                        /* Condition data word 2:
                         * bits[7:0]   = joystick X (0=left, 128=center, 255=right)
                         * bits[15:8]  = joystick Y (0=up, 128=center, 255=down)
                         * bits[23:16] = joy2 X
                         * bits[31:24] = joy2 Y */
                        resp[3] = (uint32_t)(uint8_t)(ctrl->joyx + 128)
                                | ((uint32_t)(uint8_t)(ctrl->joyy + 128) << 8)
                                | ((uint32_t)(uint8_t)(ctrl->joy2x + 128) << 16)
                                | ((uint32_t)(uint8_t)(ctrl->joy2y + 128) << 24);
                    } else {
                        { static unsigned nlog = 0;
                          if (nlog < 40) {
                              nlog++;
                              printf("[MAPLE-IN-REFUS] port=%u func=%08X", port, func_type);
                              putchar(10);
                          } }
                        resp[0] = (uint32_t)-1; /* Unknown function type */
                    }
                    break;
                }

                /* === Naomi JVS commands (0x80+) === */
                case 0x80: { /* MDC_JVSUploadFirmware */
                    uint8_t sum = 0;
                    for (uint32_t i = 4; i < plen * 4; i++)
                        sum += in_bytes[i];
                    resp[0] = 0x80 | (src_addr << 8) | (dst_addr << 16) | (0x01 << 24);
                    resp[1] = (uint32_t)sum;
                    resp[2] = 0x07 | (src_addr << 8) | (dst_addr << 16) | (0x00 << 24);
                    break;
                }

                case 0x82: { /* MDC_JVSGetId */
                    static const char MIE_ID[56] = "315-6149    COPYRIGHT SEGA ENTERPRISES CO,LTD.  1998";
                    resp[0] = 0x83 | (src_addr << 8) | (dst_addr << 16) | (0x07 << 24);
                    memcpy(&resp[1], MIE_ID, 28);
                    resp[8] = 0x83 | (src_addr << 8) | (dst_addr << 16) | (0x05 << 24);
                    memcpy(&resp[9], MIE_ID + 28, 20);
                    break;
                }

                case 0x84: /* MDC_JVSSelfTest */
                    resp[0] = 0x85 | (src_addr << 8) | (dst_addr << 16) | (0x01 << 24);
                    resp[1] = 0;
                    break;

                case 0x86: { /* MDC_JVSCommand */
                    uint8_t subcmd = in_bytes[4];
                    if (maple_log < 20) {
                        maple_log++;
                        printf("[JVS] subcmd=0x%02X\n", subcmd);
                    }
                    /* Generic OK response for all JVS subcommands */
                    resp[0] = 0x87 | (src_addr << 8) | (dst_addr << 16) | (0x01 << 24);
                    resp[1] = 0;
                    if (subcmd == 0x15) {
                        /* Read controls: return 14 words */
                        resp[0] = 0x87 | (src_addr << 8) | (dst_addr << 16) | (0x0E << 24);
                        resp[1] = 0x00000001;
                        for (int i = 2; i <= 14; i++) resp[i] = 0;
                    }
                    break;
                }

                default:
                    resp[0] = (uint32_t)-1;
                    break;
                }
            }
        }
        /* else: NOP, Reset, SDCKB - just skip */

        /* Advance to next descriptor */
        addr += 8 + plen * 4;
    }
}

void dc_maple_set_connected(DCHardware *hw, int port, bool connected) {
    if (!hw || port < 0 || port >= 4) return;
    if (hw->controller_connected[port] != connected) {
        memset(&hw->controllers[port], 0, sizeof hw->controllers[port]);
        hw->controllers[port].buttons = 0xffff;
        hw->controller_connected[port] = connected;
    }
}

MapleController* dc_maple_get_controller(DCHardware *hw, int port) {
    if (!hw || port < 0 || port >= 4) return NULL;
    if (!hw->controller_connected[port]) return NULL;
    return &hw->controllers[port];
}

/* ========== AICA Sound ========== */

void dc_aica_init(DCHardware *hw) {
    aica_pcm_reset();
    printf("[AICA] Sound processor initialized\n");
    hw->aica_arm_running = false;
}

void dc_aica_mix(int16_t *stereo, unsigned frames) {
    aica_pcm_mix(g_aica_regs,sh4_get_aica_ram_ptr(),stereo,frames);
}

/* Etat des voix pour l'analyse du son enregistree pendant une partie
 * (platform_sdl2.c) : combien de voix jouent, combien bouclent sur un laps de
 * temps tres court (une boucle de quelques millisecondes est ce qui sonne
 * comme une alarme), et les reglages d'une voix donnee. */
unsigned dc_aica_voice_count(void) {
    unsigned n = 0;
    for (int ch = 0; ch < 64; ++ch) n += aica_pcm_channel_active(ch) != 0;
    return n;
}

unsigned dc_aica_keyon_total(void) { return g_aica_keyons; }

unsigned dc_aica_master_volume(void) { return g_aica_regs[0x2800 / 4] & 15u; }

uint32_t dc_aica_channel_reg(unsigned ch, unsigned word) {
    if (ch >= 64 || word >= 32) return 0;
    return g_aica_regs[ch * 32 + word];
}

int dc_aica_channel_playing(unsigned ch) {
    return ch < 64 && aica_pcm_channel_active(ch);
}

unsigned dc_aica_small_loops(unsigned max_samples, unsigned *first_channel) {
    unsigned n = 0;
    if (first_channel) *first_channel = 0xffffffffu;
    for (int ch = 0; ch < 64; ++ch) {
        if (!aica_pcm_channel_active(ch)) continue;
        const uint32_t *v = &g_aica_regs[ch * 32];
        if (!((v[0] >> 9) & 1u)) continue;            /* pas de bouclage */
        uint32_t lsa = v[2] & 0xffffu, lea = v[3] & 0xffffu;
        if (lea <= lsa) continue;
        if (lea - lsa <= max_samples) {
            if (first_channel && *first_channel == 0xffffffffu) *first_channel = (unsigned)ch;
            ++n;
        }
    }
    return n;
}

void dc_aica_write_channel(DCHardware *hw, int ch, uint32_t offset, uint32_t val) {
    (void)hw;
    (void)ch;
    (void)offset;
    (void)val;
    /* TODO: Implement AICA channel control */
}

/* Timer A is the driver's heartbeat. The counter runs off the 44.1kHz sample
 * clock through a prescaler, and wraps past 0xFF into an interrupt.
 *
 * ponytail: driven off the wall clock rather than a cycle count, because
 * nothing here counts SH-4 cycles to pace it against. That makes the tick rate
 * right on average and jittery instant to instant, which a sound driver
 * tolerates - it is counting interrupts, not timing them. ARM7_IPS below is the
 * knob if the driver turns out to care.
 */
/* The real part runs at 2.8MHz. Pace it off the wall clock rather than off
 * how often the SH-4 happens to poll: tying it to the poll rate made the sound
 * processor's speed a function of what the game was doing, and during sound
 * init - which is exactly when the driver has work to do - the SH-4 polls
 * rarely enough to starve it to about 97k instructions a second. The driver
 * then took over a second to answer a request the game expected in
 * microseconds, and the tone bank load gave up before it landed. */
#define ARM7_HZ   2800000
/* Big on purpose. The processor only advances when the SH-4 reaches
 * dc_aica_update, which happens once per 256 SH-4 memory accesses - so the
 * busier the SH-4, the more the sound chip runs, which is exactly backwards.
 * When the game is *waiting* on the driver it does almost no memory access,
 * the driver is called sixty times a second instead of thousands, and the wait
 * can never end: the thing being waited for only runs when the waiter is busy.
 *
 * A burst large enough to clear a whole tick's debt in one call ought to break
 * that loop. Tried at 50000 and it did not - the processor is not
 * debt-limited, so the shortfall is upstream of this. Left at a size that
 * cannot stall the SH-4 noticeably. */
#define ARM7_MAX_BURST 4000
/* One tick of the coarsest clock we run on. GetTickCount64 moves in 15.6ms
 * steps, so a debt ceiling below 15.6ms x 2.8MHz = ~44k instructions throws
 * away most of every tick and the processor crawls at a twentieth of its
 * speed - which starved ChuChu Rocket's driver of the Timer A interrupts its
 * initialisation counts on. */
#define ARM7_MAX_DEBT  64000    /* was 20000. Bigger is worse past this: the
                                 * processor only advances inside an SH-4
                                 * memory access, so a large catch-up burst
                                 * stalls the SH-4, which delays the next
                                 * poll, which grows the debt again. Letting
                                 * it reach 600k cost the SH-4 six sevenths
                                 * of its memory traffic and the game stopped
                                 * rendering entirely. */

uint32_t dc_aica_bus_reads;

void dc_aica_update(DCHardware *hw) {
    (void)hw;
    /* La réserve de la sortie son est remplie aussi pendant les longs calculs
     * du jeu (toutes les 4 ms), pas seulement entre deux images. */
    { static unsigned appels; static uint64_t dernier_us;
      if ((++appels & 63u) == 0) {
          uint64_t us = platform_get_ticks_us();
          if (us - dernier_us >= 4000) { dernier_us = us; platform_audio_pump(); }
      } }
    if (!g_arm7.running) {
        dc_aica_bus_reads = 0;
        return;
    }

    aica_advance_timer_a();

    uint64_t now = platform_get_ticks_ms();

    /* Instructions owed for the time that has actually passed, kept as a debt
     * rather than a per-call quota.
     *
     * The processor only advances when the SH-4 gets here, so anything that
     * stalls the SH-4 - a printf in a trace build, a slow frame - is time the
     * sound processor loses. Capping each burst and throwing the rest away
     * made that permanent: with tracing on, the driver fell far enough behind
     * that the game's tone bank load overtook it and failed, so turning on the
     * instrumentation was enough to break the thing being instrumented.
     * Carrying the debt lets it catch up on the next poll instead. */
    static uint64_t arm_last_ms;
    static int64_t  arm_debt;
    if (!arm_last_ms) arm_last_ms = now;
    {
        /* DCRECOMP_ARM7_HZ / _BURST / _DEBT override these, for working out
         * whether a game is racing the sound processor. */
        static int hz = -1, maxburst, maxdebt;
        if (hz < 0) { const char *e;
                      e = getenv("DCRECOMP_ARM7_HZ");    hz = e ? atoi(e) : ARM7_HZ;
                      e = getenv("DCRECOMP_ARM7_BURST"); maxburst = e ? atoi(e) : ARM7_MAX_BURST;
                      e = getenv("DCRECOMP_ARM7_DEBT");  maxdebt = e ? atoi(e) : ARM7_MAX_DEBT;
                      if (hz < 1) hz = ARM7_HZ;
                      if (maxburst < 1) maxburst = ARM7_MAX_BURST;
                      if (maxdebt < 1) maxdebt = ARM7_MAX_DEBT; }
        arm_debt += (int64_t)(now - arm_last_ms) * (hz / 1000);
        arm_last_ms = now;
        /* Plus the time the SH-4 spent on the G2 bus reading sound RAM or
         * registers, about 250ns a read. Compiled, a wait loop on the driver
         * goes round far faster than on hardware, and a game that counts its
         * tries gives up before the driver is up: Crazy Taxi polls +0x5C at
         * most 0x400000 times, a second of bus time on the console and a few
         * dozen milliseconds here - 64k sound instructions, not enough for the
         * driver to say it is ready ("AICADRV ERR"). */
        { static uint64_t bus_frac;
          bus_frac += (uint64_t)dc_aica_bus_reads * (uint64_t)hz; dc_aica_bus_reads = 0;
          arm_debt += (int64_t)(bus_frac / 4000000u); bus_frac %= 4000000u; }
        if (arm_debt > maxdebt) arm_debt = maxdebt;
        int burst = arm_debt > maxburst ? maxburst : (int)arm_debt;
        if (burst > 0) {
            g_in_arm7 = 1;
            dc_ralentis_debut(RAL_ARM7); arm7_run(&g_arm7, burst); dc_ralentis_fin(RAL_ARM7);
            g_in_arm7 = 0;
            arm_debt -= burst;
        }
        { static int light = -1; static uint64_t next, last;
          if (light < 0) { const char *e = getenv("PHANTOM_DC_SON_TRACE");
                           light = (e && *e && atoi(e) == 0) ? 0 : 1; }
          if (light && now >= next) {
              next = now + 1000;
              printf("[SARM] %llu instr/s pc=%08X fiq=%llu keyons=%u voix=%u scipd=%04X mcipd=%04X",
                     (unsigned long long)(g_arm7.instructions - last), g_arm7.r[15],
                     (unsigned long long)arm7_fiq_count(&g_arm7), g_aica_keyons,
                     dc_aica_voice_count(), *aica_reg(AICA_SCIPD), *aica_reg(AICA_MCIPD));
              putchar(10);
              last = g_arm7.instructions;
          } }
    }

    /* DCRECOMP_ARM7=1: is the driver running code, and is it going anywhere? */
    static int report = -1;
    if (report < 0) report = getenv("DCRECOMP_ARM7") ? 1 : 0;
    if (report) {
        static uint64_t next_ms;
        static uint64_t last_instrs;
        if (now >= next_ms) {
            next_ms = now + 1000;
            printf("[ARM7] pc=%08X cpsr=%08X mode=%02X  +%llu instrs  "
                   "scipd=%04X scieb=%04X mcipd=%04X\n",
                   g_arm7.r[15], g_arm7.cpsr, g_arm7.cpsr & 0x1F,
                   (unsigned long long)(g_arm7.instructions - last_instrs),
                   *aica_reg(AICA_SCIPD), *aica_reg(AICA_SCIEB),
                   *aica_reg(AICA_MCIPD));
            last_instrs = g_arm7.instructions;
            arm7_dump_polled_regs();
            arm7_dump_polled_ram();
            printf("[ARM7] timerA fires=%u  ta=%04X  fiq_taken=%llu\n",
                   g_timer_a_fires, *aica_reg(AICA_TIMER_A),
                   (unsigned long long)arm7_fiq_count(&g_arm7));
            { const uint8_t *ram = sh4_get_aica_ram_ptr();
              if (ram) {
                  printf("[ARM7] mailbox 12200:");
                  for (int i = 0; i < 8; i++)
                      printf(" %08X", ((const uint32_t *)(ram + 0x12200))[i]);
                  printf("\n[ARM7] reply 12400: %08X   EXEC 000F8: %08X\n",
                         *(const uint32_t *)(ram + 0x12400),
                         *(const uint32_t *)(ram + 0xF8));
              } }
        }
    }
}

/* ========== GD-ROM ========== */

/* The drive signals completion with an external interrupt: SB_ISTEXT bit 0,
 * summarised into SB_ISTNRM bit 30. A game's interrupt dispatcher is what then
 * runs the driver's completion callback, so a command that finishes without
 * this is a command the driver never learns about. */
void dc_gdrom_signal_complete(DCHardware *hw) {
    dc_gdrom_signal_command_end(hw, 1);
}

/* G1 DMA end only when the command moved data by DMA: raised for every
 * command, nothing ever acknowledged it for a status or TOC request, and the
 * level-4 handler ran on every poll - 1500 times a second, most of the time
 * Grandia II had, which left its movie decoder a frame every few seconds. */
void dc_gdrom_signal_command_end(DCHardware *hw, int dma) {
    if (!hw) return;
    hw->sb_istext |= 1u;          /* external: GD-ROM */
    hw->sb_istnrm |= (1u << 30);  /* summary bit for external interrupts */
    if (dma) hw->sb_istnrm |= (1u << 14);
}

/* The AICA signals the SH-4 the same way the drive does: an external
 * interrupt, SB_ISTEXT bit 1, summarised into SB_ISTNRM bit 30. */
void dc_hw_signal_aica(DCHardware *hw) {
    if (!hw) return;
    hw->sb_istext |= (1u << 1);
    hw->sb_istnrm |= (1u << 30);
}

void dc_gdrom_init(DCHardware *hw) {
    printf("[GDROM] Drive initialized\n");
    hw->gdrom_status = 0;
}

int dc_gdrom_read_sectors(DCHardware *hw, uint32_t lba, uint32_t count, void *buf) {
    (void)hw;
    (void)lba;
    (void)count;
    (void)buf;
    /* TODO: Read from extracted disc files */
    return 0;
}

/* For the heartbeat: how much work the sound processor has done. */
uint64_t dc_aica_arm_instructions(void) { return arm7_instruction_count(&g_arm7); }

/* One-shot: what is the sound processor doing right now? */
void dc_aica_arm_state(void) {
    printf("[AICA] driver pc=%08X mode=%02X scipd=%04X scieb=%04X intreq=%u\n",
           g_arm7.r[15], g_arm7.cpsr & 0x1F,
           g_aica_regs[AICA_SCIPD / 4], g_aica_regs[AICA_SCIEB / 4],
           aica_int_level());
    arm7_dump_polled_regs();
}
