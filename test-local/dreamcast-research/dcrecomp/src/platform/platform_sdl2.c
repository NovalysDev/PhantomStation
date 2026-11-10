/**
 * SDL2 Platform Layer
 *
 * Provides window creation, input handling, and audio output using SDL2.
 * This is the bridge between the Dreamcast HAL and the host PC.
 */

#include "recompiler/sh4_cpu.h"
#include "hal/dc_hardware.h"
#include "hal/dc_cinematic.h"
#include "hal/aica_pcm.h"
#include "hal/dc_cheats.h"
#include "hal/dc_ralentis.h"
#include "hal/pvr2.h"
#include <stdio.h>
#include <stdbool.h>
#include <stdlib.h>
#include <math.h>
#include <string.h>
#include <time.h>
#ifdef _WIN32
#include <direct.h>
#else
#include <sys/stat.h>
#endif

#ifdef _WIN32
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#endif

/* Forward declarations - will use SDL2 when available */
/* For initial build, we use stub implementations */

#ifndef HAS_SDL2

/* Stub platform layer for initial build without SDL2 */

typedef struct {
    int width;
    int height;
    bool fullscreen;
    bool running;
    uint64_t frame_count;
} PlatformState;

static PlatformState g_platform = {0};

int platform_init(int width, int height, const char *title) {
    g_platform.width = width;
    g_platform.height = height;
    g_platform.fullscreen = false;
    g_platform.running = true;
    g_platform.frame_count = 0;

    printf("[PLATFORM] Initialized %dx%d window: %s\n", width, height, title);
    printf("[PLATFORM] (SDL2 not linked - running in headless mode)\n");
    return 0;
}

void platform_shutdown(void) {
    aica_pcm_diagnostic_flush();
    printf("[PLATFORM] Shutdown after %llu frames\n",
           (unsigned long long)g_platform.frame_count);
}

bool platform_poll_events(DCHardware *hw) {
    (void)hw;
    g_platform.frame_count++;

    /* In headless mode, run for a limited number of frames for testing */
    if (g_platform.frame_count >= 300) {
        printf("[PLATFORM] Headless frame limit reached\n");
        g_platform.running = false;
    }

    dc_cinematic_update(hw);
    return g_platform.running;
}

void platform_swap_buffers(void) {
    /* No-op in headless mode */
}
void platform_audio_pump(void) {}
void platform_get_drawable_size(int *w,int *h) { *w=g_platform.width; *h=g_platform.height; }


void platform_set_title(const char *title) {
    printf("[PLATFORM] Title: %s\n", title);
}

uint64_t platform_get_ticks_us(void) {
#ifdef _WIN32
    static LARGE_INTEGER freq;
    LARGE_INTEGER now;
    if (!freq.QuadPart) QueryPerformanceFrequency(&freq);
    QueryPerformanceCounter(&now);
    return (uint64_t)((now.QuadPart * 1000000) / freq.QuadPart);
#else
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000 + ts.tv_nsec / 1000;
#endif
}

uint64_t platform_get_ticks_ms(void) {
#ifdef _WIN32
    return GetTickCount64();
#else
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000 + ts.tv_nsec / 1000000;
#endif
}

void platform_sleep_ms(uint32_t ms) {
#ifdef _WIN32
    Sleep(ms);
#else
    struct timespec ts = {ms / 1000, (ms % 1000) * 1000000};
    nanosleep(&ts, NULL);
#endif
}

#else /* HAS_SDL2 */

#include <SDL.h>
#include <SDL_opengl.h>

uint64_t platform_get_ticks_us(void);

static SDL_Window *g_window = NULL;
static SDL_GLContext g_gl_context = NULL;
static SDL_AudioDeviceID g_audio_device;
static FILE *g_audio_capture;
static uint64_t g_audio_polls, g_audio_underruns, g_audio_batches;
/* Mesures de la sortie : ce que l'oreille entend n'est pas le PCM produit mais
 * le PCM produit plus le silence que SDL insère quand sa file est vide. On
 * mesure donc le temps passé sans rien dans la file, et les échantillons
 * réellement produits. */
static uint64_t g_audio_mixed, g_audio_first_us, g_audio_starve_us, g_audio_starve_start, g_audio_worst_us;
static bool g_running = true;

/* ---- Analyse du son pendant la partie ----------------------------------
 * Tout ce que la puce envoie aux haut-parleurs est enregistre et mesure en
 * continu :
 *   - le PCM entier de la partie dans un WAV (20 minutes par defaut,
 *     PHANTOM_DC_SON_MAX_MIN) ;
 *   - une ligne par seconde dans un fichier texte : niveau RMS et crete en
 *     dBFS, saturation, contenu aigu, periodicite (un tampon qui se repete
 *     est la signature d'une boucle qui n'est plus alimentee, l'« alarme »),
 *     voies actives, voies bouclant sur moins de 512 echantillons, volume
 *     general et trous de sortie ;
 *   - le releve des voies actives quand une seconde est signalee.
 *
 * Les deux fichiers vont a cote du journal de ralentissements, dans
 * ...\<disque>\son-<date>.wav et .txt. PHANTOM_DC_SON_DIR force le dossier,
 * PHANTOM_DC_SON_ANALYSE=0 coupe l'analyse. */
#define SON_RATE   44100
#define SON_DEC    4
#define SON_HRATE  (SON_RATE / SON_DEC)
#define SON_HIST_N (SON_HRATE * 2)

static FILE *g_son_wav, *g_son_txt;
static int g_son_on = -1;
static char g_son_wav_path[1400], g_son_txt_path[1400];
static uint64_t g_son_wav_frames, g_son_wav_limit;
static uint64_t g_son_frames, g_son_sec_frames;
static double g_son_l2, g_son_r2, g_son_d2, g_son_dc_l, g_son_dc_r;
static uint64_t g_son_clip;
static unsigned g_son_zc, g_son_sec, g_son_suspectes, g_son_alarmes, g_son_releves;
static double g_son_rms_max = -120.0, g_son_crete_max = -120.0, g_son_sec_crete;
static uint64_t g_son_prev_trous;
static int g_son_prev_mono;
static int16_t g_son_hist[SON_HIST_N];
static unsigned g_son_hist_pos;
static uint64_t g_son_hist_count;
static int g_son_prev_lag_ms, g_son_boucle_suite;

static void son_mkdir(const char *path) {
    char tmp[1200];
    snprintf(tmp, sizeof tmp, "%s", path);
    for (char *p = tmp + 1; *p; ++p)
        if (*p == '\\' || *p == '/') {
            char c = *p; *p = 0;
#ifdef _WIN32
            _mkdir(tmp);
#else
            mkdir(tmp, 0777);
#endif
            *p = c;
        }
#ifdef _WIN32
    _mkdir(tmp);
#else
    mkdir(tmp, 0777);
#endif
}

static void son_wav_entete(FILE *f, uint32_t octets) {
    unsigned char h[44] = {0};
    uint16_t s; uint32_t v;
    memcpy(h, "RIFF", 4); v = 36u + octets; memcpy(h + 4, &v, 4);
    memcpy(h + 8, "WAVEfmt ", 8);
    v = 16; memcpy(h + 16, &v, 4);
    s = 1;  memcpy(h + 20, &s, 2);
    s = 2;  memcpy(h + 22, &s, 2);
    v = SON_RATE; memcpy(h + 24, &v, 4);
    v = SON_RATE * 4u; memcpy(h + 28, &v, 4);
    s = 4;  memcpy(h + 32, &s, 2);
    s = 16; memcpy(h + 34, &s, 2);
    memcpy(h + 36, "data", 4); memcpy(h + 40, &octets, 4);
    fwrite(h, 1, sizeof h, f);
}

static int son_open(void) {
    if (g_son_on >= 0) return g_son_on;
    const char *off = getenv("PHANTOM_DC_SON_ANALYSE");
    if (off && *off && !strcmp(off, "0")) return g_son_on = 0;
    char dir[1024] = "";
    const char *forced = getenv("PHANTOM_DC_SON_DIR");
    if (forced && *forced) snprintf(dir, sizeof dir, "%s", forced);
    else {
        /* Meme dossier que le journal de ralentissements : le parent du
         * dossier « partie-<date> » que le lanceur passe au moteur. */
        const char *partie = getenv("PHANTOM_DC_LEARN_DIR");
        if (partie && *partie) {
            const char *a = strrchr(partie, '\\'), *b = strrchr(partie, '/');
            const char *slash = (b > a) ? b : a;
            if (slash && slash > partie) snprintf(dir, sizeof dir, "%.*s", (int)(slash - partie), partie);
        }
        if (!dir[0]) {
            const char *base = getenv("LOCALAPPDATA");
            if (base && *base) snprintf(dir, sizeof dir, "%s\\PhantomStation\\Sons", base);
            else snprintf(dir, sizeof dir, ".");
        }
    }
    son_mkdir(dir);
    time_t now = time(NULL);
    struct tm tmv; struct tm *lt = localtime(&now);
    if (lt) tmv = *lt; else memset(&tmv, 0, sizeof tmv);
    char stamp[32];
    strftime(stamp, sizeof stamp, "%Y%m%d-%H%M%S", &tmv);
    snprintf(g_son_wav_path, sizeof g_son_wav_path, "%s\\son-%s.wav", dir, stamp);
    snprintf(g_son_txt_path, sizeof g_son_txt_path, "%s\\son-%s.txt", dir, stamp);
    g_son_wav = fopen(g_son_wav_path, "wb");
    if (g_son_wav) son_wav_entete(g_son_wav, 0);
    g_son_txt = fopen(g_son_txt_path, "w");
    if (!g_son_wav && !g_son_txt) { g_son_on = 0; return 0; }
    g_son_wav_limit = (uint64_t)SON_RATE * 60u * 20u;
    const char *mins = getenv("PHANTOM_DC_SON_MAX_MIN");
    if (mins && atoi(mins) > 0) g_son_wav_limit = (uint64_t)SON_RATE * 60u * (unsigned)atoi(mins);
    if (g_son_txt) {
        fprintf(g_son_txt, "Analyse du son pendant la partie\n");
        fprintf(g_son_txt, "Une ligne par seconde : rms et crete en dBFS, saturation, aigu (hf),\n"
                           "periodicite (correlation du signal avec lui-meme 23-371 ms plus tard),\n"
                           "voix = voies actives de la puce, courtes = voies bouclant sur moins de 512 ech.,\n"
                           "keyons = canaux demarres depuis le debut, trous = sorties sans echantillons.\n");
        fprintf(g_son_txt, "WAV : %s (limite %llu min)\n\n",
                g_son_wav ? g_son_wav_path : "non cree",
                (unsigned long long)(g_son_wav_limit / (SON_RATE * 60u)));
        fflush(g_son_txt);
    }
    printf("[SON] analyse enregistree dans %s\n", g_son_txt ? g_son_txt_path : g_son_wav_path);
    g_son_on = 1;
    return 1;
}

static void son_releve_voies(void) {
    if (!g_son_txt) return;
    fprintf(g_son_txt, "    voix actives :");
    int affichees = 0;
    for (int ch = 0; ch < 64 && affichees < 16; ++ch) {
        if (!dc_aica_channel_playing(ch)) continue;
        uint32_t v0 = dc_aica_channel_reg(ch, 0), v1 = dc_aica_channel_reg(ch, 1);
        uint32_t v2 = dc_aica_channel_reg(ch, 2), v3 = dc_aica_channel_reg(ch, 3);
        uint32_t v6 = dc_aica_channel_reg(ch, 6), v9 = dc_aica_channel_reg(ch, 9);
        uint32_t v10 = dc_aica_channel_reg(ch, 10);
        uint32_t sa = ((v0 & 0x7fu) << 16) | (v1 & 0xffffu);
        uint32_t lsa = v2 & 0xffffu, lea = v3 & 0xffffu;
        fprintf(g_son_txt, "\n      ch%02d sa=%05X lsa=%04X lea=%04X boucle=%u pitch=%u oct=%u disdl=%u pan=%02X tl=%u",
                ch, sa, lsa, lea, lea > lsa ? lea - lsa : 0, v6 & 0x3ffu, (v6 >> 11) & 15u,
                (v9 >> 8) & 15u, v9 & 31u, (v10 >> 8) & 255u);
        ++affichees;
    }
    if (!affichees) fprintf(g_son_txt, " aucune");
    fprintf(g_son_txt, "\n");
}

static void son_bilan_seconde(void) {
    double n = (double)g_son_sec_frames;
    if (n <= 0) return;
    double rms = sqrt((g_son_l2 + g_son_r2) / (2.0 * n));
    double rms_db = rms > 0 ? 20.0 * log10(rms / 32768.0) : -120.0;
    double crete_db = g_son_sec_crete > 0 ? 20.0 * log10(g_son_sec_crete / 32768.0) : -120.0;
    double rms_diff = sqrt(g_son_d2 / n);
    double hf = rms > 0 ? rms_diff / rms : 0;
    double dc = (g_son_dc_l + g_son_dc_r) / (2.0 * n);
    double zcr = g_son_zc / n;
    double corr = 0; int lag_ms = 0;
    if (g_son_hist_count >= SON_HRATE + 4096) {
        static double x[SON_HRATE];
        for (unsigned k = 0; k < SON_HRATE; ++k) {
            unsigned idx = (g_son_hist_pos + SON_HIST_N - SON_HRATE + k) % SON_HIST_N;
            x[k] = g_son_hist[idx];
        }
        for (int lag = 256; lag <= 4096; lag += 64) {
            double sxy = 0, sxx = 0, syy = 0;
            unsigned m = SON_HRATE - (unsigned)lag;
            for (unsigned k = 0; k < m; ++k) {
                double a = x[k], b = x[k + (unsigned)lag];
                sxy += a * b; sxx += a * a; syy += b * b;
            }
            if (sxx <= 0 || syy <= 0) continue;
            double c = sxy / sqrt(sxx * syy);
            if (c > corr) { corr = c; lag_ms = lag * 1000 / SON_HRATE; }
        }
    }
    unsigned voix = dc_aica_voice_count();
    unsigned courte_ch = 0;
    unsigned courtes = dc_aica_small_loops(512, &courte_ch);
    unsigned keyons = dc_aica_keyon_total();
    uint64_t trous = g_audio_underruns - g_son_prev_trous;
    g_son_prev_trous = g_audio_underruns;
    unsigned mvol = dc_aica_master_volume();
    int boucle = corr >= 0.85 && rms_db > -35.0;
    if (boucle && abs(lag_ms - g_son_prev_lag_ms) <= 12) ++g_son_boucle_suite;
    else g_son_boucle_suite = boucle ? 1 : 0;
    g_son_prev_lag_ms = lag_ms;
    const char *verdict = "ok";
    if (g_son_clip > (uint64_t)(n * 0.002) || g_son_boucle_suite >= 3 ||
        (courtes > 0 && rms_db > -35.0 && corr >= 0.7)) verdict = "ALARME";
    else if (boucle || (hf > 0.5 && rms_db > -30.0)) verdict = "suspect";
    if (!strcmp(verdict, "ALARME")) ++g_son_alarmes;
    else if (!strcmp(verdict, "suspect")) ++g_son_suspectes;
    if (g_son_txt) {
        fprintf(g_son_txt, "%5us rms=%6.1f crete=%6.1f sat=%llu hf=%.2f zc=%.3f dc=%+.0f "
                           "boucle=%.2f@%dms voix=%u courtes=%u keyons=%u mvol=%u trous=%llu %s\n",
                g_son_sec, rms_db, crete_db, (unsigned long long)g_son_clip, hf, zcr, dc,
                corr, lag_ms, voix, courtes, keyons, mvol,
                (unsigned long long)trous, verdict);
        if (courtes)
            fprintf(g_son_txt, "    boucle courte sur le canal %u (premiere detectee)\n", courte_ch);
        /* Le releve detaille des voix n'est ecrit que sur une alerte : les
         * premieres secondes de musique ne doivent pas consommer la place. */
        if (!strcmp(verdict, "ALARME") && g_son_releves < 20) {
            ++g_son_releves;
            son_releve_voies();
        }
        fflush(g_son_txt);
    }
    if (rms_db > g_son_rms_max) g_son_rms_max = rms_db;
    if (crete_db > g_son_crete_max) g_son_crete_max = crete_db;
    ++g_son_sec;
    g_son_sec_frames = 0; g_son_l2 = g_son_r2 = g_son_d2 = 0;
    g_son_dc_l = g_son_dc_r = 0; g_son_clip = 0; g_son_zc = 0; g_son_sec_crete = 0;
}

static void son_feed(const int16_t *audio, unsigned frames) {
    if (!son_open()) return;
    for (unsigned i = 0; i < frames; ++i) {
        int l = audio[i * 2], r = audio[i * 2 + 1];
        double lf = l, rf = r;
        g_son_l2 += lf * lf; g_son_r2 += rf * rf;
        g_son_dc_l += lf; g_son_dc_r += rf;
        int pic = l < 0 ? -l : l, pic_r = r < 0 ? -r : r;
        if (pic_r > pic) pic = pic_r;
        if (pic >= 32767) ++g_son_clip;
        if (pic > g_son_sec_crete) g_son_sec_crete = pic;
        int mono = (l + r) / 2;
        int d = mono - g_son_prev_mono;
        g_son_d2 += (double)d * d;
        if ((mono < 0) != (g_son_prev_mono < 0)) ++g_son_zc;
        g_son_prev_mono = mono;
        if ((g_son_frames & (SON_DEC - 1)) == 0) {
            g_son_hist[g_son_hist_pos] = (int16_t)mono;
            g_son_hist_pos = (g_son_hist_pos + 1) % SON_HIST_N;
            ++g_son_hist_count;
        }
        ++g_son_frames; ++g_son_sec_frames;
    }
    if (g_son_wav && g_son_wav_frames < g_son_wav_limit) {
        unsigned n = frames;
        if (g_son_wav_frames + n > g_son_wav_limit) n = (unsigned)(g_son_wav_limit - g_son_wav_frames);
        if (n) { fwrite(audio, 4, n, g_son_wav); g_son_wav_frames += n; }
    }
    if (g_son_sec_frames >= SON_RATE) son_bilan_seconde();
}

static void son_close(void) {
    if (g_son_on != 1) return;
    if (g_son_txt) {
        fprintf(g_son_txt, "\n--- bilan : %u s analysees, %u ALARME, %u suspectes, "
                           "crete max %.1f dBFS, rms max %.1f dBFS, %llu trous de sortie\n",
                g_son_sec, g_son_alarmes, g_son_suspectes, g_son_crete_max, g_son_rms_max,
                (unsigned long long)g_audio_underruns);
        if (g_son_wav)
            fprintf(g_son_txt, "WAV : %s (%llu s enregistrees sur %llu s analysees)\n",
                    g_son_wav_path, (unsigned long long)(g_son_wav_frames / SON_RATE),
                    (unsigned long long)(g_son_frames / SON_RATE));
        fclose(g_son_txt); g_son_txt = NULL;
    }
    if (g_son_wav) {
        uint32_t octets = (uint32_t)(g_son_wav_frames * 4u);
        fseek(g_son_wav, 0, SEEK_SET);
        son_wav_entete(g_son_wav, octets);
        fclose(g_son_wav); g_son_wav = NULL;
    }
    printf("[SON] analyse terminee : %u s, %u alarme(s)\n", g_son_sec, g_son_alarmes);
    g_son_on = 0;
}

/* Pousse le son vers la sortie quand sa réserve baisse (deux paquets de
 * 16,7 ms au moins). Appelé à chaque image ET, toutes les 4 ms, depuis
 * dc_aica_update pendant que le jeu calcule : quand Grandia II charge après
 * l'appui sur Start (une image de 516 ms), plus personne ne poussait le son,
 * la sortie se vidait et il grésillait. Une vraie puce son joue de son côté.
 * (Un fil séparé avec verrou étouffait la sortie : le pilote son interroge la
 * puce sans arrêt.) */
void platform_audio_pump(void) {
    if (!g_audio_device) return;
    uint32_t queued=SDL_GetQueuedAudioSize(g_audio_device);
    uint64_t now_us=platform_get_ticks_us();
    if (!g_audio_first_us) g_audio_first_us=now_us;
    if (queued==0) {
        if (!g_audio_starve_start) { g_audio_starve_start=now_us; if (g_audio_batches) ++g_audio_underruns; }
    } else if (g_audio_starve_start) {
        uint64_t gap=now_us-g_audio_starve_start;
        g_audio_starve_us+=gap; if (gap>g_audio_worst_us) g_audio_worst_us=gap;
        g_audio_starve_start=0;
    }
    while (queued < 735u*4u*3u) {
        int16_t audio[735*2]; dc_ralentis_debut(RAL_SON); dc_aica_mix(audio,735); dc_ralentis_fin(RAL_SON); son_feed(audio,735);
        if (SDL_QueueAudio(g_audio_device,audio,sizeof audio)!=0) break;
        ++g_audio_batches; queued += sizeof audio; g_audio_mixed+=735;
        if (g_audio_capture) { fwrite(audio,1,sizeof audio,g_audio_capture); fflush(g_audio_capture); }
    }
}
static SDL_GameController *g_pads[4];
static uint16_t g_keyboard_buttons = 0xffff;
/* Keep a short key tap visible across event-pump cycles, so a down and up
   received in the same poll cannot disappear before Maple reads it. */
static uint64_t g_key_release_at[16];
static uint64_t g_key_down_at[16];
static uint8_t g_key_ltrig = 0, g_key_rtrig = 0;  /* 0 = relache, 255 = a fond */

/* A short pad tap can fall between two Maple reads of the game: hold it
   visible for a minimum time, exactly like a keyboard tap below.
   Per-pad button state: 0 released, 1 physically down, 2 kept after a short
   release until g_pad_since. */
#define PAD_TAP_MIN_MS 80u
static uint8_t  g_pad_state[4][16];
static uint32_t g_pad_since[4][16];

static void open_pad(int device_index, DCHardware *hw) {
    if (getenv("DCRECOMP_TEST_NO_CONTROLLERS")) return;
    if (!SDL_IsGameController(device_index)) return;
    SDL_JoystickID id = SDL_JoystickGetDeviceInstanceID(device_index);
    for (int i=0; i<4; ++i)
        if (g_pads[i] && SDL_JoystickInstanceID(SDL_GameControllerGetJoystick(g_pads[i])) == id) return;
    for (int i=0; i<4; ++i) if (!g_pads[i]) {
        g_pads[i] = SDL_GameControllerOpen(device_index);
        if (g_pads[i]) {
            memset(g_pad_state[i], 0, sizeof g_pad_state[i]);
            dc_maple_set_connected(hw, i, true);
            printf("[INPUT] Gamepad %d: %s\n", i+1, SDL_GameControllerName(g_pads[i]));
        }
        return;
    }
}
static void close_pad(SDL_JoystickID id, DCHardware *hw) {
    for (int i=0; i<4; ++i)
        if (g_pads[i] && SDL_JoystickInstanceID(SDL_GameControllerGetJoystick(g_pads[i])) == id) {
            SDL_GameControllerClose(g_pads[i]); g_pads[i] = NULL;
            memset(g_pad_state[i], 0, sizeof g_pad_state[i]);
            dc_maple_set_connected(hw, i, i == 0);
        }
}
static void poll_pads(DCHardware *hw) {
    static const struct { SDL_GameControllerButton sdl; uint16_t dc; } map[] = {
        {SDL_CONTROLLER_BUTTON_A,CONT_A}, {SDL_CONTROLLER_BUTTON_B,CONT_B},
        {SDL_CONTROLLER_BUTTON_X,CONT_X}, {SDL_CONTROLLER_BUTTON_Y,CONT_Y},
        {SDL_CONTROLLER_BUTTON_START,CONT_START},
        {SDL_CONTROLLER_BUTTON_DPAD_UP,CONT_DPAD_UP}, {SDL_CONTROLLER_BUTTON_DPAD_DOWN,CONT_DPAD_DOWN},
        {SDL_CONTROLLER_BUTTON_DPAD_LEFT,CONT_DPAD_LEFT}, {SDL_CONTROLLER_BUTTON_DPAD_RIGHT,CONT_DPAD_RIGHT}
    };
    for (int i=0; i<4; ++i) {
        MapleController *c = dc_maple_get_controller(hw, i);
        if (!c) continue;
        c->buttons = i == 0 ? g_keyboard_buttons : 0xffff;
        c->ltrig = i == 0 ? g_key_ltrig : 0; c->rtrig = i == 0 ? g_key_rtrig : 0;
        c->joyx = c->joyy = 0;
        SDL_GameController *pad = g_pads[i];
        if (!pad || !SDL_GameControllerGetAttached(pad)) continue;
        for (unsigned n=0; n<sizeof(map)/sizeof(map[0]); ++n) {
            uint16_t bit = map[n].dc;
            int down = SDL_GameControllerGetButton(pad,map[n].sdl);
            for (int b=0; b<16; ++b) if (bit & (1u<<b)) {
                uint32_t now = (uint32_t)SDL_GetTicks64();
                if (down) {
                    if (g_pad_state[i][b] != 1) g_pad_since[i][b] = now;
                    g_pad_state[i][b] = 1;
                } else if (g_pad_state[i][b] == 1) {
                    uint32_t deadline = g_pad_since[i][b] + PAD_TAP_MIN_MS;
                    if (now >= deadline) g_pad_state[i][b] = 0;
                    else { g_pad_state[i][b] = 2; g_pad_since[i][b] = deadline; }
                } else if (g_pad_state[i][b] == 2 && now >= g_pad_since[i][b]) {
                    g_pad_state[i][b] = 0;
                }
                if (g_pad_state[i][b]) c->buttons &= (uint16_t)~(1u<<b);
            }
        }
        c->joyx = (int8_t)(SDL_GameControllerGetAxis(pad,SDL_CONTROLLER_AXIS_LEFTX) / 256);
        c->joyy = (int8_t)(SDL_GameControllerGetAxis(pad,SDL_CONTROLLER_AXIS_LEFTY) / 256);
        int lt=SDL_GameControllerGetAxis(pad,SDL_CONTROLLER_AXIS_TRIGGERLEFT);
        int rt=SDL_GameControllerGetAxis(pad,SDL_CONTROLLER_AXIS_TRIGGERRIGHT);
        if (SDL_GameControllerGetButton(pad,SDL_CONTROLLER_BUTTON_LEFTSHOULDER)) lt=32767;
        if (SDL_GameControllerGetButton(pad,SDL_CONTROLLER_BUTTON_RIGHTSHOULDER)) rt=32767;
        /* Gachettes analogiques : 0 = relachee, 255 = a fond (comme Flycast). */
        { uint8_t vlt=(uint8_t)(lt/128);
          uint8_t vrt=(uint8_t)(rt/128);
          if (vlt > c->ltrig) c->ltrig=vlt;
          if (vrt > c->rtrig) c->rtrig=vrt; }
    }
    /* Un changement d'appui est rare et vaut une ligne de journal : c'est la
     * seule trace qui dise si la manette parle vraiment au jeu. */
    {
        MapleController *c0 = dc_maple_get_controller(hw, 0);
        static uint16_t shown = 0xffff;
        if (c0 && c0->buttons != shown) {
            char ligne[32];
            shown = c0->buttons;
            snprintf(ligne, sizeof ligne, "[PAD] buttons=%04X", shown);
            puts(ligne);
        }
    }
}

int platform_init(int width, int height, const char *title) {
    /* The SDL view can be a child of PhantomStation's launcher window. */
    SDL_SetHint(SDL_HINT_JOYSTICK_ALLOW_BACKGROUND_EVENTS, "1");
    if (SDL_Init(SDL_INIT_VIDEO | SDL_INIT_AUDIO | SDL_INIT_GAMECONTROLLER) < 0) {
        fprintf(stderr, "[PLATFORM] SDL2 init failed: %s\n", SDL_GetError());
        return -1;
    }

    SDL_GL_SetAttribute(SDL_GL_CONTEXT_MAJOR_VERSION, 3);
    SDL_GL_SetAttribute(SDL_GL_CONTEXT_MINOR_VERSION, 3);
    SDL_GL_SetAttribute(SDL_GL_CONTEXT_PROFILE_MASK, SDL_GL_CONTEXT_PROFILE_CORE);
    SDL_GL_SetAttribute(SDL_GL_DOUBLEBUFFER, 1);

    const char *test_w=getenv("DCRECOMP_TEST_WINDOW_WIDTH"), *test_h=getenv("DCRECOMP_TEST_WINDOW_HEIGHT");
    if(test_w && test_h && atoi(test_w)>0 && atoi(test_h)>0) { width=atoi(test_w); height=atoi(test_h); }
    g_window = SDL_CreateWindow(title,
        SDL_WINDOWPOS_CENTERED, SDL_WINDOWPOS_CENTERED,
        width, height,
        SDL_WINDOW_OPENGL | SDL_WINDOW_RESIZABLE | (getenv("DCRECOMP_TEST_HIDDEN") ? SDL_WINDOW_HIDDEN : 0));

    if (!g_window) {
        fprintf(stderr, "[PLATFORM] Window creation failed: %s\n", SDL_GetError());
        return -1;
    }

    g_gl_context = SDL_GL_CreateContext(g_window);
    if (!g_gl_context) {
        fprintf(stderr, "[PLATFORM] GL context failed: %s\n", SDL_GetError());
        return -1;
    }

    if (getenv("PHANTOM_DC_FULLSCREEN")) SDL_SetWindowFullscreen(g_window, SDL_WINDOW_FULLSCREEN_DESKTOP);
    /* No VSync: the swap happens wherever the game starts a render, often in
     * its own interrupt handler, and waiting there for the host display took
     * the SH-4's time - 95% of it in Grandia II, whose movie decoder runs in
     * whatever is left. The game paces itself on the emulated VBlank anyway.
     * PHANTOM_DC_VSYNC=1 brings the wait back. */
    SDL_GL_SetSwapInterval(getenv("PHANTOM_DC_VSYNC") ? 1 : 0);
    if (getenv("DCRECOMP_PCM_AUDIO") || getenv("PHANTOM_DC_AUDIO")) {
        g_audio_polls=g_audio_underruns=g_audio_batches=0;
        g_audio_mixed=g_audio_first_us=g_audio_starve_us=g_audio_starve_start=g_audio_worst_us=0;
        SDL_AudioSpec want = {0}, got = {0};
        want.freq=44100; want.format=AUDIO_S16SYS; want.channels=2; want.samples=512;
        g_audio_device=SDL_OpenAudioDevice(NULL,0,&want,&got,0);
        if (g_audio_device) {
            const char *capture=getenv("DCRECOMP_TEST_PCM_CAPTURE");
            if (capture && *capture) g_audio_capture=fopen(capture,"wbx");
            SDL_PauseAudioDevice(g_audio_device,0);
        }
        else fprintf(stderr,"[AUDIO] %s\n",SDL_GetError());
    }

    printf("[PLATFORM] SDL2 initialized: %dx%d\n", width, height);
    return 0;
}

void platform_shutdown(void) {
    aica_pcm_diagnostic_flush();
    if (g_audio_device) {
        uint64_t now_us=platform_get_ticks_us();
        uint64_t span=g_audio_first_us && now_us>g_audio_first_us ? now_us-g_audio_first_us : 0;
        if (g_audio_starve_start && now_us>g_audio_starve_start)
            g_audio_starve_us+=now_us-g_audio_starve_start;
        double playable=g_audio_mixed/44.1;             /* ms de son réellement produit */
        double span_ms=span/1000.0;
        fprintf(stderr,"[AUDIO-STATS] polls=%llu underruns=%llu batches=%llu\n",
                (unsigned long long)g_audio_polls,(unsigned long long)g_audio_underruns,(unsigned long long)g_audio_batches);
        fprintf(stderr,"[AUDIO-SORTIE] duree=%.0fms son=%.0fms trous=%.0fms (%.1f%%) pire_trou=%.0fms\n",
                span_ms,playable,span_ms-playable,
                span_ms>0 ? 100.0*(span_ms-playable)/span_ms : 0.0,
                g_audio_worst_us/1000.0);
    }
    son_close();
    if (g_audio_capture) { fclose(g_audio_capture); g_audio_capture=NULL; }
    if (g_audio_device) { SDL_CloseAudioDevice(g_audio_device); g_audio_device=0; }
    for (int i=0; i<4; ++i) if (g_pads[i]) { SDL_GameControllerClose(g_pads[i]); g_pads[i]=NULL; }
    if (g_gl_context) SDL_GL_DeleteContext(g_gl_context);
    if (g_window) SDL_DestroyWindow(g_window);
    SDL_Quit();
    printf("[PLATFORM] SDL2 shutdown\n");
}

bool platform_poll_events(DCHardware *hw) {
    uint64_t key_now=SDL_GetTicks64();
    /* Diagnostic input can target a hidden test instance without taking focus. */
    static uint16_t test_key = 0;
    static uint64_t test_key_until = 0;
    const char *test_input = getenv("DCRECOMP_TEST_INPUT_FILE");
    if (test_key && key_now >= test_key_until) {
        g_keyboard_buttons |= test_key;
        test_key = 0;
    }
    if (test_input && !test_key) {
        FILE *input = fopen(test_input, "rb");
        if (input) {
            int c = fgetc(input);
            fclose(input);
            remove(test_input);
            test_key = c == 'S' ? CONT_START : c == 'A' ? CONT_A :
                       c == 'B' ? CONT_B : c == 'U' ? CONT_DPAD_UP :
                       c == 'D' ? CONT_DPAD_DOWN : c == 'L' ? CONT_DPAD_LEFT :
                       c == 'R' ? CONT_DPAD_RIGHT : 0;
            if (test_key) { g_keyboard_buttons &= ~test_key; test_key_until = key_now + 400; fprintf(stderr, "[TEST-FILE-INPUT] %c\n", c); }
        }
    }
    for (int i=0;i<16;++i) if (g_key_release_at[i] && key_now>=g_key_release_at[i]) {
        g_keyboard_buttons |= (uint16_t)(1u<<i);
        g_key_release_at[i]=0;
    }
    /* Opt-in repeated Start presses for repeatable end-to-end diagnostics. */
    static int auto_start = -1, repeat = 0, pressed = 0, start_count = 0, start_limit = 0;
    static uint64_t started, last_press;
    if (auto_start < 0) {
        const char *v = getenv("DCRECOMP_TEST_START_MS");
        const char *r = getenv("DCRECOMP_TEST_START_EVERY_MS");
        const char *l = getenv("DCRECOMP_TEST_START_COUNT");
        auto_start = v ? atoi(v) : 0; repeat = r ? atoi(r) : 0; start_limit = l ? atoi(l) : 0;
        started = SDL_GetTicks64();
    }
    if (auto_start > 0) {
        uint64_t elapsed = SDL_GetTicks64() - started;
        MapleController *ctrl = dc_maple_get_controller(hw, 0);
        if (ctrl && !pressed && (!start_limit || start_count < start_limit) && elapsed >= (uint64_t)auto_start &&
            (!last_press || (repeat > 0 && elapsed - last_press >= (uint64_t)repeat))) {
            g_keyboard_buttons &= ~CONT_START; pressed = 1; last_press = elapsed; start_count++;
            printf("[TEST-INPUT] Start pressed at %llums\n", (unsigned long long)elapsed);
        } else if (ctrl && pressed && elapsed - last_press >= 400) {
            g_keyboard_buttons |= CONT_START; pressed = 0;
            printf("[TEST-INPUT] Start released at %llums\n", (unsigned long long)elapsed);
        }
    }
    static int auto_a = -1, repeat_a = 0, a_pressed = 0;
    static uint64_t a_started, a_last;
    if (auto_a < 0) {
        const char *v = getenv("DCRECOMP_TEST_A_MS");
        const char *r = getenv("DCRECOMP_TEST_A_EVERY_MS");
        auto_a = v ? atoi(v) : 0; repeat_a = r ? atoi(r) : 0;
        a_started = SDL_GetTicks64();
    }
    if (auto_a > 0) {
        uint64_t elapsed = SDL_GetTicks64() - a_started;
        MapleController *ctrl = dc_maple_get_controller(hw, 0);
        if (ctrl && !a_pressed && elapsed >= (uint64_t)auto_a &&
            (!a_last || (repeat_a > 0 && elapsed - a_last >= (uint64_t)repeat_a))) {
            g_keyboard_buttons &= ~CONT_A; a_pressed = 1; a_last = elapsed;
            printf("[TEST-INPUT] A pressed at %llums\n", (unsigned long long)elapsed);
        } else if (ctrl && a_pressed && elapsed - a_last >= 400) {
            g_keyboard_buttons |= CONT_A; a_pressed = 0;
            printf("[TEST-INPUT] A released at %llums\n", (unsigned long long)elapsed);
        }
    }
    /* Essais : gâchette droite enfoncée à partir de DCRECOMP_TEST_RTRIG_MS
     * (l'accélérateur de Crazy Taxi), pour rouler dans la ville. */
    { static long rt_ms = -2; static uint64_t rt_start;
      if (rt_ms == -2) { const char *v = getenv("DCRECOMP_TEST_RTRIG_MS"); rt_ms = v ? atol(v) : -1; rt_start = SDL_GetTicks64(); }
      if (rt_ms >= 0 && SDL_GetTicks64() - rt_start >= (uint64_t)rt_ms) g_key_rtrig = 255; }
    static int auto_down=-1, down_repeat=0, down_limit=0, down_count=0, down_pressed=0;
    static uint64_t down_started, down_last;
    if (auto_down < 0) {
        const char *v=getenv("DCRECOMP_TEST_DOWN_MS"), *r=getenv("DCRECOMP_TEST_DOWN_EVERY_MS"),
                   *l=getenv("DCRECOMP_TEST_DOWN_COUNT");
        auto_down=v?atoi(v):0; down_repeat=r?atoi(r):0; down_limit=l?atoi(l):0;
        down_started=SDL_GetTicks64();
    }
    if (auto_down>0) {
        uint64_t elapsed=SDL_GetTicks64()-down_started;
        if (!down_pressed && (!down_limit || down_count<down_limit) && elapsed>=(uint64_t)auto_down &&
            (!down_last || (down_repeat>0 && elapsed-down_last>=(uint64_t)down_repeat))) {
            g_keyboard_buttons &= ~CONT_DPAD_DOWN; down_pressed=1; down_last=elapsed; down_count++;
            printf("[TEST-INPUT] Down pressed at %llums\n",(unsigned long long)elapsed);
        } else if (down_pressed && elapsed-down_last>=200) {
            g_keyboard_buttons |= CONT_DPAD_DOWN; down_pressed=0;
        }
    }
    static int auto_close=-1, close_sent=0; static uint64_t close_started;
    if (auto_close<0) { const char *v=getenv("DCRECOMP_TEST_CLOSE_MS");
                       auto_close=v?atoi(v):0; close_started=SDL_GetTicks64(); }
    if (auto_close>0 && !close_sent && SDL_GetTicks64()-close_started >= (uint64_t)auto_close) {
        SDL_Event close_event={0}; close_event.type=SDL_QUIT; SDL_PushEvent(&close_event); close_sent=1;
    }
    SDL_Event event;
    while (SDL_PollEvent(&event)) {
        switch (event.type) {
        case SDL_CONTROLLERDEVICEADDED:
            open_pad(event.cdevice.which, hw); break;
        case SDL_CONTROLLERDEVICEREMOVED:
            close_pad(event.cdevice.which, hw); break;
        case SDL_WINDOWEVENT:
            if (event.window.event==SDL_WINDOWEVENT_SIZE_CHANGED) {
                int w,h; SDL_GL_GetDrawableSize(g_window,&w,&h); pvr2_render_resize(w,h);
            }
            break;
        case SDL_QUIT:
            g_running = false;
            break;

        case SDL_KEYDOWN:
        case SDL_KEYUP: {
            if (getenv("DCRECOMP_DEBUG_INPUT"))
                fprintf(stderr,"[INPUT-KEY] type=%s sym=%d scan=%d\n",event.type==SDL_KEYDOWN?"down":"up",event.key.keysym.sym,event.key.keysym.scancode);
            MapleController *ctrl = dc_maple_get_controller(hw, 0);
            if (!ctrl) break;
            bool pressed = (event.type == SDL_KEYDOWN);
            uint32_t bit = 0;

            switch (event.key.keysym.sym) {
            case SDLK_F11:
                /* Active ou coupe les codes de triche charges. */
                if (pressed && !event.key.repeat) dc_cheats_toggle();
                break;

            case SDLK_F12:
                /* F12 toggles fullscreen. Shift+F12 keeps the diagnostic capture. */
                if (pressed && !event.key.repeat && (event.key.keysym.mod & KMOD_SHIFT)) {
                    static int captures;
                    char chemin[256];
                    snprintf(chemin, sizeof chemin, "capture-%03d.ppm", ++captures);
                    pvr2_screenshot(chemin);
                    puts(chemin);
                } else if (pressed && !event.key.repeat) {
                    Uint32 flags = SDL_GetWindowFlags(g_window);
                    SDL_SetWindowFullscreen(g_window,
                        (flags & SDL_WINDOW_FULLSCREEN) ? 0 : SDL_WINDOW_FULLSCREEN_DESKTOP);
                }
                break;
            case SDLK_UP:     bit = CONT_DPAD_UP; break;
            case SDLK_DOWN:   bit = CONT_DPAD_DOWN; break;
            case SDLK_LEFT:   bit = CONT_DPAD_LEFT; break;
            case SDLK_RIGHT:  bit = CONT_DPAD_RIGHT; break;
            case SDLK_z:      bit = CONT_A; break;
            case SDLK_x:      bit = CONT_B; break;
            case SDLK_a:      bit = CONT_X; break;
            case SDLK_s:      bit = CONT_Y; break;
            case SDLK_RETURN: bit = CONT_START; break;
            case SDLK_q:      /* L trigger */
                g_key_ltrig = pressed ? 255 : 0;
                break;
            case SDLK_w:      /* R trigger */
                g_key_rtrig = pressed ? 255 : 0;
                break;
            default: break;
            }

            if (bit) {
                /* Buttons are active LOW on Dreamcast */
                for (int i=0;i<16;++i) if (bit & (1u<<i)) {
                    uint64_t now=SDL_GetTicks64();
                    if (pressed) {
                        if (g_keyboard_buttons & bit) g_key_down_at[i]=now;
                        g_keyboard_buttons &= ~bit; g_key_release_at[i]=0;
                    } else {
                        uint64_t earliest=g_key_down_at[i]+80;
                        g_key_release_at[i]=now>=earliest ? now : earliest;
                    }
                }
            }
            break;
        }
        }
    }
    static bool enumerated = false;
    if (!enumerated) {
        enumerated = true;
        for (int i=0; i<SDL_NumJoysticks(); ++i) open_pad(i,hw);
    }
    SDL_GameControllerUpdate(); poll_pads(hw);
    if (g_audio_device) {
        ++g_audio_polls; platform_audio_pump();
    } else {
        /* Sound off on this side, but the sound chip still plays: streamed
         * music and movie players time themselves on its play position.
         * Nothing consumed samples without a device, so the position stood
         * still and Grandia II's intro movie never left its first frame. */
        static uint64_t last_us, owed_us;
        uint64_t now_us=platform_get_ticks_us();
        if (last_us) {
            uint64_t elapsed=now_us-last_us;
            owed_us+=elapsed > 100000 ? 100000 : elapsed;
            static int16_t sink[735*2];
            while (owed_us >= 16667) { dc_ralentis_debut(RAL_SON); dc_aica_mix(sink,735); dc_ralentis_fin(RAL_SON); son_feed(sink,735); owed_us-=16667; }   /* 735 frames at 44.1 kHz */
        }
        last_us=now_us;
    }
    dc_cinematic_update(hw);
    return g_running;
}

void platform_swap_buffers(void) {
    { static int base_vue;
      if (!base_vue) { dc_cheats_set_base(SDL_GetBasePath()); base_vue = 1; } }
    dc_cheats_tick();
    extern void pvr2_osd_frame(void);
    pvr2_osd_frame();
    /* A test may capture the composed frame; normal games do not create files. */
    { static int checked; static const char *shot;
      if (!checked) { shot = getenv("DCRECOMP_TEST_OSD_SCREENSHOT"); checked = 1; }
      if (shot && dc_cinematic_message()) { pvr2_screenshot(shot); shot = NULL; } }
    SDL_GL_SwapWindow(g_window);
    /* Optional stress test for occasional long rendering stalls. */
    static int stall=-1; static unsigned swaps;
    if (stall<0) { const char *v=getenv("DCRECOMP_TEST_RENDER_STALL_MS");stall=v?atoi(v):0; }
    if (stall>0 && ++swaps%30u==0) SDL_Delay((uint32_t)stall);
}
void platform_get_drawable_size(int *w,int *h) { SDL_GL_GetDrawableSize(g_window,w,h); }


void platform_set_title(const char *title) {
    if (g_window) SDL_SetWindowTitle(g_window, title);
}

uint64_t platform_get_ticks_ms(void) {
    return SDL_GetTicks64();
}

uint64_t platform_get_ticks_us(void) {
    static Uint64 freq;
    if (!freq) freq = SDL_GetPerformanceFrequency();
    return (uint64_t)((SDL_GetPerformanceCounter() * 1000000) / freq);
}

void platform_sleep_ms(uint32_t ms) {
    SDL_Delay(ms);
}

#endif /* HAS_SDL2 */
