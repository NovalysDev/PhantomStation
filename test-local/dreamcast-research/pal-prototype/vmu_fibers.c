/* Crazy Taxi PAL research only: route SDK cooperative VMU fibers to host fibers.
 * Enabled only with DCRECOMP_TEST_SAVE_SCHEDULER; not a general game runtime. */
#include "recompiler/sh4_cpu.h"
#include "recompiler/sh4_task.h"
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#define MAX_FIBERS 8
typedef struct { SH4Task *task; uint32_t entry, stack_top; int started, done; } VmuFiber;
static VmuFiber slots[MAX_FIBERS];
static VmuFiber *current;
extern void sh4_call_indirect(SH4CPU *cpu);
static void fiber_entry(SH4CPU *cpu) {
    if (!current) return;
    cpu->pc=current->entry;
    sh4_call_indirect(cpu);
    current->done=1;
}
void pal_vmsfb_create(SH4CPU *cpu) {
    uint32_t entry=cpu->r[4], stack=cpu->r[5], size=cpu->r[6];
    if (size<256 || size>0x100000 || (stack & 0x1f000000u)!=0x0c000000u) {cpu->r[0]=0xffffffffu;return;}
    for (unsigned i=0;i<MAX_FIBERS;i++) if (!slots[i].task) {
        memset(&slots[i],0,sizeof slots[i]);
        slots[i].entry=entry; slots[i].stack_top=(stack+size-4u)&~3u;
        slots[i].task=sh4_task_spawn(fiber_entry,cpu);
        cpu->r[0]=slots[i].task ? i : 0xffffffffu;
        printf("[VMU-FIBER] created %u entry=%08x stack=%08x\n",i,entry,slots[i].stack_top);
        return;
    }
    cpu->r[0]=0xffffffffu;
}
void pal_vmsfb_exec(SH4CPU *cpu) {
    SH4CPU saved=*cpu;
    for (unsigned i=0;i<MAX_FIBERS;i++) {
        VmuFiber *slot=&slots[i]; if (!slot->task || slot->done) continue;
        current=slot;
        if (!slot->started) { cpu->r[15]=slot->stack_top; slot->started=1; }
        int alive=sh4_task_resume(slot->task);
        if (!alive || slot->done) { sh4_task_destroy(slot->task); slot->task=NULL; }
        memcpy(cpu->r,saved.r,sizeof cpu->r); memcpy(cpu->r_bank,saved.r_bank,sizeof cpu->r_bank);
        cpu->pc=saved.pc; cpu->pr=saved.pr; cpu->sr=saved.sr; cpu->gbr=saved.gbr;
        cpu->mach=saved.mach; cpu->macl=saved.macl; cpu->fpscr=saved.fpscr; cpu->fpul=saved.fpul;
        memcpy(cpu->fr,saved.fr,sizeof cpu->fr); memcpy(cpu->xf,saved.xf,sizeof cpu->xf);
    }
    current=NULL; cpu->r[0]=0;
}
void pal_vmsfb_sleep(SH4CPU *cpu) { (void)cpu; sh4_task_yield(); }
void pal_vmsfb_end(SH4CPU *cpu) { (void)cpu; if(current) current->done=1; sh4_task_yield(); }

unsigned pal_vmsfb_active_count(void) { unsigned n=0; for (unsigned i=0;i<MAX_FIBERS;i++) if (slots[i].task) n++; return n; }
