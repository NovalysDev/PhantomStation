/* Dreamcast movie detection and a bounded START press.
 * The game decides whether the current sequence is skippable. */
#include "hal/dc_cinematic.h"
#include "platform/platform.h"
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define START_EXTENSION_US 250000u
#define HINT_DURATION_US 5000000u

static int movie_running, start_was_down;
static uint64_t start_until;
static uint64_t hint_started;
static int hint_offered;

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
        hint_offered = 0;
        puts("[CINEMA] film detecte");
    }
    /* The first movie frame can arrive on the same poll as the key-down.
     * Compare with the previous poll before updating start_was_down, so that
     * a short press on that frame is not missed. A button already held before
     * the movie started is not treated as a new request. */
    if (down && !start_was_down) {
        start_until = now + START_EXTENSION_US;
        puts("[CINEMA] START demande par le joueur");
    }
    start_was_down = down;
}

uint16_t dc_cinematic_buttons(void) {
    return movie_running && start_until && platform_get_ticks_us() < start_until
        ? CONT_START : 0;
}

void dc_cinematic_offer_skip(void) {
    if (movie_running && !hint_offered) {
        hint_offered = 1;
        hint_started = platform_get_ticks_us();
    }
}

const char *dc_cinematic_message(void) {
    if (!movie_running || !hint_offered || dc_cinematic_buttons() ||
        platform_get_ticks_us() - hint_started >= HINT_DURATION_US) return NULL;
    const char *language = getenv("PHANTOM_LANGUAGE");
    return language && strcmp(language, "en") == 0
        ? "START / ENTER TO SKIP" : "START / ENTREE POUR PASSER";
}
