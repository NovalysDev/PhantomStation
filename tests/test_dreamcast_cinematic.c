#include <assert.h>
#include <stdint.h>
#include <string.h>
#include "hal/dc_cinematic.h"

static MapleController pad;
static uint64_t now_us;
static int movie;
static unsigned frames;

uint64_t platform_get_ticks_us(void) { return now_us; }
MapleController *dc_maple_get_controller(DCHardware *hw, int port) { (void)hw; (void)port; return &pad; }
int dc_video_movie_active(void) { return movie; }
unsigned dc_video_movie_frames(void) { return frames; }
void dc_video_movie_size(int *w, int *h) { *w = 640; *h = 480; }

int main(void) {
    DCHardware *hw = (DCHardware *)(uintptr_t)1;
    pad.buttons = 0xffff; now_us = 1000000;
    dc_cinematic_update(hw);
    assert(!dc_cinematic_message() && !dc_cinematic_buttons());
    movie = 1; frames = 1;
    dc_cinematic_update(hw);
    assert(strcmp(dc_cinematic_message(), "PRESS START") == 0);
    now_us += 9000000; ++frames;
    dc_cinematic_update(hw);
    assert(strcmp(dc_cinematic_message(), "PRESS START") == 0);
    pad.buttons &= (uint16_t)~CONT_A; now_us += 1000; ++frames;
    dc_cinematic_update(hw);
    assert(dc_cinematic_buttons() == 0);
    pad.buttons |= CONT_A; now_us += 1000; ++frames;
    dc_cinematic_update(hw);
    pad.buttons &= (uint16_t)~CONT_START; now_us += 1000; ++frames;
    dc_cinematic_update(hw);
    assert(dc_cinematic_buttons() == CONT_START);
    movie = 0; now_us += 1000;
    dc_cinematic_update(hw);
    assert(!dc_cinematic_message() && !dc_cinematic_buttons());
    return 0;
}
