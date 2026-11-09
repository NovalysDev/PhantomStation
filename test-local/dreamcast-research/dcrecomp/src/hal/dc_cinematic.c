/* Dreamcast movie detection and a bounded START press.
 * The game decides whether the current sequence is skippable. */
#include "hal/dc_cinematic.h"
#include "platform/platform.h"
#include <stdint.h>
#include <stdio.h>

#define START_EXTENSION_US 250000u

static int movie_running, start_was_down;
static uint64_t start_until;

static int start_down(DCHardware *hw) {
    MapleController *pad = dc_maple_get_controller(hw, 0);
    return pad && !(pad->buttons & CONT_START);
}

void dc_cinematic_update(DCHardware *hw) {
    uint64_t now = platform_get_ticks_us();
    int active = dc_video_movie_active();
    int down = start_down(hw);
    if (!active) {
        if (movie_running) puts("[CINEMA] fin du film");
        movie_running = 0;
        start_until = 0;
        start_was_down = down;
        return;
    }
    if (!movie_running) {
        movie_running = 1;
        start_until = 0;
        start_was_down = down;
        puts("[CINEMA] film detecte");
    } else if (down && !start_was_down) {
        start_until = now + START_EXTENSION_US;
        puts("[CINEMA] START demande par le joueur");
    }
    start_was_down = down;
}

uint16_t dc_cinematic_buttons(void) {
    return movie_running && start_until && platform_get_ticks_us() < start_until
        ? CONT_START : 0;
}

const char *dc_cinematic_message(void) {
    /* The game controls its own prompts. Do not claim a film is skippable. */
    return NULL;
}
