/* Experimental local diagnostic. No claim of playable game support. */
#include "recompiler/sh4_cpu.h"
#include "recompiler/sh4_task.h"
#include "hal/dc_hardware.h"
#include "hal/dc_bios.h"
#include "hal/pvr2.h"
#include "hal/pvr2_texture.h"
#include "hal/dc_vmu.h"
#include "platform/platform.h"
#include "game/game_functions.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <windows.h>
#include <shellapi.h>
#define SDL_MAIN_HANDLED
#include <SDL.h>
#include <dbghelp.h>

static SH4CPU cpu;
int pal_chd_open(const char *path);
int pal_chd_open_stream(FILE *stream);
int pal_chd_root_file(const char *name, unsigned char *dest, uint32_t capacity);
int pal_texture_fallback(SH4CPU *cpu, uint32_t surface, uint32_t source);
int pal_texture_free(SH4CPU *cpu, uint32_t surface);

static DWORD WINAPI diagnostic_stack(LPVOID argument) {
    HANDLE target = (HANDLE)argument;
    Sleep(2000);
    if (SuspendThread(target) == (DWORD)-1) return 1;
    CONTEXT ctx = {0}; ctx.ContextFlags = CONTEXT_FULL;
    if (GetThreadContext(target, &ctx)) {
        fprintf(stderr, "[PAL-STATE] pc=%08x pr=%08x r0=%08x r4=%08x r14=%08x\n",
                cpu.pc, cpu.pr, cpu.r[0], cpu.r[4], cpu.r[14]);
        uint32_t at = cpu.r[14] & cpu.ram_mask;
        if (at <= cpu.ram_size - 48) {
            fprintf(stderr, "[PAL-STATE] handle:");
            for (unsigned i=0; i<48; i++) fprintf(stderr, " %02x", cpu.ram[at+i]);
            fprintf(stderr, "\n");
        }
        HANDLE process = GetCurrentProcess();
        uintptr_t base = (uintptr_t)GetModuleHandle(NULL);
        STACKFRAME64 frame = {0};
        frame.AddrPC.Offset=ctx.Rip; frame.AddrPC.Mode=AddrModeFlat;
        frame.AddrFrame.Offset=ctx.Rbp; frame.AddrFrame.Mode=AddrModeFlat;
        frame.AddrStack.Offset=ctx.Rsp; frame.AddrStack.Mode=AddrModeFlat;
        SymInitialize(process, NULL, TRUE);
        fprintf(stderr, "[PAL-STACK] pc-rva=0x%llx\n", (unsigned long long)(ctx.Rip-base));
        for (int i=0; i<25; i++) {
            if (!StackWalk64(IMAGE_FILE_MACHINE_AMD64, process, target, &frame, &ctx, NULL,
                             SymFunctionTableAccess64, SymGetModuleBase64, NULL)) break;
            if (frame.AddrPC.Offset < base) break;
            fprintf(stderr, "[PAL-STACK] rva=0x%llx\n", (unsigned long long)(frame.AddrPC.Offset-base));
        }
    }
    ResumeThread(target);
    CloseHandle(target);
    return 0;
}

static void pal_irq(SH4CPU *c) {
    uint32_t regs[16], pr = c->pr, sr = c->sr;
    memcpy(regs, c->r, sizeof regs);
    func_8C16C810(c);
    /* Diagnostic only: deliver the SDK backup server until the BIOS callback
       dispatcher is implemented. Run it only after the game initializes it. */
    if ((getenv("DCRECOMP_TEST_SAVE_SCHEDULER") || getenv("PHANTOM_DC_FIBERS")) && dc_vmu_active() &&
        *(uint32_t *)(c->ram + (0x0c177aa4u & c->ram_mask))) {
        static unsigned vmu_ticks;
        if ((++vmu_ticks & 63u) == 0) {
            memcpy(c->r, regs, sizeof regs); c->pr = pr; c->sr = sr;
            func_8C1503D4(c);
        }
    }
    /* Diagnostic scheduler: the normal BIOS interrupt dispatcher is not yet
       implemented. Service the actual ADX server when it has open requests. */
    int pending = 0;
    for (int i = 0; i < 16; ++i)
        if (c->ram[(0x0c2b9c38u & c->ram_mask) + i * 48] == 1) pending = 1;
    if (pending) {
        static unsigned calls;
        if (++calls <= 3) fprintf(stderr, "[PAL] ADX scheduler pass %u\n", calls);
        memcpy(c->r, regs, sizeof regs); c->pr = pr; c->sr = sr;
        func_8C14E460(c); /* gdFsExecServer: retire pending disc requests */
        memcpy(c->r, regs, sizeof regs); c->pr = pr; c->sr = sr;
        func_8C152340(c);
    }
}

static int irq_test_ran;
static int run_texture_reserve_regression(void) {
#ifdef _WIN32
    _putenv_s("PHANTOM_DC_TEXTURE_RESERVE","1");
#else
    setenv("PHANTOM_DC_TEXTURE_RESERVE","1",1);
#endif
    SH4CPU test_cpu;
    sh4_init_ex(&test_cpu, DC_RAM_SIZE_16MB);
    sh4_set_cpu_ref(&test_cpu);
    uint32_t surface=0x0c001000u, source=0x0c002000u;
    for (unsigned i=0;i<128;i+=2) { test_cpu.ram[0x2000u+i]=0; test_cpu.ram[0x2001u+i]=0xf8; }
    sh4_write32(&test_cpu,surface+20u,128u);
    int fail=!pal_texture_fallback(&test_cpu,surface,source);
    uint32_t addr=sh4_read32(&test_cpu,surface+28u);
    fail |= addr<DC_VRAM_SIZE || addr+128u>2u*DC_VRAM_SIZE;
    if (!fail) fail |= memcmp(test_cpu.vram+addr,test_cpu.ram+0x2000u,128u)!=0;
    uint8_t pixels[8u*8u*4u]; int w=0,h=0;
    int decoded=pvr2_decode_texture(test_cpu.vram,0,0x08000000u|(addr>>3),pixels,sizeof pixels,&w,&h,NULL,0);
    fail |= !decoded || w!=8 || h!=8 || pixels[0]!=255 || pixels[1]!=0 || pixels[2]!=0;
    fail |= !pal_texture_free(&test_cpu,surface);
    fail |= !pal_texture_fallback(&test_cpu,surface,source);
    fail |= sh4_read32(&test_cpu,surface+28u)!=addr;
    fail |= !pal_texture_free(&test_cpu,surface);
    fail |= pal_texture_fallback(&test_cpu,surface,0x00000200u)!=0;
    puts(fail?"[TEXTURE-RESERVE] FAIL":"[TEXTURE-RESERVE] PASS");
    return fail;
}

static int run_texture_dma_regression(void) {
    SH4CPU test_cpu;
    sh4_init_ex(&test_cpu, DC_RAM_SIZE_16MB);
    DCHardware *hw=dc_hw_init();
    if (!hw) return 1;
    sh4_set_hardware(hw); sh4_set_cpu_ref(&test_cpu);
    for (unsigned i=0;i<4;i++) {
        test_cpu.ram[test_cpu.ram_size-4u+i]=(uint8_t)(0x11u+i);
        test_cpu.ram[i]=(uint8_t)(0x22u+i);
    }
    test_cpu.dmac_regs[8]=0x0c000000u+test_cpu.ram_size-4u;
    dc_hw_write32(hw,SB_C2DSTAT,0x11001000u);
    dc_hw_write32(hw,SB_C2DLEN,8u);
    dc_hw_write32(hw,SB_C2DST,1u);
    const uint8_t expected[]={0x11,0x12,0x13,0x14,0x22,0x23,0x24,0x25};
    int fail=memcmp(test_cpu.vram+0x1000u,expected,sizeof expected)!=0;
    /* A destination past the 8 MiB VRAM must not underflow the copy size. */
    dc_hw_write32(hw,SB_C2DSTAT,0x11800000u);
    dc_hw_write32(hw,SB_C2DLEN,32u);
    dc_hw_write32(hw,SB_C2DST,1u);
    dc_hw_destroy(hw);
    puts(fail?"[TEXTURE-DMA] FAIL":"[TEXTURE-DMA] PASS");
    return fail;
}

static void irq_test_handler(SH4CPU *c) {
    irq_test_ran++;
    c->pc = 0x12345678u;
    c->pr = 0x11111111u;
    c->r[3] = 0x22222222u;
}

/* Exercise a pending interrupt during the delay slot of an indirect branch.
 * The handler must not alter the pending destination or the game registers. */
static int run_irq_regression(void) {
    sh4_init_ex(&cpu, DC_RAM_SIZE_16MB);
    DCHardware *hw = dc_hw_init();
    if (!hw) return 1;
    sh4_set_hardware(hw); sh4_set_cpu_ref(&cpu);
    dc_pvr_init(hw); dc_aica_init(hw);
    cpu.pc = 0x8c08136au; cpu.pr = 0x8c0416deu; cpu.r[3] = 0xaabbccddu; cpu.sr = 0;
    dc_hw_write32(hw, SB_IML6NRM, 1u << 3);
    dc_hw_raise_istnrm(3);
    sh4_set_irq_handler(irq_test_handler);
    sh4_poll_irq(&cpu);
    int failed = irq_test_ran != 1 || cpu.pc != 0x8c08136au ||
                 cpu.pr != 0x8c0416deu || cpu.r[3] != 0xaabbccddu;
    printf("[IRQ-REGRESSION] %s handler=%d pc=%08x pr=%08x r3=%08x\n",
           failed ? "FAIL" : "PASS", irq_test_ran, cpu.pc, cpu.pr, cpu.r[3]);
    return failed;
}

static int run_controller_regression(void) {
    DCHardware *hw = dc_hw_init();
    if (!hw || platform_init(320,240,"Controller diagnostic") != 0) return 1;
    int index = SDL_JoystickAttachVirtual(SDL_JOYSTICK_TYPE_GAMECONTROLLER,
                                          SDL_CONTROLLER_AXIS_MAX, SDL_CONTROLLER_BUTTON_MAX, 0);
    if (index < 0) { fprintf(stderr,"virtual pad: %s\n",SDL_GetError()); return 1; }
    SDL_Joystick *joy=SDL_JoystickOpen(index);
    if (!joy) return 1;
    platform_poll_events(hw);
    SDL_JoystickSetVirtualButton(joy, SDL_CONTROLLER_BUTTON_A, 1);
    SDL_JoystickSetVirtualButton(joy, SDL_CONTROLLER_BUTTON_DPAD_UP, 1);
    SDL_JoystickSetVirtualAxis(joy, SDL_CONTROLLER_AXIS_LEFTX, 24000);
    platform_poll_events(hw);
    int fail=1;
    for (int i=0; i<4; i++) {
        MapleController *c=dc_maple_get_controller(hw,i);
        if (c && !(c->buttons & CONT_A) && !(c->buttons & CONT_DPAD_UP) && c->joyx >= 80) {
            printf("[INPUT-REGRESSION] PASS port=%d buttons=%04x x=%d\n",i+1,c->buttons,c->joyx);
            fail=0; break;
        }
    }
    if (fail) fprintf(stderr,"[INPUT-REGRESSION] FAIL: virtual input not found\n");
    SDL_JoystickClose(joy); SDL_JoystickDetachVirtual(index);
    platform_poll_events(hw);
    SDL_Event key={0}; key.type=SDL_KEYDOWN; key.key.keysym.sym=SDLK_z; SDL_PushEvent(&key);
    key.type=SDL_KEYUP; SDL_PushEvent(&key);
    platform_poll_events(hw);
    MapleController *keyboard=dc_maple_get_controller(hw,0);
    if (!keyboard || (keyboard->buttons & CONT_A)) {
        fprintf(stderr,"[INPUT-REGRESSION] FAIL: short keyboard tap lost\n"); fail=1;
    } else printf("[INPUT-REGRESSION] PASS short keyboard tap\n");
    SDL_Delay(100); platform_poll_events(hw);
    if (!keyboard || !(keyboard->buttons & CONT_A)) {
        fprintf(stderr,"[INPUT-REGRESSION] FAIL: keyboard button stuck\n"); fail=1;
    }
    platform_shutdown(); dc_hw_destroy(hw);
    return fail;
}

static int run_maple_vmu_regression(const char *path) {
    sh4_init_ex(&cpu, DC_RAM_SIZE_16MB);
    DCHardware *hw=dc_hw_init(); if (!hw) return 1;
    sh4_set_cpu_ref(&cpu); sh4_set_hardware(hw);
    if (!dc_vmu_open(path)) return 1;
    uint32_t *desc=(uint32_t *)(cpu.ram+0x1000), *resp=(uint32_t *)(cpu.ram+0x2000);
    desc[0]=0x80000000u; desc[1]=0x8c002000u;
    desc[2]=0x00000101u; dc_maple_dma(hw,0x8c001000u);
    if ((resp[0]&255)!=5 || resp[1]!=0x02000000u || ((resp[0]>>16)&255)!=1) return 1;
    /* A single-word NOP before a valid frame must not corrupt RAM. */
    desc[0]=0x00000700u; desc[1]=0x80000000u; desc[2]=0x8c002000u; desc[3]=0x00000101u;
    dc_maple_dma(hw,0x8c001000u);
    if ((resp[0]&255)!=5) return 1;
    desc[1]=0x8c002000u;
    desc[0]=0x80000000u|2u; desc[2]=0x0200010bu;
    desc[3]=0x02000000u; desc[4]=0xff000000u;
    dc_maple_dma(hw,0x8c001000u);
    if ((resp[0]&255)!=8 || ((uint8_t *)&resp[3])[0]!=0x55) return 1;
    desc[0]=0x80000000u|34u; desc[2]=0x2200010cu;
    desc[3]=0x02000000u; desc[4]=0x07000200u;
    memset(&desc[5],0x5a,128); dc_maple_dma(hw,0x8c001000u);
    if ((resp[0]&255)!=7) return 1;
    desc[0]=0x80000000u|2u; desc[2]=0x0200010bu; desc[4]=0x07000000u;
    dc_maple_dma(hw,0x8c001000u);
    if ((resp[0]&255)!=8 || ((uint8_t *)&resp[3])[256]!=0x5a) return 1;
    dc_vmu_close(); dc_hw_destroy(hw); puts("[VMU-MAPLE-REGRESSION] PASS"); return 0;
}

static int task_test_failed;
static void task_test_worker(SH4CPU *c) {
    if (c->pc != 0x12345678u) task_test_failed=1;
    c->pc=0x87654321u;
    sh4_task_yield();
    if (c->pc != 0x87654321u) task_test_failed=1;
}
static int run_task_regression(void) {
    if (!sh4_task_init()) return 1;
    SH4CPU c={0}; c.pc=0x12345678u;
    SH4Task *task=sh4_task_spawn(task_test_worker,&c);
    if (!task || !sh4_task_resume(task) || c.pc!=0x12345678u) task_test_failed=1;
    c.pc=0xaabbccddu;
    if (sh4_task_resume(task) || c.pc!=0xaabbccddu) task_test_failed=1;
    sh4_task_destroy(task); sh4_task_shutdown();
    printf("[TASK-REGRESSION] %s\n",task_test_failed?"FAIL":"PASS");
    return task_test_failed;
}

extern void pal_vmsfb_create(SH4CPU *cpu);
extern void pal_vmsfb_exec(SH4CPU *cpu);
extern unsigned pal_vmsfb_active_count(void);
extern void phantom_learn_set_boot(uint32_t size);   /* learn.c */
static int run_vmu_fiber_regression(void) {
    _putenv_s("DCRECOMP_TEST_SAVE_SCHEDULER","1");
    if (!sh4_task_init()) return 1;
    SH4CPU c={0}; c.r[4]=0x8c170232u; c.r[5]=0x8c010000u; c.r[6]=0x400u;
    pal_vmsfb_create(&c);
    if (c.r[0]!=0 || pal_vmsfb_active_count()!=1) return 1;
    c.pc=0x11223344u; c.r[15]=0x8c00fc00u;
    pal_vmsfb_exec(&c);
    if (pal_vmsfb_active_count()!=1 || c.pc!=0x11223344u || c.r[15]!=0x8c00fc00u) return 1;
    pal_vmsfb_exec(&c);
    if (pal_vmsfb_active_count()!=0 || c.pc!=0x11223344u || c.r[15]!=0x8c00fc00u) return 1;
    /* Reuse a finished slot: stale done/started flags must not suppress it. */
    c.r[4]=0x8c170232u; c.r[5]=0x8c010400u; c.r[6]=0x400u;
    pal_vmsfb_create(&c); if (c.r[0]!=0 || pal_vmsfb_active_count()!=1) return 1;
    pal_vmsfb_exec(&c); if (pal_vmsfb_active_count()!=1) return 1;
    pal_vmsfb_exec(&c); if (pal_vmsfb_active_count()!=0) return 1;
    sh4_task_shutdown(); puts("[VMU-FIBER-REGRESSION] PASS"); return 0;
}

static int run_vmu_env_regression(void) {
    DCHardware *hw=dc_hw_init(); if(!hw) return 1;
    dc_maple_init(hw);
    unsigned char data[128]; memset(data,0x5a,sizeof data);
    int failed=!dc_vmu_active() || !dc_vmu_write(7,2,data,sizeof data);
    dc_hw_destroy(hw); puts(failed?"[VMU-UNICODE] FAIL":"[VMU-UNICODE] PASS"); return failed;
}

static int run_aica_monitor_regression(void) {
    sh4_init_ex(&cpu, DC_RAM_SIZE_16MB); sh4_set_cpu_ref(&cpu);
    DCHardware *hw=dc_hw_init(); if (!hw) return 1;
    sh4_set_hardware(hw);dc_aica_init(hw);
    dc_aica_reg_write(0x04,0x100);dc_aica_reg_write(0x0c,8);
    dc_aica_reg_write(0x24,15u<<8);dc_aica_reg_write(0,0xc000);
    int16_t samples[6]={0};dc_aica_mix(samples,3);
    dc_aica_reg_write(0x280c,0);
    int failed=dc_aica_reg_read(0x2814)!=3;
    dc_aica_reg_write(0x280c,1u<<8);
    failed |= dc_aica_reg_read(0x2814)!=0;
    dc_hw_destroy(hw);sh4_destroy(&cpu);
    puts(failed?"[AICA-MONITOR] FAIL":"[AICA-MONITOR] PASS");return failed;
}

int main(int argc, char **argv) {
    setvbuf(stdout, NULL, _IONBF, 0);
    if (argc == 2 && !strcmp(argv[1], "--test-texture-reserve")) return run_texture_reserve_regression();
    if (argc == 2 && !strcmp(argv[1], "--test-texture-dma")) return run_texture_dma_regression();
    if (argc == 2 && !strcmp(argv[1], "--test-irq")) return run_irq_regression();
    if (argc == 2 && !strcmp(argv[1], "--test-tasks")) return run_task_regression();
    if (argc == 2 && !strcmp(argv[1], "--test-vmu-fibers")) return run_vmu_fiber_regression();
    if (argc == 2 && !strcmp(argv[1], "--test-controller")) return run_controller_regression();
    if (argc == 2 && !strcmp(argv[1], "--test-aica-monitor")) return run_aica_monitor_regression();
    if (argc == 2 && !strcmp(argv[1], "--test-vmu-env")) return run_vmu_env_regression();
    if (argc == 3 && !strcmp(argv[1], "--test-vmu")) return run_maple_vmu_regression(argv[2]);
    if (argc != 3) { fprintf(stderr, "Usage: pal_probe 1ST_READ.BIN game.chd\n"); return 2; }
    int wide_argc=0;
    wchar_t **wide_argv=CommandLineToArgvW(GetCommandLineW(),&wide_argc);
    if (!wide_argv || wide_argc!=3) { if(wide_argv) LocalFree(wide_argv); return 2; }
    FILE *f=_wfopen(wide_argv[1],L"rb");
    FILE *disc_stream=_wfopen(wide_argv[2],L"rb");
    LocalFree(wide_argv);
    if (!f || !disc_stream) { if(f) fclose(f); if(disc_stream) fclose(disc_stream); perror("game image"); return 2; }
    if (pal_chd_open_stream(disc_stream) != 0) { fclose(f); fclose(disc_stream); fprintf(stderr,"Cannot open Dreamcast CHD\n"); return 2; }
    sh4_init_ex(&cpu, DC_RAM_SIZE_16MB);
    sh4_set_legacy_write_guards(1);   /* Crazy Taxi's allocator table, see sh4_cpu.c */
    DCHardware *hw = dc_hw_init();
    if (!hw) { fclose(f); return 1; }
    sh4_set_hardware(hw);
    sh4_set_cpu_ref(&cpu);
    dc_pvr_init(hw); dc_maple_init(hw); dc_aica_init(hw); dc_gdrom_init(hw);
    if (_wgetenv(L"PHANTOM_DC_VMU") && !dc_vmu_active()) {
        fputs("[VMU] carte memoire indisponible : partie sans sauvegarde.", stderr);
    }
    int aica_size = pal_chd_root_file("AICADRV.BIN", cpu.aica_ram, DC_AICA_SIZE);
    if (aica_size < 0) { fprintf(stderr, "[PAL] AICADRV.BIN absent ou illisible\n"); return 1; }
    printf("[PAL] AICA driver: %d bytes\n", aica_size);
    size_t size = fread(cpu.ram + 0x10000, 1, 1477040, f);
    fclose(f);
    if (size != 1477040) { fprintf(stderr, "Wrong boot size\n"); return 2; }
    /* A hand-written profile learns too: the boot program occupies this much
     * RAM, so calls into it are entry points of the main programme rather than
     * code the game loaded, and windows dumped around unknown code stop here. */
    phantom_learn_set_boot((uint32_t)size);
    for (unsigned off = 0xc000; off < 0xf400; off += 4) {
        const unsigned char pattern[] = {'S','E','G','A'};
        memcpy(cpu.ram + off, pattern, 4);
    }
    /* The European entry stub at 0x8C02002C points to this CRT bootstrap.
       It initializes game data and libraries before entering MainLoop. */
    cpu.pc = 0x8c14ab60;
    cpu.r[15] = 0x8c00fc00; cpu.vbr = 0x8c00f400;
    cpu.r[2] = 0x8c000000; cpu.sr = 0x700000f0;
    cpu.fpscr = 0x00040001; cpu.pr = 0;
    sh4_bios_install_vectors(&cpu);
    /* Diagnostic HLE BIOS settings: European region, English fallback.
       This title image advertises English and Japanese only. */
    cpu.ram[0x72] = '2'; cpu.ram[0x73] = '1'; cpu.ram[0x74] = '1';
    cpu.ram[0x7d] = 1;
    /* Diagnostic only: fake the sound-ready handshake to inspect later boot
       stages while the native AICA audio implementation remains incomplete. */
    sh4_aica_publish(0x5c, 1, 0);
    if ((getenv("DCRECOMP_TEST_SAVE_SCHEDULER") || getenv("PHANTOM_DC_FIBERS")) && !sh4_task_init()) return 1;
    sh4_set_irq_handler(pal_irq);
    int scale=1; const char *scale_env=getenv("PHANTOM_DC_SCALE");
    if (scale_env) { int n=atoi(scale_env); if(n>=1 && n<=4) scale=n; }
    if (platform_init(640*scale, 480*scale, "Crazy Taxi Europe - diagnostic") < 0) return 1;
    int drawable_w,drawable_h; platform_get_drawable_size(&drawable_w,&drawable_h);
    pvr2_ta_init(); if (pvr2_render_init(drawable_w,drawable_h) != 0) return 1;
    printf("[PAL] Europe CRT bootstrap 0x%08X; boot size %zu\n", cpu.pc, size);
    HANDLE main_thread;
    if (getenv("DCRECOMP_DIAGNOSTIC_STACK") && DuplicateHandle(GetCurrentProcess(), GetCurrentThread(), GetCurrentProcess(), &main_thread,
                        0, FALSE, DUPLICATE_SAME_ACCESS)) {
        HANDLE observer = CreateThread(NULL, 0, diagnostic_stack, main_thread, 0, NULL);
        if (observer) CloseHandle(observer); else CloseHandle(main_thread);
    }
    func_8C14AB60(&cpu);
    printf("[PAL] Bootstrap returned\n");
    return 0;
}
