#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include "hal/dc_cinematic.h"

static MapleController pad;
static int active;
static uint64_t now;
MapleController *dc_maple_get_controller(DCHardware *hw, int port) {
    (void)hw;
    return port == 0 ? &pad : NULL;
}
int dc_video_movie_active(void) { return active; }
uint64_t platform_get_ticks_us(void) { return now; }

int main(void) {
    DCHardware *hw = (DCHardware *)1;
    pad.buttons = 0xffff;
    dc_cinematic_update(hw);
    assert(dc_cinematic_buttons() == 0);
    /* The key-down and the first detected video frame occur together. */
    now = 100; active = 1; pad.buttons &= ~CONT_START;
    dc_cinematic_update(hw);
    assert(dc_cinematic_buttons() == CONT_START);
    dc_cinematic_offer_skip();
    assert(dc_cinematic_message() == NULL); /* hidden after START */
    /* A short press remains visible long enough, then releases. */
    now = 10000; pad.buttons |= CONT_START; dc_cinematic_update(hw);
    assert(dc_cinematic_buttons() == CONT_START);
    now = 250101; dc_cinematic_update(hw);
    assert(dc_cinematic_buttons() == 0);
    assert(dc_cinematic_message() != NULL);
    /* Ending a video clears a pending extension immediately. */
    now = 260000; pad.buttons &= ~CONT_START; dc_cinematic_update(hw);
    assert(dc_cinematic_buttons() == CONT_START);
    active = 0; dc_cinematic_update(hw);
    assert(dc_cinematic_buttons() == 0);
    assert(dc_cinematic_message() == NULL);
    /* A button held from before a video is not a fresh request. */
    now = 270000; active = 1; dc_cinematic_update(hw);
    assert(dc_cinematic_buttons() == 0);
    pad.buttons |= CONT_START; dc_cinematic_update(hw);
    now = 280000; pad.buttons &= ~CONT_START; dc_cinematic_update(hw);
    assert(dc_cinematic_buttons() == CONT_START);
    dc_cinematic_offer_skip();
    assert(dc_cinematic_message() == NULL);
    now = 6000000; pad.buttons |= CONT_START; dc_cinematic_update(hw);
    dc_cinematic_offer_skip();
    assert(dc_cinematic_message() == NULL); /* one brief hint per film */
    puts("Dreamcast cinema START edge cases passed");
    return 0;
}
