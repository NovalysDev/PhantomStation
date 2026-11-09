/**
 * SH-4 CPU State Implementation
 *
 * Manages CPU state and memory access for statically recompiled
 * Dreamcast/Naomi games. Memory accesses are routed through this layer
 * to handle the memory map, including mirrored regions and hardware registers.
 */

#include "recompiler/sh4_cpu.h"
#include "hal/dc_hardware.h"
#include "hal/pvr2.h"
#include "platform/platform.h"
#include <stdlib.h>
#include <string.h>
#include <stdio.h>

/* Native backtrace of the recompiled call chain.
 *
 * Recompiled functions are ordinary C functions, so the C stack IS the SH-4
 * call chain. cpu->pr and cpu->pc only move on indirect branches, which makes
 * them useless for locating a stall reached through direct calls - this is not.
 * Needs debug info; without a PDB the frames print as bare addresses. */
#ifdef _WIN32
#include <windows.h>
#include <dbghelp.h>
static SH4CPU *g_cpu_ref;

static LONG WINAPI sh4_crash_filter(EXCEPTION_POINTERS *info) {
    printf("[CRASH] exception 0x%08lX at 0x%p\n",
           (unsigned long)info->ExceptionRecord->ExceptionCode,
           info->ExceptionRecord->ExceptionAddress);
    if (info->ExceptionRecord->NumberParameters >= 2)
        printf("[CRASH] accessing 0x%p\n",
               (void *)info->ExceptionRecord->ExceptionInformation[1]);
    if (g_cpu_ref)
        printf("[CRASH] sh4 pc=0x%08X pr=0x%08X sp=0x%08X sr=0x%08X\n",
               g_cpu_ref->pc, g_cpu_ref->pr, g_cpu_ref->r[15], g_cpu_ref->sr);
    fflush(stdout);
    { /* Walk from the faulting context. */
      CONTEXT ctx = *info->ContextRecord;
      STACKFRAME64 f; memset(&f, 0, sizeof f);
      HANDLE proc = GetCurrentProcess(), thr = GetCurrentThread();
      char nbuf[sizeof(SYMBOL_INFO) + 256];
      SYMBOL_INFO *sym = (SYMBOL_INFO *)nbuf;
      SymSetOptions(SYMOPT_DEFERRED_LOADS | SYMOPT_UNDNAME);
      SymInitialize(proc, NULL, TRUE);
      sym->SizeOfStruct = sizeof(SYMBOL_INFO);
      sym->MaxNameLen = 255;
      f.AddrPC.Offset = ctx.Rip;    f.AddrPC.Mode = AddrModeFlat;
      f.AddrFrame.Offset = ctx.Rbp; f.AddrFrame.Mode = AddrModeFlat;
      f.AddrStack.Offset = ctx.Rsp; f.AddrStack.Mode = AddrModeFlat;
      printf("[CRASH] rip=0x%llX rsp=0x%llX rbp=0x%llX\n",
             (unsigned long long)ctx.Rip, (unsigned long long)ctx.Rsp,
             (unsigned long long)ctx.Rbp);
      printf("[CRASH] stack:");
      for (int i = 0; i < 32; i++) {
          if (!StackWalk64(IMAGE_FILE_MACHINE_AMD64, proc, thr, &f, &ctx,
                           NULL, SymFunctionTableAccess64, SymGetModuleBase64,
                           NULL))
              break;
          if (!f.AddrPC.Offset) break;
          DWORD64 disp = 0;
          if (SymFromAddr(proc, f.AddrPC.Offset, &disp, sym))
              printf(" %s", sym->Name);
          else
              printf(" 0x%llX", (unsigned long long)f.AddrPC.Offset);
      }
      printf("\n"); }
    sh4_dump_native_stack("crash");
    { void dcrecomp_dump_calltrace(int);
      dcrecomp_dump_calltrace(24); }
    fflush(stdout);
    return EXCEPTION_EXECUTE_HANDLER;
}

void sh4_install_crash_handler(void) {
    SetUnhandledExceptionFilter(sh4_crash_filter);
}

void sh4_dump_native_stack(const char *tag) {
    void *frames[40];
    USHORT n = CaptureStackBackTrace(0, 40, frames, NULL);
    HANDLE proc = GetCurrentProcess();
    static int inited = 0;
    if (!inited) {
        SymSetOptions(SYMOPT_DEFERRED_LOADS | SYMOPT_UNDNAME);
        SymInitialize(proc, NULL, TRUE);
        inited = 1;
    }
    char buf[sizeof(SYMBOL_INFO) + 256];
    SYMBOL_INFO *sym = (SYMBOL_INFO *)buf;
    sym->SizeOfStruct = sizeof(SYMBOL_INFO);
    sym->MaxNameLen = 255;
    printf("[STACK %s]", tag);
    for (USHORT i = 0; i < n; i++) {
        DWORD64 disp = 0;
        if (SymFromAddr(proc, (DWORD64)(uintptr_t)frames[i], &disp, sym))
            printf(" %s", sym->Name);
        else
            printf(" 0x%p", frames[i]);
    }
    printf("\n");
}
#else
void sh4_dump_native_stack(const char *tag) { (void)tag; }
#endif

/* Write watchpoint. Set DCRECOMP_WATCH to an address (hex, 0x-prefixed) and
 * every 32-bit write to it prints the value and the recompiled call chain.
 * Answers "which code sets this flag", which grep cannot when the address is
 * register-relative in the generated C. */
static SH4CPU *g_cpu_ref;
static uint64_t g_irq_delivered;
static uint64_t g_irq_masked;
static unsigned g_irq_repeats;
/* Set while every RAM write may skip the out-of-line path: no watchpoint
 * and no legacy guard wants to see it (see SH4_FAST_RAM in sh4_cpu.h). */
int sh4_fast_writes;
static int g_legacy_guards;
static void refresh_fast_writes(void) {
    sh4_fast_writes = !g_legacy_guards && !getenv("DCRECOMP_WATCH") && !getenv("DCRECOMP_WATCH2") &&
                      !getenv("DCRECOMP_WATCHRANGE");
}
static uint32_t g_watch_addr = 0;
static uint32_t g_watch_addr2 = 0;
static int g_watch_init = 0;

static void watch_check(uint32_t addr, uint32_t val) {
    { static int rl = -2; static uint32_t lo, hi;
      if (rl == -2) { const char *e = getenv("DCRECOMP_WATCHRANGE");
          if (e) { lo = (uint32_t)strtoul(e, NULL, 0); hi = lo + 0x20; rl = 1; }
          else rl = 0; }
      if (rl == 1) { uint32_t a = addr & 0x1FFFFFFF;
          if (val && a >= (lo & 0x1FFFFFFF) && a < (hi & 0x1FFFFFFF)) {
              static int n = 0;
              if (++n < 24) {
                  printf("[RANGE] 0x%08X = 0x%08X\n", addr, val);
                  sh4_dump_native_stack("range");
              } } } }
    if (!g_watch_init) {
        const char *e = getenv("DCRECOMP_WATCH");
        g_watch_addr = e ? (uint32_t)strtoul(e, NULL, 0) : 0;
        const char *e2 = getenv("DCRECOMP_WATCH2");
        g_watch_addr2 = e2 ? (uint32_t)strtoul(e2, NULL, 0) : 0;
        g_watch_init = 1;
    }
    uint32_t wa=addr & 0x1fffffffu;
    int hit = (g_watch_addr && wa <= (g_watch_addr & 0x1fffffffu) + 3 && (uint64_t)wa + 3 >= (g_watch_addr & 0x1fffffffu))
           || (g_watch_addr2 && wa <= (g_watch_addr2 & 0x1fffffffu) + 3 && (uint64_t)wa + 3 >= (g_watch_addr2 & 0x1fffffffu));
    if (hit) {
        static unsigned long n = 0;
        n++;
        if (n <= 60 || (n % 5000) == 0) {
            printf("[WATCH] t=%llu irq=%llu masked=%llu sr=%08X write 0x%08X = 0x%08X\n", (unsigned long long)platform_get_ticks_ms(), (unsigned long long)g_irq_delivered, (unsigned long long)g_irq_masked, g_cpu_ref ? g_cpu_ref->sr : 0, addr, val);
            sh4_dump_native_stack("watch");
        }
    }
}

/* External hardware reference (set during init) */
static DCHardware *g_hardware = NULL;
static SH4CPU *g_cpu_ref = NULL;

/* TMU time base: set when TCNTn is written */
static uint64_t tmu_write_time_ms[3] = {0, 0, 0};

/* ========== Interrupt delivery ==========
 *
 * See sh4_cpu.h. The handler is called from sh4_read32, i.e. between the
 * memory accesses of recompiled code rather than between SH-4 instructions.
 * That is close enough to hardware, with one catch: recompiled code keeps all
 * live state in cpu->r[] instead of C locals, so a handler that runs mid
 * sequence would clobber whatever function it interrupted. Real hardware
 * avoids this by banking r0-r7 on interrupt entry. We save and restore the
 * whole architectural register file, which covers the banked half plus r8-r15
 * and the FP state.
 *
 * Deliberately NOT saved: dmac_regs, tmu_regs, sq/qacr, cycles and running.
 * Those are hardware side effects the handler is supposed to leave behind.
 */
typedef struct {
    uint32_t r[16];
    uint32_t pc, pr, sr, gbr, mach, macl, fpul, fpscr;
    float fr[16], xf[16];
} SH4IrqFrame;

static void (*g_irq_handler)(SH4CPU *cpu) = NULL;

/* ---- On-chip timer and interrupt controller (opt-in) ----
 *
 * The lightweight model only counts TCNT down on 32-bit reads, never raises
 * the underflow interrupt, and drops 8/16-bit accesses to the P4 area - TSTR
 * is a byte and TCR a word, so a game could neither start a timer nor see one
 * expire. Katana's one-shot timer waits on exactly that (Grandia II). Off by
 * default: Crazy Taxi was brought up against the lightweight behaviour. */
static int g_accurate_p4 = 0;
static uint16_t g_ipr[3];
static uint32_t g_intevt;
static struct {
    uint8_t tocr, tstr;
    uint32_t tcor[3], tcnt[3];
    uint16_t tcr[3];
    uint64_t last_ns[3], frac[3];
    unsigned refused[3];
} g_tmu;

static int g_irq_level;
int sh4_irq_level(void) { return g_irq_level; }
void sh4_set_intevt(uint32_t code) { g_intevt = code; }

void sh4_set_accurate_p4(int on) {
    g_accurate_p4 = on;
    for (int ch = 0; ch < 3; ch++) { g_tmu.tcor[ch] = g_tmu.tcnt[ch] = 0xFFFFFFFFu; }
}

static uint64_t p4_now_ns(void) {
#ifdef _WIN32
    static LARGE_INTEGER freq;
    LARGE_INTEGER now;
    if (!freq.QuadPart) QueryPerformanceFrequency(&freq);
    QueryPerformanceCounter(&now);
    return (uint64_t)((double)now.QuadPart * 1e9 / (double)freq.QuadPart);
#else
    return platform_get_ticks_ms() * 1000000ull;
#endif
}

/* Peripheral clock 50 MHz; TPSC selects /4 .. /1024. */
static void tmu_update(int ch) {
    static const unsigned div[8] = {4, 16, 64, 256, 1024, 1024, 1024, 1024};
    uint64_t now = p4_now_ns();
    if (!(g_tmu.tstr & (1u << ch))) { g_tmu.last_ns[ch] = now; return; }
    uint64_t num = (now - g_tmu.last_ns[ch]) * 50u + g_tmu.frac[ch];
    uint64_t den = (uint64_t)div[g_tmu.tcr[ch] & 7] * 1000u;
    g_tmu.last_ns[ch] = now;
    uint64_t ticks = num / den;
    g_tmu.frac[ch] = num % den;
    if (ticks <= g_tmu.tcnt[ch]) { g_tmu.tcnt[ch] -= (uint32_t)ticks; return; }
    ticks -= (uint64_t)g_tmu.tcnt[ch] + 1;
    ticks %= (uint64_t)g_tmu.tcor[ch] + 1;
    g_tmu.tcnt[ch] = g_tmu.tcor[ch] - (uint32_t)ticks;
    g_tmu.tcr[ch] |= 0x100;   /* UNF */
}

/* Returns 1 when the access was a register handled here. */
static int p4_access(uint32_t addr, int write, uint32_t *val) {
    { static int trace = -1, n;
      if (trace < 0) trace = getenv("PHANTOM_DC_P4_TRACE") != NULL;
      if (trace && write && addr != 0xFFD8000C && addr != 0xFFD80018 && addr != 0xFFD80024 && n++ < 300)
          printf("[P4] write %08X = %08X\n", addr, *val); }
    if (addr == 0xFF000028) { if (!write) *val = g_intevt; return 1; }
    /* SCIF, the serial port, with nothing plugged in: what is sent goes out
     * at once (SCFSR2 TEND|TDFE, both FIFOs empty) and nothing ever arrives.
     * Reading 0 everywhere left Crazy Taxi's disc loader waiting forever to
     * send its first byte. PHANTOM_DC_SCIF_TRACE prints what a game sends. */
    if (addr >= 0xFFE80000 && addr <= 0xFFE80027) {
        static uint32_t scif[10];
        unsigned reg = (addr - 0xFFE80000) / 4;
        if (write) {
            scif[reg] = *val;
            if (reg == 3) {   /* SCFTDR2 */
                static int trace = -1; static char line[128]; static unsigned len, lines;
                if (trace < 0) trace = getenv("PHANTOM_DC_SCIF_TRACE") != NULL;
                char c = (char)*val;
                if (trace && lines < 200) {
                    if (c != '\n' && c != '\r' && len < sizeof line - 1) line[len++] = c >= 32 ? c : '.';
                    if ((c == '\n' || len == sizeof line - 1) && len) { line[len] = 0; printf("[SCIF] %s\n", line); len = 0; lines++; }
                }
            }
        } else {
            *val = reg == 4 ? 0x0060u                 /* SCFSR2: transmit done, nothing received */
                 : reg == 5 || reg == 7 ? 0u          /* SCFRDR2, SCFDR2: FIFOs empty */
                 : scif[reg];
        }
        return 1;
    }
    if (addr >= 0xFFD00004 && addr <= 0xFFD0000C && !(addr & 3)) {
        uint16_t *r = &g_ipr[(addr - 0xFFD00004) / 4];
        if (write) *r = (uint16_t)*val; else *val = *r;
        return 1;
    }
    if (addr < 0xFFD80000 || addr > 0xFFD8002B) return 0;
    uint32_t off = addr - 0xFFD80000;
    if (off == 0) { if (write) g_tmu.tocr = (uint8_t)*val; else *val = g_tmu.tocr; return 1; }
    if (off == 4) {
        for (int ch = 0; ch < 3; ch++) tmu_update(ch);
        if (write) g_tmu.tstr = (uint8_t)*val; else *val = g_tmu.tstr;
        return 1;
    }
    int ch = (int)(off - 8) / 12, reg = (int)(off - 8) % 12;
    tmu_update(ch);
    if (reg == 0) { if (write) g_tmu.tcor[ch] = *val; else *val = g_tmu.tcor[ch]; }
    else if (reg == 4) { if (write) { g_tmu.tcnt[ch] = *val; g_tmu.frac[ch] = 0; } else *val = g_tmu.tcnt[ch]; }
    else if (reg == 8) {
        if (write) {
            /* UNF can only be cleared by software, never set. */
            g_tmu.tcr[ch] = (uint16_t)((*val & ~0x100u) | (g_tmu.tcr[ch] & *val & 0x100u));
            g_tmu.refused[ch] = 0;
        } else *val = g_tmu.tcr[ch];
    }
    return 1;
}

static void swap_banks(SH4CPU *cpu) {
    uint32_t tmp[8];
    memcpy(tmp, cpu->r, sizeof tmp);
    memcpy(cpu->r, cpu->r_bank, sizeof tmp);
    memcpy(cpu->r_bank, tmp, sizeof tmp);
}

void sh4_call_indirect(SH4CPU *cpu);
static uint64_t g_last_vblank_ms = 0;
static bool g_in_irq = false;
/* Nested delivery, for games whose handler lowers SR.IMASK and then waits on
 * a higher-priority interrupt. Off by default: Crazy Taxi was brought up
 * without it, and turned on for Grandia II it crashed during the logos. The
 * handler is entered with IMASK at the accepted level, which is what the
 * game's own vector stub would have set. */
static int g_irq_nesting = 0, g_irq_depth = 0;
#define IRQ_MAX_DEPTH 4
/* Bit d: the interrupt open at depth d went in through the game's own vector
 * (sh4_take_exception). There SR.BL and SR.IMASK, which the handler manages
 * itself, already say what may nest, exactly as on the SH-4: House of the
 * Dead 2's level-4 dispatcher lowers IMASK to 4 so VBlank (level 6) can
 * interrupt it, and waits for it. Refused, it waited for good. */
static uint32_t g_irq_by_vector = 0;
void sh4_set_irq_nesting(int on) { g_irq_nesting = on; }
/* Per game thread: a thread switched away inside a handler must not leave
 * the next one unable to take interrupts (see pal-prototype/threads.c). */
int sh4_irq_depth(void) { return g_irq_depth; }
void sh4_set_irq_depth(int depth) { g_irq_depth = depth; g_in_irq = depth > 0; }

/* Set when an exception's RTE went somewhere other than the code it
 * interrupted. */
static int g_exception_redirected;
uint32_t sh4_sp_loads;
uint32_t sh4_reloc;

/* SPC/SSR/SGR saved, MD/BL raised and the vector run until its RTE ends the
 * call. RB and the bank swap only with the accurate P4 model, the one place
 * the translated RTE swaps back. The interrupted PC is only the last call
 * target - translated code does not keep one - which is enough to see whether
 * the RTE went back to it. A preemptive kernel patches the saved PC to its
 * own switch routine instead (Grandia II's does), and that has to run before
 * the interrupted code may continue: see exception_continue. */
void (*sh4_on_exception)(SH4CPU *cpu);

void sh4_take_exception(SH4CPU *cpu, uint32_t vector) {
    uint32_t sr = cpu->sr, from = cpu->pc;
    cpu->spc = from; cpu->ssr = sr; cpu->sgr = cpu->r[15];
    if (sh4_on_exception) sh4_on_exception(cpu);
    if (g_accurate_p4 && !(sr & SR_RB)) swap_banks(cpu);
    cpu->sr = sr | SR_MD | SR_BL | (g_accurate_p4 ? SR_RB : 0);
    cpu->pc = vector;
    /* The depth is the one on entry: a thread switch inside the handler
     * changes g_irq_depth (pal-prototype/threads.c) before this returns. */
    int depth = g_irq_depth;
    uint32_t by_vector = g_irq_by_vector & (1u << depth);
    g_irq_by_vector |= 1u << depth;
    static int stats = -1;
    if (stats < 0) stats = getenv("PHANTOM_DC_EXC_STATS") != NULL;
    uint64_t t0 = stats ? p4_now_ns() : 0;
    uint32_t intevt = g_intevt;
    sh4_call_indirect(cpu);
    g_irq_by_vector = (g_irq_by_vector & ~(1u << depth)) | by_vector;
    if (stats) {
        static uint64_t since, spent[64]; static unsigned count[64];
        uint64_t t1 = p4_now_ns(); unsigned k = (intevt >> 5) & 63;
        spent[k] += t1 - t0; count[k]++;
        if (!since) since = t1;
        if (t1 - since >= 2000000000ull) {
            fprintf(stderr, "[EXC-STATS]");
            for (unsigned i = 0; i < 64; i++) if (count[i])
                fprintf(stderr, " %03X:%u x %.1fms", i << 5, count[i], spent[i] / 1e6), count[i] = 0, spent[i] = 0;
            fprintf(stderr, " /2s\n"); since = t1;
        }
    }
    /* Whatever the handler left in bank 1 stays there, as on hardware. When
     * its RTE already switched back, the swap did that. */
    if (g_accurate_p4 && !(sr & SR_RB) && (cpu->sr & SR_RB)) memcpy(cpu->r_bank, cpu->r, sizeof cpu->r_bank);
    if (cpu->pc == cpu->spc && cpu->pc != from) g_exception_redirected = 1;
}

/* Runs once the interrupt bookkeeping is undone, on this host stack and with
 * the state the RTE left. A thread switch inside parks this fiber here
 * (pal-prototype/threads.c); the call returns when the thread is switched
 * back to, and the interrupted code continues as if the exception had just
 * ended. */
static void exception_continue(SH4CPU *cpu) {
    if (!g_exception_redirected) return;
    g_exception_redirected = 0;
    { static int n; if (getenv("PHANTOM_DC_THREAD_TRACE") && n++ < 50)
          fprintf(stderr, "[THREAD] exception returns to %08X sp=%08X\n", cpu->pc, cpu->r[15]); }
    sh4_call_indirect(cpu);
}

/* A timer underflow is taken the way the SH-4 takes it: INTEVT set and the
 * game's own vector at VBR + 0x600 run. If the vector has not been
 * translated yet the call is reported as unresolved, which is what lets
 * learning find it; until then the channel is left alone after a few
 * refusals. */
static int tmu_deliver(SH4CPU *cpu) {
    int best = -1; unsigned prio = 0;
    for (int ch = 0; ch < 3; ch++) {
        tmu_update(ch);
        if ((g_tmu.tcr[ch] & 0x120) != 0x120 || g_tmu.refused[ch] > 8) continue;
        unsigned p = (g_ipr[0] >> (12 - 4 * ch)) & 0xF;
        if (p > prio) { prio = p; best = ch; }
    }
    if (best < 0 || !cpu->vbr || (cpu->sr & SR_BL) || ((cpu->sr & SR_IMASK) >> 4) >= prio) return 0;

    SH4IrqFrame f;
    memcpy(f.r, cpu->r, sizeof f.r);
    f.pc = cpu->pc; f.pr = cpu->pr; f.sr = cpu->sr; f.gbr = cpu->gbr;
    f.mach = cpu->mach; f.macl = cpu->macl; f.fpul = cpu->fpul; f.fpscr = cpu->fpscr;
    memcpy(f.fr, cpu->fr, sizeof f.fr); memcpy(f.xf, cpu->xf, sizeof f.xf);

    g_intevt = 0x400u + 0x20u * (unsigned)best;
    { static int n; if (getenv("PHANTOM_DC_P4_TRACE") && n++ < 50)
          printf("[P4] timer %d interrupt -> vector %08X sr=%08X\n", best, cpu->vbr + 0x600u, cpu->sr); }
    uint32_t sp_loads = sh4_sp_loads;
    g_in_irq = true; g_irq_depth++; g_irq_delivered++;
    g_irq_by_vector &= ~(1u << g_irq_depth);
    sh4_take_exception(cpu, cpu->vbr + 0x600u);
    g_irq_depth--; g_in_irq = g_irq_depth > 0;
    if ((g_tmu.tcr[best] & 0x120) == 0x120) g_tmu.refused[best]++;
    exception_continue(cpu);

    sh4_sp_loads = sp_loads;
    memcpy(cpu->r, f.r, sizeof f.r);
    cpu->pc = f.pc; cpu->pr = f.pr; cpu->sr = f.sr; cpu->gbr = f.gbr;
    cpu->mach = f.mach; cpu->macl = f.macl; cpu->fpul = f.fpul; cpu->fpscr = f.fpscr;
    memcpy(cpu->fr, f.fr, sizeof f.fr); memcpy(cpu->xf, f.xf, sizeof f.xf);
    return 1;
}
static int irq_blocked(void) {
    return g_in_irq && !((g_irq_nesting || (g_irq_by_vector >> g_irq_depth & 1u)) && g_irq_depth < IRQ_MAX_DEPTH);
}
static uint64_t g_irq_delivered = 0;
static uint64_t g_irq_reentrant = 0;   /* skipped because a handler was running */
static uint64_t g_irq_masked = 0;      /* skipped because SR.BL or SR.IMASK blocked it */
static unsigned g_irq_repeats = 0;     /* deliveries since the pending set last emptied */


void sh4_set_irq_handler(void (*handler)(SH4CPU *cpu)) {
    g_irq_handler = handler;
    printf("[IRQ] handler %s\n", handler ? "registered" : "cleared");
}

/* Which IRL level Holly is routing VBlank-IN to, per SB_IML6/4/2NRM.
 * Zero means the game has not routed it anywhere, so nothing should fire. */
static int vblank_irl_level(void) {
    const uint32_t VBL_IN = 1u << 3;
    uint32_t pend = dc_hw_get_istnrm(g_hardware);
    if (!pend)
        return 0;

    /* Whatever the game has routed, at the level it routed it to. VBlank is
     * not special - a DMA completion or an end-of-list the game is waiting on
     * has to reach it too, and waiting for the next VBlank to deliver one is
     * how a transfer that finished instantly still takes a frame. */
    if (pend & dc_hw_read32(g_hardware, SB_IML6NRM)) return 13;
    if (pend & dc_hw_read32(g_hardware, SB_IML4NRM)) return 11;
    if (pend & dc_hw_read32(g_hardware, SB_IML2NRM)) return 9;

    /* External sources - GD-ROM, AICA, modem - have their own status and mask
     * registers and are just as able to be what a game is waiting on. */
    { uint32_t ext = dc_hw_get_istext(g_hardware);
      if (ext) {
          if (ext & dc_hw_read32(g_hardware, SB_IML6EXT)) return 13;
          if (ext & dc_hw_read32(g_hardware, SB_IML4EXT)) return 11;
          if (ext & dc_hw_read32(g_hardware, SB_IML2EXT)) return 9;
      } }
    if (!(pend & VBL_IN))
        return 0;
    /* Holly's three interrupt levels are wired to SH-4 IRL 13, 11 and 9 - not
     * to 6, 4 and 2, which are just what the registers are named after. Getting
     * this wrong matters: a game that masks to IMASK=8 around video setup still
     * expects VBlank, because IRL 13 is above the mask. Treating it as level 6
     * blocked it, and the vblank wait in the middle of the mode change never
     * finished. */
    if (dc_hw_read32(g_hardware, SB_IML6NRM) & VBL_IN) return 13;
    if (dc_hw_read32(g_hardware, SB_IML4NRM) & VBL_IN) return 11;
    if (dc_hw_read32(g_hardware, SB_IML2NRM) & VBL_IN) return 9;

    /* Nothing routed. On hardware the BIOS programs these before handing
     * control to the game, and we bypass the BIOS - Crazy Taxi happens to set
     * them itself, Mushiking never touches them. Treating unrouted as "never
     * deliver" would mean such a game gets no interrupts at all, so assume the
     * mid IRL. The SR.BL and SR.IMASK checks below still do the real work of
     * keeping us out of critical sections. */
    return 11;
}

/* Frames the game is owed for time a device spent that we did not, and the
 * remainder that has not yet added up to one. */
/* The pending set as of the last delivery decision - see the back-off below. */
static uint32_t g_last_pend = 0;

static unsigned g_owed_vblanks = 0;
static uint32_t g_owed_ms = 0;

void sh4_credit_elapsed_ms(uint32_t ms) {
    g_owed_ms += ms;
    g_owed_vblanks += g_owed_ms / 16;
    g_owed_ms %= 16;
    /* A second's worth is more than any single wait needs, and capping keeps a
     * bad length from parking us in the poll loop. */
    if (g_owed_vblanks > 60)
        g_owed_vblanks = 60;
}

uint64_t dc_aica_arm_instructions(void);   /* hal/dc_hardware.c */

void sh4_poll_irq(SH4CPU *cpu) {
    if (!g_hardware) return;
    /* ponytail: no nested interrupts. Hardware allows a handler that lowers
     * SR.IMASK to be interrupted again; we just drop the second one, which
     * costs a frame at worst. If a handler ever needs to wait on a later
     * interrupt, this guard is what deadlocks and this is where to fix it. */
    /* DCRECOMP_STACKMS=N: the recompiled call chain every N ms.
     *
     * Above the re-entrancy guard on purpose. The other samplers are driven
     * by interrupts and by read volume, and this one used to sit below the
     * guard - so all three went blind in the one case worth sampling, a run
     * stuck inside its own interrupt handler. */
    { static unsigned stick = 0; static int ms = -1; static uint64_t next;
      if (ms < 0) { const char *e = getenv("DCRECOMP_STACKMS");
                    ms = e ? atoi(e) : 0; }
      if (ms > 0 && (++stick & 0x0F) == 0) {
          uint64_t now = platform_get_ticks_ms();
          if (now >= next) {
              next = now + (uint64_t)ms;
              printf("[SAMPLE t=%llums arm7=%lluk irq=%llu reent=%llu%s] ",
                     (unsigned long long)now,
                     (unsigned long long)(dc_aica_arm_instructions() / 1000),
                     (unsigned long long)g_irq_delivered,
                     (unsigned long long)g_irq_reentrant,
                     g_in_irq ? " IN-HANDLER" : "");
              sh4_dump_native_stack("sample");
          } } }
    /* The sound processor is a separate chip and keeps running while the SH-4
     * is inside an interrupt handler, so step it above the re-entrancy guard.
     * Below it, the driver stopped dead for the whole of every handler and lost
     * most of its execution: ChuChu Rocket's spends enough time in there to
     * hold the driver to a fraction of its clock, and the driver counts Timer A
     * interrupts to sequence its own initialisation.
     *
     * These read the clock, which is the expensive part of a poll, so moving
     * them behind the tick gate below is tempting. It is wrong: it makes the
     * sound processor's response thirty-two times less prompt, and what the
     * sound path races is latency, not throughput. */
    /* DCRECOMP_TEST_POLL_WATCH=addr[,addr...]: report when a RAM word changes,
     * whatever wrote it. The store watch only sees the CPU; this also catches
     * DMA and host-side copies, at the cost of only knowing the poll it
     * happened by. DCRECOMP_TEST_POLL_WATCH_FROM_MS starts it late. */
    { static int pw = -1; static uint32_t pw_addr[8], pw_last[8]; static uint64_t pw_from;
      if (pw < 0) { const char *e = getenv("DCRECOMP_TEST_POLL_WATCH"), *f = getenv("DCRECOMP_TEST_POLL_WATCH_FROM_MS");
                    pw = 0; pw_from = f ? strtoull(f, NULL, 0) : 0;
                    while (e && *e && pw < 8) { char *end; pw_addr[pw++] = (uint32_t)strtoul(e, &end, 0) & 0x00fffffcu;
                                                e = *end == ',' ? end + 1 : NULL; } }
      if (pw && cpu->ram && (!pw_from || platform_get_ticks_ms() >= pw_from))
          for (int i = 0; i < pw; i++) {
              uint32_t v; memcpy(&v, cpu->ram + pw_addr[i], 4);
              if (v != pw_last[i]) {
                  printf("[POLL-WATCH] t=%llums %08X: %08X -> %08X pc=%08X pr=%08X\n",
                         (unsigned long long)platform_get_ticks_ms(), pw_addr[i] | 0x8c000000u,
                         pw_last[i], v, cpu->pc, cpu->pr);
                  pw_last[i] = v;
              } } }
    /* The same for video memory: PHANTOM_DC_VRAM_WATCH=offset,... (offsets in
     * the 64-bit texture area, as a TCW addresses it). */
    { static int vw = -1; static uint32_t vw_off[8], vw_last[8];
      if (vw < 0) { const char *e = getenv("PHANTOM_DC_VRAM_WATCH"); vw = 0;
                    while (e && *e && vw < 8) { char *end; vw_off[vw++] = (uint32_t)strtoul(e, &end, 0) & 0x007ffffcu;
                                                e = *end == ',' ? end + 1 : NULL; } }
      if (vw && cpu->vram)
          for (int i = 0; i < vw; i++) {
              uint32_t v; memcpy(&v, cpu->vram + vw_off[i], 4);
              if (v != vw_last[i]) {
                  printf("[VRAM-WATCH] t=%llums +%06X: %08X -> %08X pc=%08X pr=%08X\n",
                         (unsigned long long)platform_get_ticks_ms(), vw_off[i], vw_last[i], v, cpu->pc, cpu->pr);
                  vw_last[i] = v;
              } } }
    dc_g2_retire_finished(g_hardware);
    dc_aica_update(g_hardware);   /* steps the sound processor */

    if (irq_blocked()) { g_irq_reentrant++; return; }

    /* Raising is on the clock; delivering is not. With nothing pending the
     * only question is whether 16ms have passed, and asking costs a clock read
     * - so ask at the old rate. With something pending the question is whether
     * the game has unmasked, which needs no clock and wants asking every time:
     * a game masks and unmasks around every list it shares with its handler,
     * so it is unmasked for only a small part of the time and a coarse check
     * lands inside a critical section almost every time. */
    /* Raising VBlank is on the clock, and the clock is the expensive part, so
     * ask at the old rate. It has to be asked unconditionally: we raise bits a
     * game may never route or clear - a DMA completion it does not use, say -
     * and gating the clock on "nothing pending" would let one of those stop
     * VBlank for good. */

    { static unsigned tick = 0;
      if ((++tick & 0x1F) == 0) {
          /* DCRECOMP_VBLANK_POLLS=N raises VBlank every N polls instead of
           * every 16ms.
           *
           * The wall clock is the default and gives the right frame rate, but
           * it makes the machine non-repeatable: the SH-4 is compiled and
           * unpaced, so how much game work falls between two VBlanks depends
           * on host load, and a game whose behaviour turns on a race between
           * an interrupt and its own progress takes a different path every
           * run. ChuChu Rocket's tone bank load is such a race, and nothing
           * tunes while the input keeps moving. Counting polls makes runs
           * repeatable, at the cost of tying frame pace to host speed - for
           * bisecting a race, not for playing. One poll is 256 accesses. */
          static int vpolls = -1;
          if (vpolls < 0) { const char *e = getenv("DCRECOMP_VBLANK_POLLS");
                            vpolls = e ? atoi(e) : 0; }
          if (vpolls > 0) {
              static unsigned since;
              if (++since >= (unsigned)vpolls) { since = 0; dc_pvr_wait_vblank(g_hardware); }
          } else {
          uint64_t now = platform_get_ticks_ms();
          if (g_last_vblank_ms == 0) g_last_vblank_ms = now;
          if (now - g_last_vblank_ms >= 16) {  /* ~60Hz */
              g_last_vblank_ms = now;
              dc_pvr_wait_vblank(g_hardware);  /* raises SB_ISTNRM VBlank-IN */
          } else if (g_owed_vblanks && !g_in_irq && now - g_last_vblank_ms >= 4) {
              /* Les tops dus (temps d'une lecture du disque que nous faisons
               * d'un coup) sont espacés d'au moins 4 ms et jamais envoyés
               * pendant une interruption : envoyés en rafale (5 en 3 ms), l'un
               * tombait dans le traitement du précédent et la logique de dessin
               * de House of the Dead 2 se figeait, une partie sur quatre. */
              g_owed_vblanks--;
              g_last_vblank_ms = now;
              dc_pvr_wait_vblank(g_hardware);
          }
          }
      } }
    if (g_accurate_p4 && tmu_deliver(cpu)) return;
    if (!g_irq_handler) return;

    /* Respect the CPU's own masking. Delivering into a critical section the
     * game had closed leaves it re-entered halfway through its own bookkeeping,
     * which is exactly as broken as it sounds. SR.BL blocks everything; SR.IMASK
     * blocks any level at or below it. The interrupt stays pending and gets
     * delivered on a later poll. */
    /* Delivering is free - no clock - so it happens on every check. What we
     * are usually waiting for is not time passing but the game leaving a
     * critical section, and it enters and leaves those constantly. */
    int level = vblank_irl_level();
    if (level == 0) { g_irq_masked++; g_irq_repeats = 0; return; }
    if (cpu->sr & SR_BL) { g_irq_masked++; return; }
    if ((int)((cpu->sr & SR_IMASK) >> 4) >= level) {
        g_irq_masked++;
        /* Asked once: this runs on every masked poll, a few hundred thousand
         * times a second, and getenv walks the whole environment. */
        static int ignore = -1;
        if (ignore < 0) ignore = getenv("DCRECOMP_IGNORE_IMASK") != NULL;
        if (!ignore) return;
    }

    /* A bit nothing ever clears would otherwise be delivered on every check
     * forever, so deliveries that leave something still pending back off to
     * one in sixty-four.
     *
     * The back-off has to be keyed on *which* bits are pending, not just on
     * whether any are. Resetting only when the pending set empties means a
     * single stuck bit - a DMA completion the game does not route, say - holds
     * the counter up for good, and then everything else is throttled with it,
     * including whatever the game is actually waiting for. That cost ChuChu
     * Rocket its tone bank: the sound library reuses one descriptor for the
     * driver and the sample bank, and the release that frees it comes from an
     * interrupt. Throttled, that interrupt arrived 451ms late, the bank load
     * found the descriptor still busy, and sound init failed.
     *
     * So: any change in the pending set is new work and gets delivered at
     * once. Only an unchanging set backs off. */
    { uint32_t pend_now = dc_hw_get_istnrm(g_hardware) ^ dc_hw_get_istext(g_hardware);
      if (pend_now != g_last_pend) { g_last_pend = pend_now; g_irq_repeats = 0; } }
    if (g_irq_repeats > 8 && (g_irq_repeats & 0x3F))
        { g_irq_repeats++; return; }
    g_irq_repeats++;

    SH4IrqFrame f;
    memcpy(f.r, cpu->r, sizeof f.r);
    f.pc = cpu->pc; f.pr = cpu->pr; f.sr = cpu->sr; f.gbr = cpu->gbr;
    f.mach = cpu->mach; f.macl = cpu->macl;
    f.fpul = cpu->fpul; f.fpscr = cpu->fpscr;
    memcpy(f.fr, cpu->fr, sizeof f.fr);
    memcpy(f.xf, cpu->xf, sizeof f.xf);

    /* Where is the game actually spending its time? Set DCRECOMP_STACKEVERY=N
     * and every Nth interrupt prints the recompiled call chain it interrupted.
     * A game that has stopped making progress shows the same chain every time. */
    { static int every = -1, n = 0;
      if (every < 0) { const char *e = getenv("DCRECOMP_STACKEVERY");
                       every = e ? atoi(e) : 0; }
      if (every > 0 && (++n % every) == 0) sh4_dump_native_stack("irq"); }

    uint32_t sp_loads = sh4_sp_loads;
    g_irq_level = level;
    g_in_irq = true; g_irq_depth++;
    g_irq_by_vector &= ~(1u << g_irq_depth);
    if (g_irq_nesting)
        cpu->sr = (cpu->sr & ~(SR_IMASK | SR_BL)) | ((uint32_t)level << 4) | SR_MD;
    g_irq_delivered++;
    g_irq_handler(cpu);
    g_irq_depth--; g_in_irq = g_irq_depth > 0;
    exception_continue(cpu);

    sh4_sp_loads = sp_loads;
    memcpy(cpu->r, f.r, sizeof f.r);
    cpu->pc = f.pc; cpu->pr = f.pr; cpu->sr = f.sr; cpu->gbr = f.gbr;
    cpu->mach = f.mach; cpu->macl = f.macl;
    cpu->fpul = f.fpul; cpu->fpscr = f.fpscr;
    memcpy(cpu->fr, f.fr, sizeof f.fr);
    memcpy(cpu->xf, f.xf, sizeof f.xf);
}

/* LDC Rm,SR in code translated with --irq-on-sr. Lowering SR.IMASK or
 * clearing SR.BL with something pending is where the SH-4 takes it. Polling
 * only on memory accesses misses it when the unmasked stretch has none -
 * Grandia II retries a full DMA queue with interrupts masked inside the call
 * and unmasked only in the caller's few register-only instructions, so the
 * completion interrupt that frees the queue was never delivered. */
/* RTE: the bank switch, but nothing delivered yet. When the RTE ends a
 * thread switch the registers are already the next thread's while this host
 * stack is still the old one's, until the dispatcher sees the new stack and
 * changes fibers. An interrupt taken in between ran on the wrong fiber;
 * Grandia II's task scheduler then saved its context there and later replayed
 * a finished switch. What is pending is taken at the next memory access. */
uint32_t sh4_rte_count, sh4_rte_pc, sh4_rte_sp;

void sh4_sr_restored(SH4CPU *cpu, uint32_t old_sr) {
    /* Le dernier RTE vraiment exécuté : où il est allé, sur quelle pile. */
    sh4_rte_count++; sh4_rte_pc = cpu->pc; sh4_rte_sp = cpu->r[15];
    if (g_accurate_p4 && ((old_sr ^ cpu->sr) & SR_RB)) swap_banks(cpu);
}

void sh4_sr_written(SH4CPU *cpu, uint32_t old_sr) {
    /* SR.RB selects which bank r0-r7 name; the translated code always uses
     * cpu->r, so a change of bank is a swap with the other one. */
    if (g_accurate_p4 && ((old_sr ^ cpu->sr) & SR_RB)) swap_banks(cpu);
    if (irq_blocked() || !g_hardware) return;
    int unmasked = (cpu->sr & SR_IMASK) < (old_sr & SR_IMASK) ||
                   ((old_sr & SR_BL) && !(cpu->sr & SR_BL));
    if (unmasked && (dc_hw_get_istnrm(g_hardware) | dc_hw_get_istext(g_hardware)))
        sh4_poll_irq(cpu);
}

DCHardware *sh4_get_hardware(void) {
    return g_hardware;
}

void sh4_set_hardware(DCHardware *hw) {
    g_hardware = hw;
}

void sh4_set_cpu_ref(SH4CPU *cpu) {
    g_cpu_ref = cpu;
}

uint8_t *sh4_get_ram_ptr(void) {
    return g_cpu_ref ? g_cpu_ref->ram : NULL;
}

uint8_t *sh4_get_vram_ptr(void) {
    return g_cpu_ref ? g_cpu_ref->vram : NULL;
}

uint32_t sh4_get_vram_capacity(void) {
    static int reserve = -1;
    if (reserve < 0) reserve = getenv("PHANTOM_DC_TEXTURE_RESERVE") != NULL;
    return reserve ? 2u * DC_VRAM_SIZE : DC_VRAM_SIZE;
}

uint8_t *sh4_get_aica_ram_ptr(void) {
    return g_cpu_ref ? g_cpu_ref->aica_ram : NULL;
}

/* Words the sound driver would have published. Overriding the read rather than
 * writing sound RAM keeps the value out of reach of the DMA that uploads the
 * driver, which lands on top of it. */
#define AICA_PUBLISHED_MAX 8
static struct { uint32_t offset, value, after_ms; } g_aica_published[AICA_PUBLISHED_MAX];
static int g_aica_published_n = 0;

/* When the sound processor was let go, and how long the driver takes to have
 * something to say. A hundred milliseconds is six frames: long enough for the
 * game to go round its wait loop with interrupts open, which is what it is
 * really there for, and short enough that nobody notices. */
static uint64_t g_aica_arm_released_ms = 0;

#define STUBBED_MAX 16
static struct { uint32_t addr, value; } g_stubbed[STUBBED_MAX];
static int g_stubbed_n = 0;

void sh4_stub_function(uint32_t addr, uint32_t value) {
    if (g_stubbed_n >= STUBBED_MAX)
        return;
    g_stubbed[g_stubbed_n].addr = addr & 0x1FFFFFFF;
    g_stubbed[g_stubbed_n].value = value;
    g_stubbed_n++;
    printf("[STUB] func_%08X answered with %u instead of run\n", addr, value);
}

bool sh4_stubbed_function(uint32_t addr, uint32_t *value) {
    addr &= 0x1FFFFFFF;
    for (int i = 0; i < g_stubbed_n; i++)
        if (g_stubbed[i].addr == addr) {
            *value = g_stubbed[i].value;
            return true;
        }
    return false;
}

/* The pair a register number names: even is this bank, odd is the other. */
static float *fmov_pair(SH4CPU *cpu, int n) {
    return (n & 1) ? &cpu->xf[n & 0xE] : &cpu->fr[n & 0xE];
}

void sh4_fmov_reg(SH4CPU *cpu, int n, int m) {
    if (cpu->fpscr & FPSCR_SZ) {
        float *d = fmov_pair(cpu, n), *s = fmov_pair(cpu, m);
        d[0] = s[0];
        d[1] = s[1];
    } else {
        cpu->fr[n] = cpu->fr[m];
    }
}

void sh4_fmov_load(SH4CPU *cpu, int n, uint32_t addr) {
    if (cpu->fpscr & FPSCR_SZ) {
        float *d = fmov_pair(cpu, n);
        d[0] = sh4_read_float(cpu, addr);
        d[1] = sh4_read_float(cpu, addr + 4);
    } else {
        cpu->fr[n] = sh4_read_float(cpu, addr);
    }
}

void sh4_fmov_store(SH4CPU *cpu, int m, uint32_t addr) {
    if (cpu->fpscr & FPSCR_SZ) {
        float *s = fmov_pair(cpu, m);
        sh4_write_float(cpu, addr, s[0]);
        sh4_write_float(cpu, addr + 4, s[1]);
    } else {
        sh4_write_float(cpu, addr, cpu->fr[m]);
    }
}

void sh4_fmov_load_inc(SH4CPU *cpu, int n, int m) {
    uint32_t addr = cpu->r[m];
    sh4_fmov_load(cpu, n, addr);
    cpu->r[m] += (cpu->fpscr & FPSCR_SZ) ? 8 : 4;
}

void sh4_fmov_store_dec(SH4CPU *cpu, int m, int n) {
    cpu->r[n] -= (cpu->fpscr & FPSCR_SZ) ? 8 : 4;
    sh4_fmov_store(cpu, m, cpu->r[n]);
}

/* DIV1 Rm,Rn, straight out of the SH-4 manual. Q and M live in SR; each step
 * shifts the dividend left through T, then adds or subtracts the divisor
 * depending on the previous Q and on M, and recomputes Q from whether that
 * carried. T ends up as (Q == M), which is what the next step shifts in. */
void sh4_div1(SH4CPU *cpu, int n, int m) {
    uint32_t divisor = cpu->r[m];
    unsigned old_q = (cpu->sr & SR_Q) ? 1u : 0u;
    unsigned mbit  = (cpu->sr & SR_M) ? 1u : 0u;
    unsigned q     = (cpu->r[n] & 0x80000000u) ? 1u : 0u;
    unsigned carry;
    uint32_t before;

    cpu->r[n] = (cpu->r[n] << 1) | ((cpu->sr & SR_T) ? 1u : 0u);

    before = cpu->r[n];
    if (old_q == mbit) {
        cpu->r[n] -= divisor;
        carry = (cpu->r[n] > before) ? 1u : 0u;
    } else {
        cpu->r[n] += divisor;
        carry = (cpu->r[n] < before) ? 1u : 0u;
    }

    /* The manual writes the new Q as four cases on (old_q, M, Q). Written out,
     * the dependence on old_q cancels and what is left is Q ^ carry ^ M. */
    q = q ^ carry ^ mbit;

    if (q) cpu->sr |= SR_Q; else cpu->sr &= ~SR_Q;
    if (q == mbit) cpu->sr |= SR_T; else cpu->sr &= ~SR_T;
}

void sh4_frchg(SH4CPU *cpu) {
    for (int i = 0; i < 16; i++) {
        float t = cpu->fr[i];
        cpu->fr[i] = cpu->xf[i];
        cpu->xf[i] = t;
    }
    cpu->fpscr ^= FPSCR_FR;
}

void sh4_aica_arm_released(void) {
    g_aica_arm_released_ms = platform_get_ticks_ms();
    if (!g_aica_arm_released_ms)
        g_aica_arm_released_ms = 1;
}

static int aica_driver_up_for(uint32_t ms) {
    return g_aica_arm_released_ms &&
           platform_get_ticks_ms() - g_aica_arm_released_ms >= ms;
}

void sh4_aica_publish(uint32_t offset, uint32_t value, uint32_t after_ms) {
    if (g_aica_published_n >= AICA_PUBLISHED_MAX)
        return;
    g_aica_published[g_aica_published_n].offset = offset;
    g_aica_published[g_aica_published_n].value = value;
    g_aica_published[g_aica_published_n].after_ms = after_ms;
    g_aica_published_n++;
    printf("[AICA] sound RAM +0x%X reads as 0x%08X, %ums after the processor "
           "starts (driver stub)\n", offset, value, after_ms);
}

uint32_t sh4_get_ram_size(void) {
    return g_cpu_ref ? g_cpu_ref->ram_size : 0;
}

uint32_t sh4_get_ram_mask(void) {
    return g_cpu_ref ? g_cpu_ref->ram_mask : 0;
}

uint32_t sh4_get_dmac_reg(int idx) {
    if (!g_cpu_ref || idx < 0 || idx >= 17) return 0;
    return g_cpu_ref->dmac_regs[idx];
}

void sh4_init_ex(SH4CPU *cpu, uint32_t ram_size) {
    sh4_install_crash_handler();
    memset(cpu, 0, sizeof(SH4CPU));
    refresh_fast_writes();

    /* Set RAM size and mask */
    cpu->ram_size = ram_size;
    cpu->ram_mask = ram_size - 1;

    /* Allocate memory regions */
    cpu->ram = (uint8_t *)calloc(1, ram_size);
    cpu->vram = (uint8_t *)calloc(1, sh4_get_vram_capacity());
    cpu->aica_ram = (uint8_t *)calloc(1, DC_AICA_SIZE);

    if (!cpu->ram || !cpu->vram || !cpu->aica_ram) {
        fprintf(stderr, "FATAL: Failed to allocate memory (RAM=%uMB)\n",
                ram_size / (1024 * 1024));
        exit(1);
    }

    printf("[SH4] Allocated %uMB RAM, %uMB VRAM, %uMB AICA\n",
           ram_size / (1024 * 1024),
           sh4_get_vram_capacity() / (1024 * 1024),
           DC_AICA_SIZE / (1024 * 1024));

    sh4_reset(cpu);
}

void sh4_init(SH4CPU *cpu) {
    sh4_init_ex(cpu, DC_RAM_SIZE);
}

void sh4_reset(SH4CPU *cpu) {
    /* Reset registers to power-on defaults */
    memset(cpu->r, 0, sizeof(cpu->r));
    memset(cpu->r_bank, 0, sizeof(cpu->r_bank));

    cpu->sr = 0x700000F0;   /* MD=1, RB=1, BL=1, IMASK=0xF */
    cpu->gbr = 0;
    cpu->vbr = 0;
    cpu->mach = 0;
    cpu->macl = 0;
    cpu->pr = 0;
    cpu->pc = GAME_LOAD_ADDR;

    /* FPU defaults */
    cpu->fpscr = 0x00040001; /* DN=1, RM=nearest */

    /* QACR defaults: route SQ0/SQ1 to TA FIFO area (0x10000000) */
    cpu->qacr[0] = 0x10;
    cpu->qacr[1] = 0x10;
    cpu->fpul = 0;
    memset(cpu->fr, 0, sizeof(cpu->fr));
    memset(cpu->xf, 0, sizeof(cpu->xf));

    /* Stack pointer */
    cpu->r[15] = 0x8C00F400;

    cpu->running = true;
    cpu->cycles = 0;
    cpu->delay_slot = 0;

    /* MMU starts disabled */
    cpu->mmucr = 0;

    printf("[MMU] Initialized (heuristic P0/P3 -> RAM mapping active)\n");
}

void sh4_destroy(SH4CPU *cpu) {
    free(cpu->ram);
    free(cpu->vram);
    free(cpu->aica_ram);
    cpu->ram = NULL;
    cpu->vram = NULL;
    cpu->aica_ram = NULL;
}

/* ========== Memory Access ========== */

static uint32_t translate_addr_cpu(SH4CPU *cpu, uint32_t addr) {
    /* P2 area (0xA0000000-0xBFFFFFFF): kernel, uncached */
    if (addr >= 0xA0000000 && addr < 0xC0000000) {
        return addr & ADDR_MASK_P1;
    }

    /* P1 area (0x80000000-0x9FFFFFFF): kernel, cached */
    if (addr >= 0x80000000 && addr < 0xA0000000) {
        uint32_t phys = addr & ADDR_MASK_P1;
        if (phys < 0x01000000) return phys;
        if (phys >= DC_VRAM_BASE && phys < DC_VRAM_BASE + DC_VRAM_SIZE) return phys;
        if (phys >= 0x05000000 && phys < 0x05800000) return phys;
        if (phys >= DC_RAM_BASE && phys < DC_RAM_BASE + cpu->ram_size) return phys;
        if (phys >= 0x005F6800 && phys < 0x005FA000) return phys;
        return DC_RAM_BASE + (addr & cpu->ram_mask);
    }

    /* P4 area (0xE0000000-0xFFFFFFFF): on-chip resources */
    if (addr >= 0xE0000000) {
        return 0xFFFFFFFF;
    }

    /* P0/U0 and P3: use TLB if MMU enabled */
    if (cpu && (cpu->mmucr & 1)) {
        for (int i = 0; i < 64; i++) {
            uint32_t entry_addr = cpu->utlb_addr[i];
            if (!(entry_addr & 0x100)) continue;

            uint32_t entry_data = cpu->utlb_data1[i];
            int sz = ((entry_data >> 6) & 2) | ((entry_data >> 4) & 1);
            uint32_t page_mask;
            switch (sz) {
            case 0: page_mask = 0xFFFFFC00; break;
            case 1: page_mask = 0xFFFFF000; break;
            case 2: page_mask = 0xFFFF0000; break;
            case 3: page_mask = 0xFFF00000; break;
            default: page_mask = 0xFFF00000; break;
            }

            uint32_t vpn = entry_addr & page_mask;
            if ((addr & page_mask) == vpn) {
                uint32_t ppn = entry_data & 0x1FFFFC00;
                uint32_t offset = addr & ~page_mask;
                return ppn | offset;
            }
        }
    }

    /* Direct map with heuristics */
    uint32_t raw_phys = addr & ADDR_MASK_P1;
    if (raw_phys < 0x01000000) return raw_phys;
    if (raw_phys >= DC_VRAM_BASE && raw_phys < DC_VRAM_BASE + DC_VRAM_SIZE)
        return raw_phys;
    if (raw_phys >= 0x05000000 && raw_phys < 0x05800000)
        return raw_phys;
    if (raw_phys >= DC_RAM_BASE && raw_phys < DC_RAM_BASE + (cpu ? cpu->ram_size : DC_RAM_SIZE))
        return raw_phys;

    return DC_RAM_BASE + (addr & (cpu ? cpu->ram_mask : DC_RAM_MASK_16MB));
}

static uint32_t translate_addr(uint32_t addr) {
    return translate_addr_cpu(g_cpu_ref, addr);
}

static bool is_hw_register(uint32_t phys_addr) {
    if (phys_addr >= 0x005F6800 && phys_addr < 0x005FA000)
        return true;
    if (phys_addr >= 0x10000000 && phys_addr < 0x10800000)
        return true;
    if (phys_addr >= 0x10800000 && phys_addr < 0x11000000)
        return true;
    return false;
}

/* Memory traffic is what advances time here: every 256th access polls for
 * interrupts, ticks the 60Hz clock and runs the sound processor. */
uint32_t sh4_read_seq = 0;   /* also counted by the inline path */
#define g_read_seq sh4_read_seq

/* Un accès de 8 ou 16 bits aux registres de la puce son : la trace dit si un
 * jeu s'en sert, ce qui était invisible quand ces accès tombaient dans le vide. */
static void aica_narrow_trace(SH4CPU *cpu, uint32_t off, int bits, int ecrit) {
    static int on = -1, n;
    if (on < 0) on = getenv("DCRECOMP_AICA_WIDTH") != NULL;
    if (on && n++ < 16)
        printf("[AICA] %s %d bits at +%04X pc=%08X\n",
               ecrit ? "write" : "read", bits, off & 0x7FFFu, cpu->pc);
}

uint8_t sh4_read8(SH4CPU *cpu, uint32_t addr) {
    if ((++g_read_seq & 0xFF) == 0) sh4_poll_irq(cpu);
    if (g_accurate_p4 && addr >= 0xFF000000) { uint32_t v; if (p4_access(addr, 0, &v)) return (uint8_t)v; }
    uint32_t phys = translate_addr(addr);

    if (phys >= DC_RAM_BASE && phys < DC_RAM_BASE + cpu->ram_size) {
        return cpu->ram[phys & cpu->ram_mask];
    }
    if (phys >= DC_VRAM_BASE && phys < DC_VRAM_BASE + DC_VRAM_SIZE) {
        return cpu->vram[phys - DC_VRAM_BASE];
    }
    if (phys >= 0x05000000 && phys < 0x05800000) {
        return cpu->vram[dc_vram32(phys - 0x05000000)];
    }
    if (phys >= DC_AICA_BASE && phys < DC_AICA_BASE + DC_AICA_SIZE) {
        dc_aica_bus_reads++;
        return cpu->aica_ram[phys - DC_AICA_BASE];
    }
    if (phys >= 0x00700000 && phys < 0x00710000) {
        /* Les registres de la puce son font 16 bits : un pilote qui les lit
         * avec mov.b / mov.w doit voir la même chose que par mov.l. */
        uint32_t off = phys - 0x00700000;
        dc_aica_access_width = 8;
        aica_narrow_trace(cpu, off, 8, 0);
        return dc_aica_reg_read8(off);
    }
    if (is_hw_register(phys) && g_hardware) {
        uint32_t aligned = phys & ~3;
        uint32_t val = dc_hw_read32(g_hardware, aligned);
        int shift = (phys & 3) * 8;
        return (val >> shift) & 0xFF;
    }
    return 0;
}

uint16_t sh4_read16(SH4CPU *cpu, uint32_t addr) {
    if ((++g_read_seq & 0xFF) == 0) sh4_poll_irq(cpu);
    if (g_accurate_p4 && addr >= 0xFF000000) { uint32_t v; if (p4_access(addr, 0, &v)) return (uint16_t)v; }
    uint32_t phys = translate_addr(addr);

    if (phys >= DC_RAM_BASE && phys < DC_RAM_BASE + cpu->ram_size) {
        return *(uint16_t *)(cpu->ram + (phys & cpu->ram_mask));
    }
    if (phys >= DC_VRAM_BASE && phys < DC_VRAM_BASE + DC_VRAM_SIZE) {
        return *(uint16_t *)(cpu->vram + (phys - DC_VRAM_BASE));
    }
    if (phys >= 0x05000000 && phys < 0x05800000) {
        return *(uint16_t *)(cpu->vram + dc_vram32(phys - 0x05000000));
    }
    if (phys >= DC_AICA_BASE && phys < DC_AICA_BASE + DC_AICA_SIZE) {
        dc_aica_bus_reads++;
        return *(uint16_t *)(cpu->aica_ram + (phys - DC_AICA_BASE));
    }
    if (phys >= 0x00700000 && phys < 0x00710000) {
        uint32_t off = phys - 0x00700000;
        dc_aica_access_width = 16;
        aica_narrow_trace(cpu, off, 16, 0);
        return (uint16_t)(dc_aica_reg_read(off & ~3u) >> ((off & 2) * 8));
    }
    if (is_hw_register(phys) && g_hardware) {
        uint32_t aligned = phys & ~3;
        uint32_t val = dc_hw_read32(g_hardware, aligned);
        int shift = (phys & 2) * 8;
        return (val >> shift) & 0xFFFF;
    }
    return 0;
}


uint32_t sh4_read32(SH4CPU *cpu, uint32_t addr) {
    /* P4 control registers */
    if (addr >= 0xFF000000) {
        if (g_accurate_p4) { uint32_t v; if (p4_access(addr, 0, &v)) return v; }
        switch (addr) {
        case 0xFF000010: return cpu->mmucr;
        case 0xFF000038: return cpu->qacr[0];
        case 0xFF00003C: return cpu->qacr[1];
        }
        if (addr >= 0xFFA00000 && addr <= 0xFFA00040) {
            uint32_t idx = (addr - 0xFFA00000) / 4;
            if (idx < 17) return cpu->dmac_regs[idx];
        }
        if (addr >= 0xFFD80000 && addr <= 0xFFD8002F) {
            /* TMU registers:
             * 0xFFD80000: TOCR, 0xFFD80004: TSTR
             * 0xFFD80008: TCOR0, 0xFFD8000C: TCNT0, 0xFFD80010: TCR0
             * 0xFFD80014: TCOR1, 0xFFD80018: TCNT1, 0xFFD8001C: TCR1
             * 0xFFD80020: TCOR2, 0xFFD80024: TCNT2, 0xFFD80028: TCR2 */
            uint32_t idx = (addr - 0xFFD80000) / 4;
            if (idx < 12) {
                /* For TCNT0/1/2 (indices 3, 6, 9): simulate countdown.
                 * Naomi Pclk = 50MHz. With prescaler /4 (TCR default),
                 * timer ticks at 12.5MHz = 12500 ticks/ms. */
                if (idx == 3 || idx == 6 || idx == 9) {
                    int ch = (idx == 3) ? 0 : (idx == 6) ? 1 : 2;
                    uint64_t now = platform_get_ticks_ms();
                    uint64_t base = tmu_write_time_ms[ch];
                    if (base == 0) base = now; /* first read before any write */
                    uint64_t elapsed_ms = now - base;
                    /* Decrement by elapsed ticks (12500 per ms for ~50MHz/4) */
                    uint64_t ticks64 = elapsed_ms * 12500;
                    uint32_t val = cpu->tmu_regs[idx];
                    if (ticks64 >= (uint64_t)val) {
                        /* Timer underflowed - reload and reset time base */
                        uint32_t reload = cpu->tmu_regs[idx - 1]; /* TCORn */
                        if (reload == 0) reload = 0xFFFFFFFF;
                        cpu->tmu_regs[idx] = reload;
                        tmu_write_time_ms[ch] = now;
                    }
                    /* Return the stored value minus elapsed (computed on the fly) */
                    uint32_t result = cpu->tmu_regs[idx];
                    if (ticks64 < (uint64_t)result) {
                        result -= (uint32_t)ticks64;
                    }
                    return result;
                }
                return cpu->tmu_regs[idx];
            }
        }
        return 0;
    }

    /* UTLB arrays */
    if (addr >= 0xF6000000 && addr < 0xF7000000) {
        return cpu->utlb_addr[(addr >> 8) & 63];
    }
    if (addr >= 0xF7000000 && addr < 0xF7800000) {
        return cpu->utlb_data1[(addr >> 8) & 63];
    }
    if (addr >= 0xF7800000 && addr < 0xF8000000) {
        return cpu->utlb_data2[(addr >> 8) & 63];
    }

    uint32_t phys = translate_addr(addr);

    g_read_seq++;
    /* Check often. sh4_poll_irq consults the clock only when it has to, so
     * the cost of a check with nothing pending is a load and a branch - and
     * the thing we are usually waiting for is not the clock but the game
     * leaving a critical section, which it does and undoes constantly.
     *
     * Every width of read drives this, not just this one. The whole machine's
     * sense of time hangs off it - interrupt delivery, the 60Hz clock, the
     * sound processor's instruction budget - so a loop that happens to poll
     * its flag with a byte read used to stop all three dead. That is what made
     * an interrupt take most of a second to arrive during ChuChu Rocket's
     * sound init, and it is the same starvation Crazy Taxi's init sits in. */
    if ((g_read_seq & 0xFF) == 0) sh4_poll_irq(cpu);
    if ((g_read_seq & 0x3FFFFF) == 0) {
        printf("[HEARTBEAT] t=%llums arm7=%lluk read32 #%uM addr=0x%08X pc=0x%08X pr=0x%08X sr=0x%08X irq=%llu reent=%llu masked=%llu\n",
               (unsigned long long)platform_get_ticks_ms(),
               (unsigned long long)(dc_aica_arm_instructions() / 1000),
               g_read_seq >> 20, addr, cpu->pc, cpu->pr, cpu->sr,
               (unsigned long long)g_irq_delivered,
               (unsigned long long)g_irq_reentrant,
               (unsigned long long)g_irq_masked);
        /* Set DCRECOMP_STACK_TRACE=1 to get the recompiled call chain with
         * each heartbeat. Symbol resolution is slow enough to distort timing,
         * so it stays off unless asked for. */
        static int trace = -1;
        if (trace < 0) trace = getenv("DCRECOMP_STACK_TRACE") ? 1 : 0;
        if (trace) sh4_dump_native_stack("heartbeat");
    }

    if (phys >= DC_RAM_BASE && phys < DC_RAM_BASE + cpu->ram_size) {
        uint32_t value = *(uint32_t *)(cpu->ram + (phys & cpu->ram_mask));
        static int watch_read = -1;
        if (watch_read < 0) watch_read = getenv("DCRECOMP_TEST_WATCH_READ") != NULL;
        if (watch_read && phys == 0x0cffa344u) {
            static uint32_t old = 0xffffffffu;
            if (value != old) {
                fprintf(stderr,"[TEST-WATCH-READ] value=%08X pc=%08X pr=%08X sp=%08X\n",value,cpu->pc,cpu->pr,cpu->r[15]);
                old=value;
            }
        }
        return value;
    }
    if (phys >= DC_VRAM_BASE && phys < DC_VRAM_BASE + DC_VRAM_SIZE) {
        return *(uint32_t *)(cpu->vram + (phys - DC_VRAM_BASE));
    }
    if (phys >= 0x05000000 && phys < 0x05800000) {
        return *(uint32_t *)(cpu->vram + dc_vram32(phys - 0x05000000));
    }
    if (phys >= DC_AICA_BASE && phys < DC_AICA_BASE + DC_AICA_SIZE) {
        uint32_t off = phys - DC_AICA_BASE;
        dc_aica_bus_reads++;
        uint32_t v = *(uint32_t *)(cpu->aica_ram + off);
        /* Only answer while nothing has written the word - if the game puts
         * its own value there, that is the real state and it wins - and only
         * once the driver would have been up to write it. */
        /* Answer once the driver would have got round to this one. The
         * delays are what make it work: a driver clears the requests already
         * queued for it as it starts and only then reports itself ready, so
         * the game's wait for "ready" is time its own handler gets to notice
         * those requests finishing. Announce everything at once and the game
         * walks out of that wait with the work still outstanding. */
        for (int i = 0; i < g_aica_published_n; i++)
            if (g_aica_published[i].offset == off &&
                aica_driver_up_for(g_aica_published[i].after_ms))
                return g_aica_published[i].value;
        /* A word in sound RAM read over and over is the CPU waiting on the
         * sound driver, which runs on a processor we do not have. Naming the
         * address and the value it is stuck on is the first thing anyone
         * needs. Set DCRECOMP_AICAPOLL to see them. */
        { static int dl = -1; if (dl < 0) dl = getenv("DCRECOMP_AICAREADS") ? 1 : 0;
          if (dl && g_in_irq) { static uint32_t seen[64]; static int ns = 0; int k;
            for (k = 0; k < ns; k++) if (seen[k] == off) break;
            if (k == ns && ns < 64) { seen[ns++] = off;
                printf("[AICARD] handler reads +0x%X = 0x%08X\n", off, v); } } }
        static int aicalog = -1;
        if (aicalog < 0) aicalog = getenv("DCRECOMP_AICAPOLL") ? 1 : 0;
        if (aicalog) {
            static uint32_t last = 0; static unsigned n = 0; static int said = 0;
            if (phys == last) {
                if (++n == 20000 && said < 12) {
                    said++;
                    printf("[AICA] 0x%08X read %u times, stuck on 0x%08X\n", phys, n, v);
                }
            } else { last = phys; n = 1; }
        }
        return v;
    }
    if (phys >= 0x00700000 && phys < 0x00710000) {
        dc_aica_access_width = 32;
        uint32_t v = dc_aica_reg_read(phys - 0x00700000);
        dc_aica_bus_reads++;
        { static int dl = -1; if (dl < 0) dl = getenv("DCRECOMP_AICAREADS") ? 1 : 0;
          if (dl) { static uint32_t seen[64]; static int ns = 0; int k;
            for (k = 0; k < ns; k++) if (seen[k] == phys) break;
            if (k == ns && ns < 64) { seen[ns++] = phys;
                printf("[AICAREG] read 0x%08X = 0x%08X%s\n", phys, v,
                       g_in_irq ? "  (in handler)" : ""); } } }
        return v;
    }
    if (is_hw_register(phys) && g_hardware) {
        return dc_hw_read32(g_hardware, phys);
    }
    return 0;
}

float sh4_read_float(SH4CPU *cpu, uint32_t addr) {
    union { uint32_t u; float f; } conv;
    conv.u = sh4_read32(cpu, addr);
    return conv.f;
}

void sh4_set_legacy_write_guards(int on) { g_legacy_guards = on; refresh_fast_writes(); }

/* The store queues take any access size. Only 32-bit writes reached them, and
 * the rest went to area 0: the movie decoder Grandia II runs writes its
 * rebuilt blocks a byte at a time, and half its pictures came out as the
 * leftovers of the previous ones. */
static int sq_write(SH4CPU *cpu, uint32_t addr, const void *val, unsigned size) {
    if (addr < 0xE0000000u || addr > 0xE3FFFFFFu) return 0;
    memcpy((uint8_t *)cpu->sq[(addr >> 5) & 1] + (addr & 31u & ~(size - 1)), val, size);
    return 1;
}

void sh4_write8(SH4CPU *cpu, uint32_t addr, uint8_t val) {
    if (g_accurate_p4 && addr >= 0xFF000000) { uint32_t v = val; if (p4_access(addr, 1, &v)) return; }
    if (sq_write(cpu, addr, &val, 1)) return;
    uint32_t phys = translate_addr(addr);
    watch_check(phys, (uint32_t)val);

    if (phys >= DC_RAM_BASE && phys < DC_RAM_BASE + cpu->ram_size) {
        /* Protect allocator vtable data from byte writes */
        if (g_legacy_guards && phys >= 0x0C1733A4 && phys <= 0x0C1733B3) return;
        cpu->ram[phys & cpu->ram_mask] = val;
        return;
    }
    if (phys >= DC_VRAM_BASE && phys < DC_VRAM_BASE + DC_VRAM_SIZE) {
        cpu->vram[phys - DC_VRAM_BASE] = val;
        return;
    }
    if (phys >= 0x05000000 && phys < 0x05800000) {
        cpu->vram[dc_vram32(phys - 0x05000000)] = val;
        return;
    }
    if (phys >= DC_AICA_BASE && phys < DC_AICA_BASE + DC_AICA_SIZE) {
        cpu->aica_ram[phys - DC_AICA_BASE] = val;
        return;
    }
    if (phys >= 0x00700000 && phys < 0x00710000) {
        uint32_t off = phys - 0x00700000, aligned = off & ~3u;
        int shift = (off & 3) * 8;
        aica_narrow_trace(cpu, off, 8, 1);
        uint32_t cur = dc_aica_reg_peek(aligned);
        cur = (cur & ~(0xFFu << shift)) | ((uint32_t)val << shift);
        dc_aica_reg_write(aligned, cur);
        return;
    }
    if (is_hw_register(phys) && g_hardware) {
        uint32_t aligned = phys & ~3;
        uint32_t cur = dc_hw_read32(g_hardware, aligned);
        int shift = (phys & 3) * 8;
        cur &= ~(0xFF << shift);
        cur |= (uint32_t)val << shift;
        dc_hw_write32(g_hardware, aligned, cur);
    }
}

void sh4_write16(SH4CPU *cpu, uint32_t addr, uint16_t val) {
    if (g_accurate_p4 && addr >= 0xFF000000) { uint32_t v = val; if (p4_access(addr, 1, &v)) return; }
    if (sq_write(cpu, addr, &val, 2)) return;
    uint32_t phys = translate_addr(addr);
    watch_check(phys, (uint32_t)val);

    if (phys >= DC_RAM_BASE && phys < DC_RAM_BASE + cpu->ram_size) {
        *(uint16_t *)(cpu->ram + (phys & cpu->ram_mask)) = val;
        return;
    }
    if (phys >= DC_VRAM_BASE && phys < DC_VRAM_BASE + DC_VRAM_SIZE) {
        *(uint16_t *)(cpu->vram + (phys - DC_VRAM_BASE)) = val;
        return;
    }
    if (phys >= 0x05000000 && phys < 0x05800000) {
        *(uint16_t *)(cpu->vram + dc_vram32(phys - 0x05000000)) = val;
        return;
    }
    if (phys >= DC_AICA_BASE && phys < DC_AICA_BASE + DC_AICA_SIZE) {
        *(uint16_t *)(cpu->aica_ram + (phys - DC_AICA_BASE)) = val;
        return;
    }
    if (phys >= 0x00700000 && phys < 0x00710000) {
        /* Une écriture de 16 bits ne remplace que sa moitié du registre ;
         * la lire d'abord avec les effets de bord effacerait le bit LP de
         * 0x2810 au passage. */
        uint32_t off = phys - 0x00700000, aligned = off & ~3u;
        int shift = (off & 2) * 8;
        aica_narrow_trace(cpu, off, 16, 1);
        uint32_t cur = dc_aica_reg_peek(aligned);
        cur = (cur & ~(0xFFFFu << shift)) | ((uint32_t)val << shift);
        dc_aica_reg_write(aligned, cur);
        return;
    }
    if (is_hw_register(phys) && g_hardware) {
        uint32_t aligned = phys & ~3;
        uint32_t cur = dc_hw_read32(g_hardware, aligned);
        int shift = (phys & 2) * 8;
        cur &= ~(0xFFFF << shift);
        cur |= (uint32_t)val << shift;
        dc_hw_write32(g_hardware, aligned, cur);
    }
}

uint32_t g_write_seq = 0;

void sh4_write32(SH4CPU *cpu, uint32_t addr, uint32_t val) {
    g_write_seq++;
    if (g_accurate_p4 && addr >= 0xFF000000) { uint32_t v = val; if (p4_access(addr, 1, &v)) return; }

    /* Handle ALL P4 area (0xE0000000+) BEFORE address translation */
    if (addr >= 0xE0000000) {
        /* Store Queue writes (0xE0000000-0xE3FFFFFF) */
        if (addr <= 0xE3FFFFFF) {
            int sq_idx = (addr >> 5) & 1;
            int word_idx = (addr >> 2) & 7;
            cpu->sq[sq_idx][word_idx] = val;
            { static int on = -1, good, bad;
              if (on < 0) on = getenv("PHANTOM_DC_SQ_TCW_TRACE") != NULL;
              if (on && word_idx == 3 && cpu->sq[sq_idx][0] == 0x808C000Eu) {
                  if (val == 0x1E000000u && good++ < 3) sh4_dump_native_stack("sample");
                  if (val == 0x04000000u && bad++ < 3) sh4_dump_native_stack("sample");
              } }
            return;
        }

        /* UTLB Address Array writes */
        if (addr >= 0xF6000000 && addr < 0xF7000000) {
            int entry = (addr >> 8) & 63;
            cpu->utlb_addr[entry] = val;
            static int utlb_log = 0;
            if (utlb_log < 20) {
                utlb_log++;
                printf("[UTLB] addr[%d] = 0x%08X (VPN=0x%08X V=%d)\n",
                       entry, val, val & 0xFFFFFC00, (val >> 8) & 1);
            }
            return;
        }
        /* UTLB Data Array 1 writes */
        if (addr >= 0xF7000000 && addr < 0xF7800000) {
            int entry = (addr >> 8) & 63;
            cpu->utlb_data1[entry] = val;
            static int utlb_d1_log = 0;
            if (utlb_d1_log < 20) {
                utlb_d1_log++;
                int sz = ((val >> 6) & 2) | ((val >> 4) & 1);
                static const char *sz_names[] = {"1KB", "4KB", "64KB", "1MB"};
                printf("[UTLB] data1[%d] = 0x%08X (PPN=0x%08X sz=%s)\n",
                       entry, val, val & 0x1FFFFC00, sz_names[sz]);
            }
            return;
        }
        /* UTLB Data Array 2 writes */
        if (addr >= 0xF7800000 && addr < 0xF8000000) {
            cpu->utlb_data2[(addr >> 8) & 63] = val;
            return;
        }

        /* P4 control registers (0xFF000000+) */
        if (addr >= 0xFF000000) {
            switch (addr) {
            case 0xFF000010: {
                int old_at = cpu->mmucr & 1;
                int new_at = val & 1;
                cpu->mmucr = val;
                if (val & 4) {
                    for (int i = 0; i < 64; i++)
                        cpu->utlb_addr[i] &= ~0x100;
                    cpu->mmucr &= ~4;
                    printf("[MMU] TLB invalidated\n");
                }
                if (old_at != new_at) {
                    printf("[MMU] Address translation %s (MMUCR=0x%08X seq=%u)\n",
                           new_at ? "ENABLED" : "DISABLED", cpu->mmucr, g_write_seq);
                }
                return;
            }
            case 0xFF000038: cpu->qacr[0] = val; return;
            case 0xFF00003C: cpu->qacr[1] = val; return;
            }
            if (addr >= 0xFFA00000 && addr <= 0xFFA00040) {
                uint32_t idx = (addr - 0xFFA00000) / 4;
                if (idx < 17) cpu->dmac_regs[idx] = val;
                return;
            }
            if (addr >= 0xFFD80000 && addr <= 0xFFD8002F) {
                uint32_t idx = (addr - 0xFFD80000) / 4;
                if (idx < 12) {
                    cpu->tmu_regs[idx] = val;
                    /* Reset time base when TCNTn is written */
                    if (idx == 3 || idx == 6 || idx == 9) {
                        int ch = (idx == 3) ? 0 : (idx == 6) ? 1 : 2;
                        tmu_write_time_ms[ch] = platform_get_ticks_ms();
                    }
                }
                return;
            }
            return;
        }
        return;
    }

    /* Normal address translation */
    uint32_t phys = translate_addr(addr);
    watch_check(phys, val);

    if (phys >= DC_RAM_BASE && phys < DC_RAM_BASE + cpu->ram_size) {
        /* Protect allocator vtable data (4 function pointers at 0x0C1733A4-0x0C1733B0)
         * These get corrupted by memset/memfill operations during init.
         * The ROM values are the correct function pointers. */
        if (g_legacy_guards && phys >= 0x0C1733A4 && phys <= 0x0C1733B0) {
            return; /* Block all writes to vtable data region */
        }
        /* Protect allocator vtable pointer */
        if (g_legacy_guards && phys == 0x0C2FB7A4) {
            uint32_t cur = *(uint32_t *)(cpu->ram + (phys & cpu->ram_mask));
            if (cur == 0x8C1733A4 && val != 0x8C1733A4 && val != 0 && val != 0xFFFFFFFF) {
                return;
            }
        }
        *(uint32_t *)(cpu->ram + (phys & cpu->ram_mask)) = val;
        return;
    }
    if (phys >= DC_VRAM_BASE && phys < DC_VRAM_BASE + DC_VRAM_SIZE) {
        { static int w = -1; static uint32_t lo, hi; static int n;
          if (w < 0) { const char *e = getenv("PHANTOM_DC_VRAM_WRITES"); w = e != NULL; if (e) { lo = (uint32_t)strtoul(e, (char **)&e, 0); hi = *e == ',' ? (uint32_t)strtoul(e + 1, NULL, 0) : lo + 4; } }
          if (w && phys - DC_VRAM_BASE >= lo && phys - DC_VRAM_BASE < hi && n++ < 30)
              printf("[VRAM-WRITE] zone 64 bits +%06X = %08X pc=%08X pr=%08X\n", phys - DC_VRAM_BASE, val, cpu->pc, cpu->pr); }
        *(uint32_t *)(cpu->vram + (phys - DC_VRAM_BASE)) = val;
        return;
    }
    if (phys >= 0x05000000 && phys < 0x05800000) {
        { static int w = -1; static uint32_t lo, hi; static int n;
          if (w < 0) { const char *e = getenv("PHANTOM_DC_VRAM_WRITES"); w = e != NULL; if (e) { lo = (uint32_t)strtoul(e, (char **)&e, 0); hi = *e == ',' ? (uint32_t)strtoul(e + 1, NULL, 0) : lo + 4; } }
          if (w && phys - 0x05000000 >= lo && phys - 0x05000000 < hi && n++ < 30)
              printf("[VRAM-WRITE] zone 32 bits +%06X = %08X pc=%08X pr=%08X\n", phys - 0x05000000, val, cpu->pc, cpu->pr); }
        *(uint32_t *)(cpu->vram + dc_vram32(phys - 0x05000000)) = val;
        return;
    }
    if (phys >= DC_AICA_BASE && phys < DC_AICA_BASE + DC_AICA_SIZE) {
        *(uint32_t *)(cpu->aica_ram + (phys - DC_AICA_BASE)) = val;
        return;
    }
    if (phys >= 0x00700000 && phys < 0x00710000) {
        dc_aica_reg_write(phys - 0x00700000, val);
        return;
    }
    if (is_hw_register(phys) && g_hardware) {
        /* Log the first write to each distinct register. "Which hardware has
         * the game actually programmed" is otherwise invisible - and a game
         * that shows nothing has usually not programmed the part you assume. */
        static int hwlog = -1;
        if (hwlog < 0) hwlog = getenv("DCRECOMP_HWREG") ? 1 : 0;
        if (hwlog) { static uint32_t seen[512]; static int nseen = 0; int i;
          for (i = 0; i < nseen; i++) if (seen[i] == phys) break;
          if (i == nseen && nseen < 512) {
              seen[nseen++] = phys;
              printf("[HWREG] first write 0x%08X = 0x%08X\n", phys, val);
          } }
        dc_hw_write32(g_hardware, phys, val);
    }
}

void sh4_write_float(SH4CPU *cpu, uint32_t addr, float val) {
    union { float f; uint32_t u; } conv;
    conv.f = val;
    sh4_write32(cpu, addr, conv.u);
}

/* ========== Store Queue Prefetch ========== */

static int sq_log_count = 0;

void sh4_sq_prefetch(SH4CPU *cpu, uint32_t addr) {
    if (addr < 0xE0000000 || addr > 0xE3FFFFFF) return;

    /* A store queue burst goes wherever QACR says, and QACR is a register the
     * game sets. Zero sends the whole burst to the bottom of area 0, which is
     * nothing - and since the store queue is how a game feeds the tile
     * accelerator, a game whose QACR is wrong renders nothing and looks like a
     * dozen other faults. Say so once. */
    { static unsigned long n = 0, lost = 0; static int said = 0;
      uint32_t q = cpu->qacr[(addr >> 5) & 1];
      uint32_t d = (((q >> 2) & 7) << 26) | (addr & 0x03FFFFE0);
      n++;
      if (d < 0x00100000) lost++;
      if (!said && n >= 2000 && lost * 2 > n) {
          said = 1;
          printf("[SQ] %lu of %lu store queue bursts are going nowhere: QACR "
                 "is 0x%X/0x%X, so the destination computes to 0x%08X. "
                 "Geometry written to the store queue is being discarded.\n",
                 lost, n, cpu->qacr[0], cpu->qacr[1], d);
      } }

    if (sq_log_count < 5) {
        sq_log_count++;
        printf("[SQ] prefetch addr=0x%08X qacr0=0x%X qacr1=0x%X\n",
               addr, cpu->qacr[0], cpu->qacr[1]);
    }

    int sq_idx = (addr >> 5) & 1;
    uint32_t qacr = cpu->qacr[sq_idx];
    uint32_t dest = (((qacr >> 2) & 7) << 26) | (addr & 0x03FFFFE0);

    if (dest >= 0x10000000 && dest < 0x10800000) {
        pvr2_ta_write(cpu->sq[sq_idx]);
    } else if (dest >= DC_VRAM_BASE && dest < DC_VRAM_BASE + DC_VRAM_SIZE) {
        uint32_t offset = dest - DC_VRAM_BASE;
        if (offset + 32 <= DC_VRAM_SIZE) {
            memcpy(cpu->vram + offset, cpu->sq[sq_idx], 32);
        }
    } else {
        for (int i = 0; i < 8; i++) {
            sh4_write32(cpu, dest + i * 4, cpu->sq[sq_idx][i]);
        }
    }
}
