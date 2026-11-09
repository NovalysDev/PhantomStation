/**
 * SH-4 CPU State Structure
 *
 * Represents the complete state of the Hitachi SH-4 CPU as used in the
 * Sega Dreamcast and Naomi arcade hardware. All recompiled code operates
 * on this structure.
 *
 * Memory map (Dreamcast / Naomi):
 *   0x00000000 - 0x001FFFFF : Boot ROM (2MB)
 *   0x00200000 - 0x0021FFFF : Flash ROM (128KB)
 *   0x00800000 - 0x009FFFFF : AICA Sound (2MB)
 *   0x04000000 - 0x047FFFFF : VRAM (8MB, 64-bit access)
 *   0x05000000 - 0x057FFFFF : VRAM (8MB, 32-bit access)
 *   0x0C000000 - 0x0CFFFFFF : System RAM (16MB DC / 32MB Naomi)
 *   0x10000000 - 0x107FFFFF : Tile Accelerator
 *   0x10800000 - 0x10FFFFFF : Hardware Registers
 *   0x8C000000 - 0x8CFFFFFF : System RAM (cached, P1 area)
 *   0xAC000000 - 0xACFFFFFF : System RAM (uncached, P2 area)
 */

#ifndef SH4_CPU_H
#define SH4_CPU_H

#include <stdint.h>
#include <stdbool.h>

/* SH-4 Status Register bits */
#define SR_T    (1 << 0)   /* True/False bit */
#define SR_S    (1 << 1)   /* Saturating operation */
#define SR_IMASK 0x000000F0 /* Interrupt mask */
#define SR_Q    (1 << 8)   /* Quotient bit */
#define SR_M    (1 << 9)   /* M bit for division */
#define SR_FD   (1 << 15)  /* FPU disable */
#define SR_BL   (1 << 28)  /* Block exceptions */
#define SR_RB   (1 << 29)  /* Register bank */
#define SR_MD   (1 << 30)  /* Processor mode */

/* FPSCR bits */
#define FPSCR_RM    0x00000003  /* Rounding mode */
#define FPSCR_DN    (1 << 18)   /* Denormalization mode */
#define FPSCR_PR    (1 << 19)   /* Precision mode (0=single, 1=double) */
#define FPSCR_SZ    (1 << 20)   /* Transfer size mode */
#define FPSCR_FR    (1 << 21)   /* Floating-point register bank */

/* Memory region bases (shared by DC and Naomi) */
#define DC_RAM_BASE     0x0C000000
#define DC_VRAM_BASE    0x04000000
#define DC_VRAM_SIZE    0x00800000  /* 8 MB */
#define DC_AICA_BASE    0x00800000
#define DC_AICA_SIZE    0x00200000  /* 2 MB */

/* RAM size configuration - set at init time */
#define DC_RAM_SIZE_16MB  0x01000000  /* 16 MB (Dreamcast) */
#define DC_RAM_SIZE_32MB  0x02000000  /* 32 MB (Naomi) */

/* Default RAM size (can be overridden before sh4_init) */
#ifndef DC_RAM_SIZE
#define DC_RAM_SIZE     DC_RAM_SIZE_16MB
#endif

#define DC_RAM_MASK_16MB  0x00FFFFFF
#define DC_RAM_MASK_32MB  0x01FFFFFF

/* P1/P2 mirror masks */
#define ADDR_MASK_P1    0x1FFFFFFF  /* Strip P1/P2/P3/P4 area bits */

/* 1ST_READ.BIN default load address */
#define GAME_LOAD_ADDR  0x8C010000

typedef union {
    float f[2];
    double d;
    uint32_t u[2];
} FPRegPair;

typedef struct SH4CPU {
    /* General purpose registers (R0-R15) */
    uint32_t r[16];

    /* Banked registers (R0_BANK-R7_BANK) */
    uint32_t r_bank[8];

    /* Control registers */
    uint32_t sr;        /* Status Register */
    uint32_t gbr;       /* Global Base Register */
    uint32_t vbr;       /* Vector Base Register */
    uint32_t ssr;       /* Saved Status Register */
    uint32_t spc;       /* Saved Program Counter */
    uint32_t sgr;       /* Saved GBR */
    uint32_t dbr;       /* Debug Base Register */

    /* System registers */
    uint32_t mach;      /* Multiply-Accumulate High */
    uint32_t macl;      /* Multiply-Accumulate Low */
    uint32_t pr;        /* Procedure Register (return address) */
    uint32_t pc;        /* Program Counter */

    /* Floating point registers (FPR0-FPR15 in two banks) */
    float fr[16];       /* FPR bank 0 (or current bank) */
    float xf[16];       /* FPR bank 1 (or other bank) */
    uint32_t fpscr;     /* Floating-point Status/Control Register */
    uint32_t fpul;      /* Floating-point Communication Register */

    /* Memory */
    uint8_t *ram;       /* Main RAM (16 or 32 MB) */
    uint8_t *vram;      /* Video RAM (8 MB) */
    uint8_t *aica_ram;  /* AICA Sound RAM (2 MB) */
    uint32_t ram_size;  /* Actual RAM size in bytes */
    uint32_t ram_mask;  /* RAM address mask */

    /* Store Queues (two 32-byte queues for DMA to VRAM/TA) */
    uint32_t sq[2][8];  /* SQ0 and SQ1, 8 words each */
    uint32_t qacr[2];   /* Queue Address Control Registers */

    /* P4 on-chip registers (DMAC, TMU, etc.) */
    /* DMAC: 0xFFA00000-0xFFA00044 */
    uint32_t dmac_regs[17]; /* SAR0..CHCR3 (16 regs) + DMAOR */
    /* TMU: 0xFFD80000-0xFFD8002C */
    uint32_t tmu_regs[12];  /* TOCR, TSTR, TCOR0, TCNT0, TCR0, ... */

    /* MMU state */
    uint32_t mmucr;         /* MMU Control Register (0xFF000010) */

    /* UTLB entries (64 entries) */
    uint32_t utlb_addr[64];
    uint32_t utlb_data1[64];
    uint32_t utlb_data2[64];

    /* Execution state */
    bool running;
    uint64_t cycles;
    uint32_t delay_slot; /* Non-zero if next instruction is in a delay slot */
} SH4CPU;

/* Initialize CPU state with specified RAM size */
void sh4_init_ex(SH4CPU *cpu, uint32_t ram_size);

/* Initialize CPU state (default 16MB RAM for DC compatibility) */
void sh4_init(SH4CPU *cpu);

/* Reset CPU */
void sh4_reset(SH4CPU *cpu);

/* Destroy CPU (free memory) */
void sh4_destroy(SH4CPU *cpu);

/* Set external hardware reference for register routing */
struct DCHardware;
void sh4_set_hardware(struct DCHardware *hw);
struct DCHardware *sh4_get_hardware(void);
void sh4_set_cpu_ref(SH4CPU *cpu);

/* Get raw memory pointers (for DMA) */
uint8_t *sh4_get_ram_ptr(void);
uint8_t *sh4_get_vram_ptr(void);
uint32_t sh4_get_vram_capacity(void);
uint8_t *sh4_get_aica_ram_ptr(void);

/* Answer reads of one word of sound RAM with `value`, as the sound
 * driver would have. The driver runs on the AICA's ARM7, which we do not
 * execute, so a game that waits for it to report itself ready waits
 * forever. `offset` is relative to the start of sound RAM.
 *
 * A stub, and only worth having until there is an ARM7 to run: the game
 * gets past its sound init and no sound comes out. Which word and which
 * value belong to the driver, so the game declares them, not the hardware
 * model. DCRECOMP_AICAPOLL names any word a game is stuck reading. */
void sh4_aica_publish(uint32_t offset, uint32_t value, uint32_t after_ms);

/* Answer a call to `addr` with `r0 = value` instead of running it.
 *
 * For subsystems that cannot work because the hardware under them is not
 * modelled - the sound driver on the AICA's ARM7 being the one that matters
 * today. A game declares these in its own bring-up file so they survive
 * regeneration and are visible to anyone reading it.
 *
 * Reaches only calls that go through the dispatcher: jsr or jmp through a
 * register. A bsr is a direct call in the generated C, so choose an address
 * the game arrives at indirectly. */
void sh4_stub_function(uint32_t addr, uint32_t value);

/* True if `addr` is stubbed; fills in the value. Used by the dispatcher. */
bool sh4_stubbed_function(uint32_t addr, uint32_t *value);

/* The sound processor has been released from reset. Published words start
 * answering shortly afterwards - the delay is the point, see the note by the
 * implementation. */
void sh4_aica_arm_released(void);

/* FMOV, honouring FPSCR.SZ and the XD encoding.
 *
 * With SZ clear these move one float. With SZ set they move a pair, and an odd
 * register number names that pair in the *other* bank - which is how a game
 * loads XMTRX. SZ is a runtime bit, so the decision lives here rather than at
 * every call site. */
void sh4_fmov_reg(SH4CPU *cpu, int n, int m);
void sh4_fmov_load(SH4CPU *cpu, int n, uint32_t addr);
void sh4_fmov_store(SH4CPU *cpu, int m, uint32_t addr);
void sh4_fmov_load_inc(SH4CPU *cpu, int n, int m);
void sh4_fmov_store_dec(SH4CPU *cpu, int m, int n);

/* Swap the two float banks. FRCHG changes which one the FR names mean; doing
 * it as a real swap keeps "current bank" always fr[] and leaves xf[] as
 * XMTRX, which is what FTRV wants. */
void sh4_frchg(SH4CPU *cpu);

/* One step of restoring division: DIV1 Rm,Rn. The SH-4 has no divide
 * instruction - DIV0S or DIV0U sets up, then thirty-two of these. */
void sh4_div1(SH4CPU *cpu, int n, int m);

/* Account for time a device took that we did not. A GD-ROM needs about a
 * second for a megabyte and a half; the game gets sixty frames in that time,
 * and its per-frame housekeeping runs in them. Whole frames of the credit are
 * delivered as VBlanks from the next poll onward, one per poll, so they
 * interleave with the game's code the way they would have. */
void sh4_credit_elapsed_ms(uint32_t ms);

uint32_t sh4_get_ram_size(void);
uint32_t sh4_get_ram_mask(void);

/* Get DMAC register value (for CH2-DMA etc.) */
uint32_t sh4_get_dmac_reg(int idx);

/* Memory access functions */
uint8_t  sh4_read8(SH4CPU *cpu, uint32_t addr);
uint16_t sh4_read16(SH4CPU *cpu, uint32_t addr);
uint32_t sh4_read32(SH4CPU *cpu, uint32_t addr);
float    sh4_read_float(SH4CPU *cpu, uint32_t addr);
void     sh4_write8(SH4CPU *cpu, uint32_t addr, uint8_t val);
void     sh4_write16(SH4CPU *cpu, uint32_t addr, uint16_t val);
void     sh4_write32(SH4CPU *cpu, uint32_t addr, uint32_t val);
void     sh4_write_float(SH4CPU *cpu, uint32_t addr, float val);

/* Inline main RAM access for translated code (SH4_FAST_RAM). Main RAM through
 * P1/P2 is what nearly every access is, and the out-of-line path tests P4,
 * the TLB arrays and every other region first. Reads keep the same count:
 * the one that lands on a poll boundary takes the full path, which polls.
 * Writes only go inline while no watchpoint or legacy guard has to see
 * them. Grandia II's movie decoder kept up with half its pictures without
 * it. */
extern uint32_t sh4_read_seq, g_write_seq;
extern int sh4_fast_writes;
#ifdef SH4_FAST_RAM
static inline __attribute__((always_inline)) uint8_t *sh4_fast_ram(SH4CPU *cpu, uint32_t addr) {
    uint32_t phys = addr & 0x1FFFFFFFu;
    return (addr >> 30) == 2 && phys - 0x0C000000u < cpu->ram_size ? cpu->ram + (phys & cpu->ram_mask) : (uint8_t *)0;
}
#define SH4_FAST_READ(type, slow)                                              \
    static inline __attribute__((always_inline)) type slow##_inline(SH4CPU *cpu, uint32_t addr) { \
        uint8_t *p = sh4_fast_ram(cpu, addr);                                  \
        if (p && ((sh4_read_seq + 1) & 0xFF)) { sh4_read_seq++; type v; __builtin_memcpy(&v, p, sizeof v); return v; } \
        return slow(cpu, addr);                                                \
    }
#define SH4_FAST_WRITE(type, slow)                                             \
    static inline __attribute__((always_inline)) void slow##_inline(SH4CPU *cpu, uint32_t addr, type val) { \
        uint8_t *p = sh4_fast_writes > 0 ? sh4_fast_ram(cpu, addr) : (uint8_t *)0; \
        if (p) { __builtin_memcpy(p, &val, sizeof val); return; }              \
        slow(cpu, addr, val);                                                  \
    }
SH4_FAST_READ(uint8_t, sh4_read8)
SH4_FAST_READ(uint16_t, sh4_read16)
SH4_FAST_READ(uint32_t, sh4_read32)
SH4_FAST_WRITE(uint8_t, sh4_write8)
SH4_FAST_WRITE(uint16_t, sh4_write16)
SH4_FAST_WRITE(uint32_t, sh4_write32)
#define sh4_read8(c, a) sh4_read8_inline(c, a)
#define sh4_read16(c, a) sh4_read16_inline(c, a)
#define sh4_read32(c, a) sh4_read32_inline(c, a)
#define sh4_write8(c, a, v) sh4_write8_inline(c, a, v)
#define sh4_write16(c, a, v) sh4_write16_inline(c, a, v)
#define sh4_write32(c, a, v) sh4_write32_inline(c, a, v)
#endif

/* ========== Interrupt delivery ==========
 *
 * Statically recompiled code has no exception path. A game's IRQ vector at
 * VBR+0x600 is typically a trampoline copied into low RAM at boot, so the
 * recompiler never sees it and the handler behind it is never reached.
 *
 * Instead the game registers its dispatcher here, and dcrecomp calls it from
 * a safe point between memory accesses at ~60Hz. Without this, any game that
 * waits on a counter incremented by its VBlank ISR deadlocks during init.
 *
 * Pass NULL to disable dispatch; VBlank is still raised in SB_ISTNRM either
 * way, so code that polls the register instead of using interrupts still works.
 */
void sh4_set_irq_handler(void (*handler)(SH4CPU *cpu));

/* Raise VBlank on a ~60Hz wall-clock boundary and, if one is registered,
 * run the game's IRQ handler. Called automatically from sh4_read32; exposed
 * so a host main loop can also pump it explicitly. */
void sh4_poll_irq(SH4CPU *cpu);
void sh4_sr_written(SH4CPU *cpu, uint32_t old_sr);
void sh4_sr_restored(SH4CPU *cpu, uint32_t old_sr);   /* RTE: bank switch only */
/* Nombre de RTE exécutés, et la destination et la pile du dernier. */
extern uint32_t sh4_rte_count, sh4_rte_pc, sh4_rte_sp;
void sh4_set_irq_nesting(int on);
void sh4_set_accurate_p4(int on);
int sh4_irq_level(void);
void sh4_set_intevt(uint32_t code);
/* Crazy Taxi's write guards around its allocator table (0x0C1733A4..B3 and
 * 0x0C2FB7A4). Off unless its bootstrap asks: other games have their own
 * data there. */
void sh4_set_legacy_write_guards(int on);
/* Enter the game's own exception vector the way the SH-4 does. */
void sh4_take_exception(SH4CPU *cpu, uint32_t vector);
/* Appelé à l'entrée de chaque exception, SGR déjà posé (les tâches du jeu y
 * notent laquelle l'a prise : pal-prototype/threads.c). */
extern void (*sh4_on_exception)(SH4CPU *cpu);
/* Stack pointer loads (not adjustments) in code translated with --sp-loads.
 * Interrupts leave it as they found it. */
extern uint32_t sh4_sp_loads;
/* VRAM through its 32-bit area (0x05000000, where framebuffers and the
 * region array live): the two 4MB banks alternate every 32-bit word in the
 * 64-bit view that the texture area and cpu->vram use, bit 22 picking the
 * bank. Stored linearly, Kamui's region array (written at 32-bit +0x568000
 * by Grandia II) landed on top of a texture in the 64-bit view. */
static inline uint32_t dc_vram32(uint32_t off) {
    return ((off & 0x003FFFFCu) << 1) | ((off >> 20) & 4u) | (off & 3u);
}
/* Code translated with --relocatable adds this to what it computes from its
 * own address (MOVA, literals read at run time). Zero, except while the
 * dispatcher runs a translation on behalf of a copy of it elsewhere in RAM:
 * Crazy Taxi's boot stub copies itself, runs from the copy and erases the
 * original, so its PC-relative data is only intact in the copy. */
extern uint32_t sh4_reloc;
/* Native resume points at calls to setjmp-like routines, in code translated
 * with --setjmp-sites; the host provides them (pal-prototype/threads.c). */
int phantom_ctx_save(void *ctx) __attribute__((returns_twice));
void *phantom_setjmp_slot(SH4CPU *cpu);

/* Print a native backtrace. Recompiled functions are real C functions, so
 * this is the SH-4 call chain - unlike cpu->pr, which only moves on
 * indirect branches. Needs debug info to resolve names. */
void sh4_dump_native_stack(const char *tag);

/* Print the native call chain on a crash instead of dying silently. */
void sh4_install_crash_handler(void);

/* SR T-bit helpers */
static inline bool sh4_get_t(SH4CPU *cpu) { return (cpu->sr & SR_T) != 0; }
static inline void sh4_set_t(SH4CPU *cpu, bool v) {
    if (v) cpu->sr |= SR_T; else cpu->sr &= ~SR_T;
}

/* Store Queue prefetch (flush SQ to external memory) */
void sh4_sq_prefetch(SH4CPU *cpu, uint32_t addr);

/* FPSCR helpers */
static inline bool sh4_get_sz(SH4CPU *cpu) { return (cpu->fpscr & FPSCR_SZ) != 0; }
static inline bool sh4_get_pr(SH4CPU *cpu) { return (cpu->fpscr & FPSCR_PR) != 0; }
static inline bool sh4_get_fr(SH4CPU *cpu) { return (cpu->fpscr & FPSCR_FR) != 0; }

#endif /* SH4_CPU_H */
