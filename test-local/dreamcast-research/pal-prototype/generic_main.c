/* Local research bootstrap: deliberately does not assume Crazy Taxi addresses. */
#include "recompiler/sh4_cpu.h"
#include "hal/dc_hardware.h"
#include "hal/dc_bios.h"
#include "hal/dc_vmu.h"
#include "hal/dc_ralentis.h"
#include "hal/pvr2.h"
#include "platform/platform.h"
#include "game/game_functions.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <windows.h>
#include <shellapi.h>

int pal_chd_open_stream(FILE *stream);
int pal_chd_root_file(const char *name, unsigned char *dest, uint32_t capacity);
void phantom_learn_set_boot(uint32_t size);
/* Written by the compiler from what it found on this disc (src/game). */
extern const uint32_t phantom_profile_irq;
extern const unsigned phantom_profile_flags;
#define PROFILE_TA_BUFFER 1u
#define PROFILE_IRQ_NESTING 2u
#define PROFILE_THREADS 4u
#define PROFILE_ACCURATE_P4 8u
#define PROFILE_EXCEPTIONS 16u
void phantom_threads_enable(void);

static uint32_t test_irq_address, test_gd_address, test_adx_address;

void (*phantom_learned_function(SH4CPU *cpu, uint32_t addr))(SH4CPU *cpu);
void phantom_unresolved(SH4CPU *cpu);

/* Holly's three interrupt outputs reach the SH-4 as IRL 9, 11 and 13:
 * priority 6, 4 and 2, INTEVT 0x320, 0x360 and 0x3A0. The poller ranks them
 * as 13/11/9 for its own masking; this restores what the game sees. */
static int exception_irq(SH4CPU *cpu) {
    int level = sh4_irq_level();
    unsigned prio = level == 13 ? 6 : level == 11 ? 4 : 2;
    uint32_t intevt = level == 13 ? 0x320u : level == 11 ? 0x360u : 0x3A0u;
    uint32_t vector = cpu->vbr + 0x600u;
    if (!cpu->vbr || !phantom_learned_function(cpu, vector)) {
        /* Not installed or not translated yet: say so, so learning picks it
         * up, and fall back to the library's dispatcher. */
        if (cpu->vbr) { uint32_t pc = cpu->pc; cpu->pc = vector; phantom_unresolved(cpu); cpu->pc = pc; }
        return 0;
    }
    if (((cpu->sr & SR_IMASK) >> 4) >= prio) return 1;
    { static unsigned long n[3]; static int trace = -1;
      if (trace < 0) trace = getenv("PHANTOM_DC_P4_TRACE") != NULL;
      int k = level == 13 ? 0 : level == 11 ? 1 : 2;
      if (trace && (++n[k] & 0x3FF) == 1)
          fprintf(stderr, "[EXC] %lu x INTEVT %03X istnrm=%08X iml6=%08X iml4=%08X iml2=%08X\n", n[k], intevt,
                  dc_hw_get_istnrm(sh4_get_hardware()), sh4_read32(cpu, 0xA05F6930), sh4_read32(cpu, 0xA05F6920),
                  sh4_read32(cpu, 0xA05F6910)); }
    sh4_set_intevt(intevt);
    sh4_take_exception(cpu, vector);
    return 1;
}

static int g_exception_mode;
/* No interrupt routine was recognised on the disc: take the exception the way
 * the SH-4 does and enter the game's own vector, VBR + 0x600.
 * Quand le compilateur a choisi les exceptions par le vecteur du jeu, c'est le
 * chemin complet (exception_irq) : INTEVT, masque IMASK, banques de registres,
 * imbrication. Ce raccourci ne posait pas INTEVT : Dead or Alive 2, dont le
 * gestionnaire lit INTEVT pour savoir quelle interruption arrive, ne
 * reconnaissait rien, et sa première interruption ne se terminait jamais. */
static void vbr_irq(SH4CPU *cpu) {
    if (!cpu->vbr) return;
    if (g_exception_mode && exception_irq(cpu)) return;
    cpu->spc = cpu->pc; cpu->ssr = cpu->sr; cpu->sgr = cpu->r[15];
    cpu->sr |= SR_MD | SR_BL;
    cpu->pc = cpu->vbr + 0x600u;
    sh4_call_indirect(cpu);
}
static void test_irq(SH4CPU *cpu) {
    if (g_exception_mode && exception_irq(cpu)) return;
    { static uint32_t vbr_seen; if (cpu->vbr != vbr_seen && getenv("PHANTOM_DC_P4_TRACE")) {
          vbr_seen = cpu->vbr; fprintf(stderr, "[GENERIC] VBR %08X\n", vbr_seen); } }
    { static int dumped; const char *path = getenv("PHANTOM_DC_DUMP_VBR");
      if (path && !dumped && platform_get_ticks_ms() > 10000 && cpu->vbr) {
          FILE *f = fopen(path, "wb"); dumped = 1;
          if (f) { fwrite(cpu->ram + ((cpu->vbr & 0x00ffffffu)), 1, 0x1000, f); fclose(f); } } }
    uint32_t regs[16], pr=cpu->pr, sr=cpu->sr;
    memcpy(regs,cpu->r,sizeof regs);
    cpu->pc = test_irq_address;
    sh4_call_indirect(cpu);
    if (test_gd_address) {
        memcpy(cpu->r,regs,sizeof regs); cpu->pr=pr; cpu->sr=sr;
        cpu->pc=test_gd_address; sh4_call_indirect(cpu);
    }
    if (test_adx_address) {
        memcpy(cpu->r,regs,sizeof regs); cpu->pr=pr; cpu->sr=sr;
        cpu->pc=test_adx_address; sh4_call_indirect(cpu);
    }
}

/* PHANTOM_DC_WATCHDOG=ms: a host thread that stops the game thread now and
 * then and prints where it is. The in-engine samplers only run on guest
 * memory accesses, so a loop without any is otherwise invisible. */
#include <dbghelp.h>
static HANDLE g_game_thread;
static const SH4CPU *g_watch_cpu;
static DWORD WINAPI watchdog(void *param) {
    DWORD ms = (DWORD)(uintptr_t)param;
    HANDLE process = GetCurrentProcess();
    /* PHANTOM_DC_WATCHDOG_FROM_MS : commencer tard (mesurer l'action, pas le
     * démarrage). PHANTOM_DC_WATCHDOG_PROFILE=1 : la pile native seulement,
     * pour compter où part le temps. Tout s'affiche une fois le jeu reparti :
     * afficher pendant qu'il est suspendu bloquait les deux s'il tenait la
     * console. Les symboles se chargent après l'attente : sur un programme de
     * 75 Mo, les charger au lancement prenait le calcul du démarrage du jeu. */
    const char *from = getenv("PHANTOM_DC_WATCHDOG_FROM_MS");
    int profile = getenv("PHANTOM_DC_WATCHDOG_PROFILE") != NULL;
    if (from) Sleep((DWORD)strtoul(from, NULL, 0));
    SymInitialize(process, NULL, TRUE);
    for (;;) {
        Sleep(ms);
        uint64_t pcs[24]; int n = 0;
        if (SuspendThread(g_game_thread) == (DWORD)-1) continue;
        CONTEXT ctx = {0}; ctx.ContextFlags = CONTEXT_FULL;
        if (GetThreadContext(g_game_thread, &ctx)) {
            STACKFRAME64 f = {0};
            f.AddrPC.Offset = ctx.Rip; f.AddrPC.Mode = AddrModeFlat;
            f.AddrStack.Offset = ctx.Rsp; f.AddrStack.Mode = AddrModeFlat;
            f.AddrFrame.Offset = ctx.Rbp; f.AddrFrame.Mode = AddrModeFlat;
            while (n < 24 && StackWalk64(IMAGE_FILE_MACHINE_AMD64, process, g_game_thread, &f, &ctx,
                                         NULL, SymFunctionTableAccess64, SymGetModuleBase64, NULL))
                pcs[n++] = f.AddrPC.Offset;
        }
        SH4CPU c = {0};
        if (g_watch_cpu && !profile) memcpy(&c, g_watch_cpu, sizeof c);
        ResumeThread(g_game_thread);
        fprintf(stderr, "[WATCHDOG]");
        for (int i = 0; i < n; i++) fprintf(stderr, " 0x%016llX", (unsigned long long)pcs[i]);
        fprintf(stderr, "\n");
        if (profile) continue;
        void dcrecomp_dump_calltrace(int count);
        dcrecomp_dump_calltrace(12);
        if (g_watch_cpu)
            fprintf(stderr, "[WATCHDOG-CPU] pc=%08X pr=%08X sr=%08X r0=%08X r4=%08X r10=%08X r11=%08X r12=%08X r13=%08X r14=%08X r15=%08X\n",
                    c.pc, c.pr, c.sr, c.r[0], c.r[4], c.r[10], c.r[11], c.r[12], c.r[13], c.r[14], c.r[15]);
    }
    return 0;
}

/* Atelier : PHANTOM_DC_AICA_RAM=fichier garde la mémoire de la puce son à la fin. */
static const SH4CPU *g_cpu_sortie;
static void garder_memoire_son(void) {
    const char *f = getenv("PHANTOM_DC_AICA_RAM");
    if (!f || !g_cpu_sortie || !g_cpu_sortie->aica_ram) return;
    FILE *o = fopen(f, "wb"); if (o) { fwrite(g_cpu_sortie->aica_ram, 1, DC_AICA_SIZE, o); fclose(o); }
}

int main(int argc, char **argv) {
    { const char *w = getenv("PHANTOM_DC_WATCHDOG");
      if (w && atoi(w) > 0 && DuplicateHandle(GetCurrentProcess(), GetCurrentThread(), GetCurrentProcess(),
                                              &g_game_thread, 0, FALSE, DUPLICATE_SAME_ACCESS))
          CreateThread(NULL, 0, watchdog, (void *)(uintptr_t)atoi(w), 0, NULL); }
    if (argc == 2 && !strcmp(argv[1], "--test-runtime")) {
        puts("Dreamcast generic runtime available (game compatibility unverified).");
        return 0;
    }
    if (argc != 3) { fprintf(stderr, "usage: generic boot.bin disc.chd\n"); return 2; }
    /* A stalled game is killed from outside; buffered output would lose the
     * diagnostics that say where. */
    setvbuf(stdout, NULL, _IONBF, 0);
    int wide_argc = 0;
    wchar_t **wide = CommandLineToArgvW(GetCommandLineW(), &wide_argc);
    if (!wide || wide_argc != 3) return 2;
    FILE *boot = _wfopen(wide[1], L"rb"), *disc = _wfopen(wide[2], L"rb");
    LocalFree(wide);
    if (!boot || !disc || pal_chd_open_stream(disc)) { fprintf(stderr,"disc unavailable\n"); return 1; }
    SH4CPU cpu;
    g_watch_cpu = &cpu;
    sh4_init_ex(&cpu, DC_RAM_SIZE_16MB);
    g_cpu_sortie = &cpu; atexit(garder_memoire_son);
    dc_ralentis_surveiller(&cpu);   /* ralentissements et gels, notés à chaque partie */
    DCHardware *hw = dc_hw_init();
    if (!hw) return 1;
    sh4_set_hardware(hw); sh4_set_cpu_ref(&cpu);
    dc_pvr_init(hw); dc_maple_init(hw); dc_aica_init(hw); dc_gdrom_init(hw);
    if (_wgetenv(L"PHANTOM_DC_VMU") && !dc_vmu_active()) {
        fputs("[VMU] carte memoire indisponible : partie sans sauvegarde.", stderr);
    }
    int driver_size = pal_chd_root_file("AICADRV.BIN", cpu.aica_ram, DC_AICA_SIZE);
    fprintf(stderr,"[GENERIC] optional AICA driver: %d bytes\n",driver_size);
    size_t size = fread(cpu.ram + 0x10000, 1, DC_RAM_SIZE_16MB - 0x10000, boot);
    fclose(boot);
    if (!size) { fprintf(stderr,"empty boot\n"); return 1; }
    phantom_learn_set_boot((uint32_t)size);
    if ((phantom_profile_flags & PROFILE_TA_BUFFER) && !getenv("DCRECOMP_TEST_TA_BUFFER"))
        _putenv("DCRECOMP_TEST_TA_BUFFER=1");
    if (phantom_profile_flags & PROFILE_IRQ_NESTING) sh4_set_irq_nesting(1);
    if (phantom_profile_flags & PROFILE_THREADS) phantom_threads_enable();
    if (phantom_profile_flags & PROFILE_ACCURATE_P4) sh4_set_accurate_p4(1);
    /* Holly interrupts through the game's own vector rather than straight to
     * the library dispatcher: what the game hangs off its vector (Grandia II's
     * server tasks, its preemptive thread switch) only runs that way.
     * PHANTOM_DC_EXCEPTIONS=0/1 overrides what the compiler chose. */
    { const char *e = getenv("PHANTOM_DC_EXCEPTIONS");
      g_exception_mode = e ? atoi(e) != 0 : (phantom_profile_flags & PROFILE_EXCEPTIONS) != 0; }
    cpu.pc=0x8c010000; cpu.r[15]=0x8c00fc00; cpu.vbr=0x8c00f400;
    /* The BIOS starts the game with the stack right under VBR. 0x8C00FC00
     * grew down through VBR+0x100..0x800, where Katana keeps its interrupt
     * handler table - deep calls overwrote Grandia II's timer handler. */
    if (phantom_profile_flags & PROFILE_ACCURATE_P4) cpu.r[15]=0x8c00f400;
    cpu.r[2]=0x8c000000; cpu.sr=0x700000f0; cpu.fpscr=0x00040001; cpu.pr=0;
    sh4_bios_install_vectors(&cpu);
    /* What the boot ROM leaves at 0x8C000070 (SYSINFO): the five system bytes
     * of the factory flash, "00211" on a European unit. Crazy Taxi's 1ST_READ
     * stub tells a retail console from a dev board by the leading "00"; without
     * it the stub waits forever for a debugger on the serial port. */
    memcpy(cpu.ram + 0x70, "00211", 5);
    /* Juste après, en 0x8C000078, le BIOS recopie les réglages système de la
     * flash (date, langue, stéréo, démarrage automatique) : c'est là que les
     * jeux lisent la langue (octet 0x8C00007D). Il y avait 1, l'anglais, en
     * dur : Grandia II et House of the Dead 2 européens restaient en anglais. */
    cpu.ram[0x7d] = (uint8_t)dc_bios_console_language();
    const char *irq = getenv("DCRECOMP_TEST_IRQ_ADDR");
    if (irq && *irq) {
        char *end = NULL;
        unsigned long addr = strtoul(irq, &end, 0);
        if (!end || *end || addr < 0x8c010000u || addr >= 0x8c010000u + size) {
            fprintf(stderr,"[GENERIC] invalid test IRQ address\n"); return 2;
        }
        test_irq_address = (uint32_t)addr;
        const char *gd=getenv("DCRECOMP_TEST_GD_SERVER"), *adx=getenv("DCRECOMP_TEST_ADX_SERVER");
        if (gd) test_gd_address=(uint32_t)strtoul(gd,NULL,0);
        if (adx) test_adx_address=(uint32_t)strtoul(adx,NULL,0);
        sh4_set_irq_handler(test_irq);
        fprintf(stderr,"[GENERIC] test IRQ address %08X\n",test_irq_address);
    } else if (phantom_profile_irq) {
        test_irq_address = phantom_profile_irq;
        sh4_set_irq_handler(test_irq);
        fprintf(stderr,"[GENERIC] disc IRQ routine %08X\n",test_irq_address);
    } else {
        sh4_set_irq_handler(vbr_irq);
        fprintf(stderr,"[GENERIC] IRQ through the game's vector table\n");
    }
    int scale=1; const char *scale_env=getenv("PHANTOM_DC_SCALE");
    if (scale_env) { int n=atoi(scale_env); if (n>=1 && n<=4) scale=n; }
    if (platform_init(640*scale,480*scale,"Dreamcast - expérimental") < 0) return 1;
    int w,h; platform_get_drawable_size(&w,&h);
    pvr2_ta_init(); if (pvr2_render_init(w,h) != 0) return 1;
    fprintf(stderr,"[GENERIC] start pc=%08X bytes=%zu\n",cpu.pc,size);
    sh4_call_indirect(&cpu);
    fprintf(stderr,"[GENERIC] bootstrap returned pc=%08X\n",cpu.pc);
    return 0;
}
