#include "hal/dc_cheats.h"
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

static uint8_t ram[16 * 1024 * 1024];
uint8_t *sh4_get_ram_ptr(void) { return ram; }
uint32_t sh4_get_ram_size(void) { return sizeof ram; }
uint32_t sh4_get_ram_mask(void) { return sizeof ram - 1; }

int main(void) {
    memset(ram, 0x5a, sizeof ram);
    dc_cheats_tick();
    dc_cheats_menu_toggle();
    assert(dc_cheats_menu_is_open());
    assert(strstr(dc_cheats_menu_text(), "Small") != NULL);
    assert(strstr(dc_cheats_menu_text(), "Unsafe") == NULL);
    dc_cheats_menu_select();
    for (int i=0; i<40; ++i) dc_cheats_tick();
    assert(ram[100] == 0x34 && ram[101] == 0x12 && ram[102] == 0x5a);
    assert(ram[200] == 0x5a);
    dc_cheats_menu_move(1);
    assert(strstr(dc_cheats_menu_text(), "> OFF Large") != NULL);
    dc_cheats_menu_select();
    for (int i=0; i<40; ++i) dc_cheats_tick();
    assert(ram[104] == 0x78 && ram[105] == 0x56 && ram[106] == 0x34 && ram[107] == 0x12);
    dc_cheats_menu_toggle();
    assert(!dc_cheats_menu_is_open());
    puts("Dreamcast cheat menu and safe writes passed");
    return 0;
}
