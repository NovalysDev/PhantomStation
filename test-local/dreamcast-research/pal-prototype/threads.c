/* Game threads on host fibers, found from the stack the game switches to.
 *
 * Translated code runs on the host stack, so a guest thread switch - save the
 * registers, load another thread's stack pointer, restore, return into that
 * thread - came back to the native caller of the *old* thread, with the new
 * thread's registers. Everything after it ran on the wrong frames; Grandia II
 * ended up calling null pointers out of whatever its callee-saved registers
 * had been popped from.
 *
 * No kernel is recognised by address. A call that returns with r15 far from
 * where it was is a switch: the running fiber is parked under the stack
 * pointer it called from, which is exactly what the guest restores when it
 * switches back, and the fiber parked under the new stack pointer is resumed.
 * A stack pointer nobody is parked under is a thread running for the first
 * time; it gets a new fiber that starts where the switch returned to.
 * The same holds for a context restored by hand (longjmp and the coroutine
 * libraries built on it) and for a switch made from an interrupt: the
 * exception's RTE then goes to the kernel's switch routine instead of the
 * interrupted code, and that routine runs on the interrupted fiber
 * (sh4_take_exception in dcrecomp), which parks it until it is resumed.
 *
 * A restored context continues after the setjmp that saved it, not after
 * the longjmp that left it. Grandia II's task scheduler reloads a register
 * right after its setjmp, and resuming at the longjmp called address 0xA00.
 * So the compiler turns every call to a setjmp-like routine into a host
 * resume point (--setjmp-sites), and a restore that lands on one jumps back
 * there, on the fiber that saved it. */
#include "recompiler/sh4_cpu.h"
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>

void sh4_call_indirect(SH4CPU *cpu);
int sh4_irq_depth(void);
void sh4_set_irq_depth(int depth);

#define MAX_THREADS 64
#define MAX_RESUME 128
#define SWITCH_DISTANCE 0x200u
#define FIBER_STACK (256u << 20)

typedef struct ResumePoint ResumePoint;

typedef struct {
    void *fiber;
    uint32_t key, start_pc, start_pr;
    int irq_depth, alive;
    uintptr_t parked_at;      /* host frame it waits in, while parked */
    ResumePoint *pending;     /* where to jump once woken */
    uint32_t park_ret;        /* adresse de retour de l'appel où elle s'est arrêtée */
} GuestThread;

/* A guest setjmp: the guest context it saved (stack pointer, return address)
 * and the host one at the call. */
struct ResumePoint {
    _Alignas(16) unsigned char ctx[256];
    uint32_t buf, sp, ret;
    int owner, irq_depth;
    uintptr_t mark;           /* host frame below the one that saved it */
};

static GuestThread g_threads[MAX_THREADS];
static ResumePoint g_resume[MAX_RESUME];
static unsigned g_resume_next;
static int g_count, g_enabled;
static GuestThread *g_current;
static SH4CPU *g_cpu;
static uint32_t g_watch_ret = 1;   /* PHANTOM_DC_SETJMP_WATCH: return address saved in the watched buffer */
static ResumePoint *resume_point(uint32_t sp, uint32_t pc);

/* Dernière exception prise : par quelle tâche, et sur quelle pile (SGR). */
static GuestThread *g_exc_thread;
static uint32_t g_exc_sgr;
static void note_exception(SH4CPU *cpu) { g_exc_thread = g_current; g_exc_sgr = cpu->r[15]; }

void phantom_threads_enable(void) { g_enabled = 1; sh4_on_exception = note_exception; }

/* setjmp/longjmp without unwinding, for the Win64 ABI (buffer in rcx): the
 * C library's longjmp unwinds through SEH and clang's __builtin variant lost
 * the frame on this target. Callee-saved: rbx rbp rdi rsi r12-r15 xmm6-15. */
__attribute__((naked, returns_twice)) int phantom_ctx_save(void *ctx) {
    __asm__ volatile(
        "movq %rbx, 0(%rcx)\n movq %rbp, 8(%rcx)\n movq %rdi, 16(%rcx)\n movq %rsi, 24(%rcx)\n"
        "movq %r12, 32(%rcx)\n movq %r13, 40(%rcx)\n movq %r14, 48(%rcx)\n movq %r15, 56(%rcx)\n"
        "leaq 8(%rsp), %rdx\n movq %rdx, 64(%rcx)\n movq (%rsp), %rdx\n movq %rdx, 72(%rcx)\n"
        "movdqu %xmm6, 80(%rcx)\n movdqu %xmm7, 96(%rcx)\n movdqu %xmm8, 112(%rcx)\n movdqu %xmm9, 128(%rcx)\n"
        "movdqu %xmm10, 144(%rcx)\n movdqu %xmm11, 160(%rcx)\n movdqu %xmm12, 176(%rcx)\n movdqu %xmm13, 192(%rcx)\n"
        "movdqu %xmm14, 208(%rcx)\n movdqu %xmm15, 224(%rcx)\n"
        "xorl %eax, %eax\n ret\n");
}

__attribute__((naked, noreturn)) static void phantom_ctx_jump(void *ctx) {
    __asm__ volatile(
        "movq 0(%rcx), %rbx\n movq 8(%rcx), %rbp\n movq 16(%rcx), %rdi\n movq 24(%rcx), %rsi\n"
        "movq 32(%rcx), %r12\n movq 40(%rcx), %r13\n movq 48(%rcx), %r14\n movq 56(%rcx), %r15\n"
        "movdqu 80(%rcx), %xmm6\n movdqu 96(%rcx), %xmm7\n movdqu 112(%rcx), %xmm8\n movdqu 128(%rcx), %xmm9\n"
        "movdqu 144(%rcx), %xmm10\n movdqu 160(%rcx), %xmm11\n movdqu 176(%rcx), %xmm12\n movdqu 192(%rcx), %xmm13\n"
        "movdqu 208(%rcx), %xmm14\n movdqu 224(%rcx), %xmm15\n"
        "movq 64(%rcx), %rsp\n movl $1, %eax\n jmpq *72(%rcx)\n");
}

static void jump_to(ResumePoint *r) {
    sh4_set_irq_depth(r->irq_depth);
    phantom_ctx_jump(r->ctx);
}

/* Called at a setjmp-like call, before the guest routine fills r4's buffer. */
__attribute__((noinline)) void *phantom_setjmp_slot(SH4CPU *cpu) {
    ResumePoint *r = NULL;
    for (int i = 0; i < MAX_RESUME && !r; i++)
        if (g_resume[i].mark && g_resume[i].buf == cpu->r[4]) r = &g_resume[i];
    /* A new buffer takes a dead point first: one saved deeper on this fiber's
     * stack than we are now (that frame has returned), or on a fiber that has
     * ended. Taking the oldest regardless threw away the one point House of
     * the Dead 2's task scheduler saves once and jumps back to every frame,
     * once its coroutines had done 128 setjmps of their own: the next jump
     * came back as a plain return and the loop ran off the end of its task
     * list into address 0. */
    uintptr_t here = (uintptr_t)__builtin_frame_address(0);
    int me = g_current ? (int)(g_current - g_threads) : 0;
    for (int i = 0; i < MAX_RESUME && !r; i++) {
        ResumePoint *p = &g_resume[i];
        if (!p->mark || (p->owner == me && p->mark < here) || (g_count && !g_threads[p->owner].alive))
            r = p;
    }
    if (!r) r = &g_resume[g_resume_next++ % MAX_RESUME];
    { static int on = -1; static unsigned n; static uint64_t since;
      if (on < 0) on = getenv("PHANTOM_DC_SETJMP_STATS") != NULL;
      if (on) { uint64_t now = GetTickCount64(); n++;
                if (!since) since = now;
                if (now - since >= 2000) { fprintf(stderr, "[SETJMP] %u per s, last from %08X\n", n * 1000 / (unsigned)(now - since), cpu->pr); n = 0; since = now; } } }
    r->buf = cpu->r[4]; r->sp = cpu->r[15]; r->ret = cpu->pr;
    { static int w = -1; static uint32_t wbuf; static int n;
      if (w < 0) { const char *e = getenv("PHANTOM_DC_SETJMP_WATCH"); w = e != NULL; wbuf = e ? (uint32_t)strtoul(e, NULL, 0) : 0; }
      if (w && (cpu->r[4] & 0x1FFFFFFFu) == (wbuf & 0x1FFFFFFFu) && n++ < 40)
          fprintf(stderr, "[SETJMP-WATCH] save buf %08X sp %08X ret %08X slot %d\n", cpu->r[4], cpu->r[15], cpu->pr, (int)(r - g_resume));
      if (w && (cpu->r[4] & 0x1FFFFFFFu) == (wbuf & 0x1FFFFFFFu)) g_watch_ret = cpu->pr; }
    r->owner = g_current ? (int)(g_current - g_threads) : 0;
    r->irq_depth = sh4_irq_depth();
    r->mark = (uintptr_t)__builtin_frame_address(0);
    return r->ctx;
}

static ResumePoint *resume_point(uint32_t sp, uint32_t pc) {
    for (int i = 0; i < MAX_RESUME; i++)
        if (g_resume[i].mark && g_resume[i].sp == sp && g_resume[i].ret == pc) return &g_resume[i];
    return NULL;
}

static void WINAPI thread_main(void *param) {
    GuestThread *t = param;
    SH4CPU *cpu = g_cpu;
    cpu->pc = t->start_pc;
    sh4_call_indirect(cpu);
    /* The thread function returned: on hardware it lands on the return
     * address its creator left, normally the library's exit routine, which
     * switches away for good. */
    fprintf(stderr, "[THREAD] %08X returned; continuing at %08X\n", t->start_pc, t->start_pr);
    if (t->start_pr) { cpu->pc = t->start_pr; sh4_call_indirect(cpu); }
    fprintf(stderr, "[THREAD] %08X has nowhere left to run\n", t->start_pc);
    t->alive = 0;
    for (;;) Sleep(1000);
}

static int fibers_ready(SH4CPU *cpu) {
    if (g_current) return 1;
    void *self = ConvertThreadToFiber(NULL);
    if (!self) return 0;
    g_cpu = cpu;
    g_threads[0].fiber = self; g_threads[0].alive = 1; g_count = 1;
    g_current = &g_threads[0];
    return 1;
}

/* Park the running fiber under sp0 and run next. Returns when someone
 * switches back, the guest having restored this thread's registers. */
static void switch_to(GuestThread *next, uint32_t sp0) {
    GuestThread *self = g_current;
    self->key = sp0;
    self->irq_depth = sh4_irq_depth();
    self->parked_at = (uintptr_t)__builtin_frame_address(0);
    next->key = 0;
    g_current = next;
    sh4_set_irq_depth(next->irq_depth);
    SwitchToFiber(next->fiber);
    g_current = self;
    self->key = 0; self->parked_at = 0;
    sh4_set_irq_depth(self->irq_depth);
    if (self->pending) { ResumePoint *r = self->pending; self->pending = NULL; jump_to(r); }
}

/* The switch is seen by the innermost call that came back on another stack,
 * which can sit a few words below the frame the guest restores - Grandia II's
 * kernel is entered through a stub that leaves r0 pushed and jumps on. */
#define KEY_SLACK 0x40u

void phantom_stack_switched(SH4CPU *cpu, uint32_t sp0, uint32_t ret, int sp_loaded) {
    uint32_t sp1 = cpu->r[15];
    /* How the switch ended. An RTE goes to SPC: a kernel restoring a thread.
     * An RTS that loaded the stack pointer and went somewhere other than the
     * call restored a saved context - longjmp, or a coroutine library built
     * on it, like the server tasks Grandia II runs from its interrupt
     * handler. A stack that was only moved is a function that left it
     * unbalanced; Grandia II has those in code it loads. */
    int rte = cpu->pc == cpu->spc, restored = sp_loaded && cpu->pr != ret;
    /* PC égal à SPC ne prouve pas non plus qu'un RTE vient d'avoir lieu :
     * l'interpréteur de scénario de Grandia II (cadre de 0x20C octets sur la
     * pile 0x8C00F300) rappelait la fonction où la dernière interruption avait
     * été prise ; sa fin normale passait pour un RTE et devenait une fausse
     * tâche, figée, après le premier combat. Il faut un RTE réellement exécuté
     * depuis le dernier passage ici, vers cette adresse et sur cette pile. */
    { static uint32_t vus;
      if (sh4_rte_count && rte)
          rte = sh4_rte_count != vus && sh4_rte_pc == cpu->pc && sh4_rte_sp == sp1;
      vus = sh4_rte_count; }
    ResumePoint *point = restored ? resume_point(sp1, cpu->pr) : NULL;
    /* PC equal to SPC does not prove an RTE: an interrupt taken at the very
     * call leaves SPC on the same address, and House of the Dead 2's
     * scheduler then lost the longjmp that ends each frame, at a different
     * moment every run. A saved setjmp context that matches exactly wins. */
    if (point) rte = 0; else if (rte) restored = 0;
    { static int miss_trace = -1, misses;
      if (miss_trace < 0) miss_trace = getenv("PHANTOM_DC_THREAD_TRACE") != NULL;
      if (miss_trace && restored && !point && misses++ < 20) {
          fprintf(stderr, "[THREAD] restored context sp=%08X pr=%08X has no resume point;", sp1, cpu->pr);
          for (int i = 0; i < MAX_RESUME; i++)
              if (g_resume[i].mark && g_resume[i].ret == cpu->pr)
                  fprintf(stderr, " [buf %08X sp %08X]", g_resume[i].buf, g_resume[i].sp);
          fprintf(stderr, "\n");
      } }
    ResumePoint *found = point;
    if (point) {
        /* Its fiber has to still be inside the frame that saved it. */
        GuestThread *owner = &g_threads[point->owner];
        if (!g_current || owner == g_current) {
            if ((uintptr_t)__builtin_frame_address(0) < point->mark) jump_to(point);
            point = NULL;
        } else if (!owner->alive || !owner->parked_at || owner->parked_at >= point->mark) {
            point = NULL;
        }
    }
    { static int w = -1; static int n;
      if (w < 0) w = getenv("PHANTOM_DC_SETJMP_WATCH") != NULL;
      if (w && cpu->pr == g_watch_ret && !point && n++ < 20) {
          int slot = -1; for (int i = 0; i < MAX_RESUME; i++) if (g_resume[i].mark && g_resume[i].ret == g_watch_ret) slot = i;
          fprintf(stderr, "[SETJMP-WATCH] RATE stack %08X -> %08X pr=%08X ret=%08X charge=%d restaure=%d point=%s fibre=%d",
                  sp0, sp1, cpu->pr, ret, sp_loaded, restored, found ? "trouve" : "absent", g_current ? (int)(g_current - g_threads) : -1);
          if (slot >= 0) fprintf(stderr, " ; place %d sp %08X fibre %d marque %p ici %p", slot, g_resume[slot].sp,
                                 g_resume[slot].owner, (void *)g_resume[slot].mark, __builtin_frame_address(0));
          fprintf(stderr, "\n");
      } }
    /* Un RTE qui retombe sur la pile qu'avait la tâche quand l'interruption l'a
     * prise (SGR) revient simplement sur cette même tâche, même si le
     * gestionnaire a beaucoup creusé la pile entre-temps. Grandia II, à la fin
     * de son premier combat, sort ainsi d'une interruption 0x20C octets plus
     * haut : pris pour une nouvelle tâche, le retour partait dans le vide et
     * le jeu restait figé. Seulement si aucune autre tâche n'a pris la main
     * depuis : le gestionnaire de Grandia a sa propre pile, partagée par toutes
     * les tâches, et y passer peut déjà avoir changé de tâche. */
    if (!point && rte && g_current && g_exc_thread == g_current && sp1 == g_exc_sgr && sp1 == cpu->sgr) return;
    if (!point && (!g_enabled || (sp1 > sp0 ? sp1 - sp0 : sp0 - sp1) < SWITCH_DISTANCE)) return;
    GuestThread *next = point ? &g_threads[point->owner] : NULL, *remplacee = NULL;
    for (int i = 0; i < g_count && !next; i++) {
        GuestThread *t = &g_threads[i];
        /* Atelier : PHANTOM_DC_KEY_SLACK=octets essaie une autre tolérance. */
        static uint32_t slack; if (!slack) { const char *e = getenv("PHANTOM_DC_KEY_SLACK"); slack = e ? (uint32_t)strtoul(e, NULL, 0) : KEY_SLACK; }
        /* Un contexte restauré qui ne revient pas là où la tâche s'était
         * arrêtée n'est pas sa reprise mais un nouveau départ sur la même pile.
         * Dead or Alive 2 relance ainsi la tâche 0x8C0C3880 depuis son entrée,
         * sur sa pile neuve (0x20 au-dessus de l'endroit où l'ancienne
         * attendait) : reprise à la place, l'ancienne repartait au milieu de sa
         * boucle avec les registres d'une tâche neuve et sautait n'importe où. */
        int sur_sa_pile = t->alive && t->key && t != g_current && sp1 >= t->key && sp1 - t->key <= slack;
        if (sur_sa_pile && restored && !rte && t->park_ret && cpu->pr != t->park_ret) { remplacee = t; continue; }
        if (sur_sa_pile) next = t;
    }
    { static int trace = -1, n;
      if (trace < 0) { const char *e = getenv("PHANTOM_DC_THREAD_TRACE"); trace = e ? atoi(e) > 1 ? atoi(e) : 200 : 0; }
      if (n++ < trace)
          fprintf(stderr, "[THREAD] switch sp %08X -> %08X pc=%08X spc=%08X pr=%08X ret=%08X %s to %d%s\n", sp0, sp1,
                  cpu->pc, cpu->spc, cpu->pr, ret, rte ? "rte" : restored ? "context" : "rts",
                  next ? (int)(next - g_threads) : -1, point ? " (setjmp)" : ""); }
    /* Nobody parked there: a thread running for the first time, from where
     * the RTE or the restored context goes. Anything else (a stack reset)
     * keeps the old behaviour of carrying on here. */
    if (!next && !rte && !restored) return;
    if (!fibers_ready(cpu)) return;
    if (!next) {
        if (g_count >= MAX_THREADS) return;
        next = &g_threads[g_count];
        next->start_pc = rte ? cpu->pc : cpu->pr; next->start_pr = cpu->pr;
        next->fiber = CreateFiberEx(0x10000, FIBER_STACK, 0, thread_main, next);
        if (!next->fiber) return;
        /* An RTE ends the exception; a restored context stays at the
         * interrupt level it was restored from. */
        next->alive = 1; next->irq_depth = rte ? 0 : sh4_irq_depth(); next->key = 0; next->park_ret = 0;
        g_count++;
        /* L'ancienne tâche de cette pile ne reprendra jamais : sa pile sert au nouveau départ. */
        if (remplacee) { remplacee->alive = 0; remplacee->key = 0; }
        static int announced;
        if (announced++ < 16) {
            fprintf(stderr, "[THREAD] new thread %d entry %08X stack %08X\n", g_count - 1, next->start_pc, sp1);
            /* Relevé : d'où l'on vient et où attendent les autres tâches. */
            if (getenv("PHANTOM_DC_THREAD_TRACE")) {
                fprintf(stderr, "[THREAD]   depuis la tache %d (pile %08X, %s, pc %08X pr %08X) ; en attente :",
                        g_current ? (int)(g_current - g_threads) : -1, sp0, rte ? "rte" : "contexte", cpu->pc, cpu->pr);
                for (int i = 0; i < g_count - 1; i++)
                    if (g_threads[i].alive && g_threads[i].key) fprintf(stderr, " %d@%08X", i, g_threads[i].key);
                fprintf(stderr, "\n[THREAD]   derniere exception : tache %d pile %08X ; SGR %08X SPC %08X SSR %08X\n",
                        g_exc_thread ? (int)(g_exc_thread - g_threads) : -1, g_exc_sgr, cpu->sgr, cpu->spc, cpu->ssr);
            }
        }
    }
    next->pending = point;
    if (g_current) g_current->park_ret = ret;   /* elle reprendra en revenant à ret */
    switch_to(next, sp0);
}
