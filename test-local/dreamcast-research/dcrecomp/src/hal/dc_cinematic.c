/**
 * Dreamcast cinematic detection.
 *
 * Deux sortes de cinematiques :
 *  - les films Sofdec, decodes en textures YUV par le TA : dc_hardware.c
 *    compte les images completes, un flux regulier signifie un film ;
 *  - les scenes en images calculees, ou aucun decodeur n'intervient : rien
 *    ne permet de les detecter, c'est le maintien de START qui fait office
 *    de demande de saut.
 *
 * Dans les deux cas un appui seul peut tomber entre deux lectures de la
 * manette par le jeu : un appui arme donc une courte rafale de START.
 * Rien n'est envoye tant que le joueur ne demande rien.
 *
 * Environment:
 *   PHANTOM_DC_AUTO_SKIP_FMV=1   re-enable the automatic START pulses
 *   PHANTOM_DC_SKIP_BUTTON=a|b|start  button used by that opt-in mode
 *   PHANTOM_DC_SKIP_HOLD=0       desactive le saut par maintien de START
 */

#include "hal/dc_cinematic.h"
#include "platform/platform.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define CINEMA_PRESS_US       150000u
#define CINEMA_GAP_US         300000u
#define CINEMA_FEED_GAP_US    120000u
#define CINEMA_MAX_PRESSES    20u
#define CINEMA_FORCED_HOLD_US 800000u
#define CINEMA_FORCED_PRESSES 4u

static int      cfg_ready;
static int      cfg_enabled = 0;   /* jamais de saut automatique par defaut */
static int      cfg_hold = 1;      /* saut par maintien de START */
static uint16_t cfg_mask = CONT_START;
static const char *cfg_name = "START";

static int      movie_running;
static int      press_down;
static uint64_t press_us;
static uint64_t release_us;
static unsigned presses;
static int      player_skip;   /* le joueur a demande a quitter ce film */
static int      player_prev;   /* START du joueur a l'image precedente */
static unsigned feed_frames;   /* images du film au dernier changement */
static uint64_t feed_change_us;
static uint64_t start_hold_since;
static int      forced;        /* saut demande sans film detecte */

static void cinema_config(void) {
    const char *v;
    if (cfg_ready) return;
    cfg_ready = 1;

    v = getenv("PHANTOM_DC_AUTO_SKIP_FMV");
    if (v && *v) cfg_enabled = atoi(v) != 0;

    v = getenv("PHANTOM_DC_SKIP_HOLD");
    if (v && *v) cfg_hold = atoi(v) != 0;

    v = getenv("PHANTOM_DC_SKIP_BUTTON");
    if (v && *v) {
        if (!strcmp(v, "a") || !strcmp(v, "A")) {
            cfg_mask = CONT_A;
            cfg_name = "A";
        } else if (!strcmp(v, "b") || !strcmp(v, "B")) {
            cfg_mask = CONT_B;
            cfg_name = "B";
        } else {
            cfg_mask = CONT_START;
            cfg_name = "START";
        }
    }
}

/* Boutons actifs a 0 : le bit est nul quand le bouton est enfonce. */
static int pad_start_down(DCHardware *hw) {
    MapleController *pad = dc_maple_get_controller(hw, 0);
    return pad && (pad->buttons & CONT_START) == 0;
}

void dc_cinematic_update(DCHardware *hw) {
    int active;
    uint64_t now;
    unsigned max_presses;

    (void)hw;
    cinema_config();

    active = dc_video_movie_active();
    now = platform_get_ticks_us();

    if (active && !movie_running) {
        int w = 0, h = 0;
        movie_running = 1;
        presses = 0;
        press_down = 0;
        press_us = release_us = 0;
        player_skip = 0;
        forced = 0;
        start_hold_since = 0;
        feed_frames = 0;
        feed_change_us = 0;
        player_prev = pad_start_down(hw);
        dc_video_movie_size(&w, &h);
        printf("[CINEMA] film detecte: %dx%d, %u images, saut automatique=%s",
               w, h, dc_video_movie_frames(),
               cfg_enabled ? "oui" : "non (START au choix du joueur)");
        putchar(10);
    }

    if (!active) {
        if (movie_running) {
            movie_running = 0;
            press_down = 0;
            player_skip = 0;
            forced = 0;
            start_hold_since = 0;
            player_prev = 0;
            feed_frames = 0;
            feed_change_us = 0;
            printf("[CINEMA] fin du film: %u appui(s) %s", presses, cfg_name);
            putchar(10);
        }

        /* Scene en images calculees : aucun decodeur YUV, donc aucun film a
         * detecter. Maintenir START vaut demande de saut. */
        if (!cfg_enabled && cfg_hold) {
            int down = pad_start_down(hw);
            if (down && !start_hold_since) start_hold_since = now;
            if (!down) {
                start_hold_since = 0;
            } else if (!forced && now - start_hold_since >= CINEMA_FORCED_HOLD_US) {
                forced = 1;
                player_skip = 1;
                presses = 0;
                press_down = 0;
                release_us = 0;
                puts("[CINEMA] saut force : START maintenu (aucun film detecte)");
            }
        }
        if (!forced) return;
    } else {
        /* Le decodeur ne fournit plus d'images : le film est fini, ou en pause.
         * Ne pas laisser un appui force partir dans la scene suivante. */
        unsigned frames = dc_video_movie_frames();
        if (frames != feed_frames || !feed_change_us) {
            feed_frames = frames;
            feed_change_us = now;
        }
        if (now - feed_change_us >= CINEMA_FEED_GAP_US) {
            press_down = 0;
            return;
        }

        /* Le bouton du joueur arme la rafale : un seul appui peut tomber entre
         * deux lectures de la manette par le jeu, la rafale le rattrape. */
        if (!cfg_enabled) {
            int down = pad_start_down(hw);
            if (down && !player_prev) {
                player_skip = 1;
                presses = 0;
                press_down = 0;
                release_us = 0;
                puts("[CINEMA] saut demande par le joueur (START)");
            }
            player_prev = down;
        }
    }

    max_presses = active ? CINEMA_MAX_PRESSES : CINEMA_FORCED_PRESSES;
    if ((!cfg_enabled && !player_skip) || (presses >= max_presses && !press_down)) {
        if (!active) { forced = 0; player_skip = 0; }
        return;
    }

    if (!press_down) {
        if (release_us && now - release_us < CINEMA_GAP_US)
            return;
        press_down = 1;
        press_us = now;
        presses++;
        printf("[CINEMA] appui %u/%u: %s (t=%llums)",
               presses, max_presses, cfg_name,
               (unsigned long long)(now / 1000));
        putchar(10);
    } else if (now - press_us >= CINEMA_PRESS_US) {
        press_down = 0;
        release_us = now;
    }
}

uint16_t dc_cinematic_buttons(void) {
    if (!press_down)
        return 0;
    if (movie_running || forced)
        return cfg_mask;
    return 0;
}

/* The hint appears only while a movie is detected, never during normal play. */
const char *dc_cinematic_message(void) {
    return movie_running ? "PRESS START" : NULL;
}

