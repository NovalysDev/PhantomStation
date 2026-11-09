/* Surveillance des ralentissements pendant les vraies parties.
 *
 * Le temps de chaque image est découpé : lecture du disque, décodage des
 * textures, dessin, affichage à l'écran, son, processeur du son (ARM7), et le
 * reste, qui est le programme du jeu lui-même. Une image qui met plus de
 * 40 ms (moins de 25 images par seconde) est notée avec ce découpage, et
 * chaque minute un bilan est écrit. Sans ça, un ralentissement vu par le
 * joueur ne laissait aucune trace.
 *
 * Fichier : PHANTOM_DC_RALENTIS s'il est donné ; sinon, à côté de la partie
 * notée par le lanceur (PHANTOM_DC_LEARN_DIR = ...\<disque>\partie-<date>),
 * dans ...\<disque>\ralentis-<date>.txt. */
#include "hal/dc_ralentis.h"
#include "recompiler/sh4_cpu.h"
#include "platform/platform.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static const char *noms[RAL_N] = { "disque", "textures", "dessin", "affichage", "son", "arm7" };
static uint64_t depuis[RAL_N];          /* début de la mesure en cours */
static uint64_t image_us[RAL_N];        /* cumul depuis l'image précédente */
static uint64_t minute_us[RAL_N];       /* cumul depuis le dernier bilan */
static int profondeur[RAL_N];
static FILE *journal;
static int ouvert;
static uint64_t debut_partie, derniere_image, debut_minute;
static unsigned images_minute, lentes_minute, lignes;
static uint64_t pire_minute;

static void ouvrir(void) {
    if (ouvert) return;
    ouvert = 1;
    const char *chemin = getenv("PHANTOM_DC_RALENTIS");
    char tampon[2048];
    if (!chemin || !*chemin) {
        const char *partie = getenv("PHANTOM_DC_LEARN_DIR");
        if (!partie || !*partie) return;
        const char *nom = strrchr(partie, '\\'), *nom2 = strrchr(partie, '/');
        if (nom2 > nom) nom = nom2;
        if (!nom) return;
        const char *date = strstr(nom, "partie-");
        snprintf(tampon, sizeof tampon, "%.*s\\ralentis-%s.txt", (int)(nom - partie), partie,
                 date ? date + 7 : nom + 1);
        chemin = tampon;
    }
    journal = fopen(chemin, "w");
    if (journal) fprintf(journal, "Ralentissements de la partie (une ligne par image de plus de 40 ms, un bilan par minute)\n");
}

void dc_ralentis_debut(int quoi) {
    if (quoi < 0 || quoi >= RAL_N) return;
    if (profondeur[quoi]++ == 0) depuis[quoi] = platform_get_ticks_us();
}

void dc_ralentis_fin(int quoi) {
    if (quoi < 0 || quoi >= RAL_N || profondeur[quoi] == 0) return;
    if (--profondeur[quoi] == 0) {
        uint64_t d = platform_get_ticks_us() - depuis[quoi];
        image_us[quoi] += d; minute_us[quoi] += d;
    }
}

static void decoupage(FILE *f, const uint64_t *us, uint64_t total) {
    uint64_t mesure = 0;
    for (int i = 0; i < RAL_N; i++) {
        if (i != RAL_TEXTURES) mesure += us[i];   /* les textures font partie du dessin */
        if (us[i] >= 1000) fprintf(f, " %s %llu", noms[i], (unsigned long long)(us[i] / 1000));
    }
    fprintf(f, " jeu %llu ms\n", (unsigned long long)((total > mesure ? total - mesure : 0) / 1000));
}

/* Gels : un fil à part regarde toutes les demi-secondes si une image a été
 * dessinée. Après 5 s sans image, il note où en est le programme du jeu
 * (adresses lues sans l'arrêter : une indication, pas un relevé exact), puis
 * la reprise si le jeu repart. Un chargement long apparaît aussi, mais court. */
static const SH4CPU *g_cpu_surveille;
static volatile uint64_t g_derniere_image_us;
static volatile unsigned g_images;

#ifdef _WIN32
#include <windows.h>
static DWORD WINAPI veille_gels(void *param) {
    (void)param;
    int gele = 0, rien_signale = 0; uint64_t debut_gel = 0, lancement = platform_get_ticks_us();
    for (;;) {
        Sleep(500);
        if (!g_images) {
            /* Aucune image depuis le lancement : bloqué avant même le premier écran. */
            uint64_t now = platform_get_ticks_us();
            if (!rien_signale && now - lancement > 20000000ull) {
                rien_signale = 1;
                ouvrir();
                const SH4CPU *c = g_cpu_surveille;
                if (journal) {
                    fprintf(journal, "   20.0 s  GEL : aucune image depuis le lancement");
                    if (c) fprintf(journal, " ; le jeu est en pc=%08X pr=%08X r15=%08X sr=%08X", c->pc, c->pr, c->r[15], c->sr);
                    fprintf(journal, "\n"); fflush(journal);
                }
            }
            continue;
        }
        if (!journal) continue;
        uint64_t now = platform_get_ticks_us(), derniere = g_derniere_image_us;
        if (!gele && now - derniere > 5000000ull) {
            const SH4CPU *c = g_cpu_surveille;
            gele = 1; debut_gel = derniere;
            fprintf(journal, "%7.1f s  GEL : plus aucune image depuis 5 s", (now - debut_partie) / 1e6);
            if (c) fprintf(journal, " ; le jeu est en pc=%08X pr=%08X r15=%08X sr=%08X", c->pc, c->pr, c->r[15], c->sr);
            fprintf(journal, "\n"); fflush(journal);
            /* Atelier : PHANTOM_DC_GEL_RAM=fichier garde la mémoire du jeu au moment du gel. */
            const char *ram = getenv("PHANTOM_DC_GEL_RAM");
            if (ram && c && c->ram) {
                FILE *f = fopen(ram, "wb");
                if (f) { fwrite(c->ram, 1, 16u << 20, f); fclose(f); }
            }
        } else if (gele && derniere > debut_gel) {
            gele = 0;
            fprintf(journal, "%7.1f s  reprise après %.1f s sans image\n", (now - debut_partie) / 1e6,
                    (derniere - debut_gel) / 1e6);
            fflush(journal);
        }
    }
    return 0;
}
#endif

void dc_ralentis_surveiller(const SH4CPU *cpu) {
    g_cpu_surveille = cpu;
#ifdef _WIN32
    static int lance;
    if (!lance++) CreateThread(NULL, 0, veille_gels, NULL, 0, NULL);
#endif
}

void dc_ralentis_image(void) {
    uint64_t now = platform_get_ticks_us();
    g_derniere_image_us = now; g_images++;
    if (!debut_partie) { debut_partie = debut_minute = derniere_image = now; memset(image_us, 0, sizeof image_us); return; }
    ouvrir();
    uint64_t duree = now - derniere_image;
    derniere_image = now;
    images_minute++;
    if (duree > pire_minute) pire_minute = duree;
    if (duree > 40000) {
        lentes_minute++;
        if (journal && lignes++ < 5000) {
            fprintf(journal, "%7.1f s  image de %llu ms :", (now - debut_partie) / 1e6, (unsigned long long)(duree / 1000));
            decoupage(journal, image_us, duree);
        }
    }
    memset(image_us, 0, sizeof image_us);
    if (now - debut_minute >= 60000000ull) {
        if (journal) {
            uint64_t total = now - debut_minute;
            fprintf(journal, "--- minute %llu : %u images (%.1f par seconde), %u lentes, la pire %llu ms ; temps :",
                    (unsigned long long)((now - debut_partie) / 60000000ull), images_minute, images_minute * 1e6 / (double)total,
                    lentes_minute, (unsigned long long)(pire_minute / 1000));
            decoupage(journal, minute_us, total);
            fflush(journal);
        }
        memset(minute_us, 0, sizeof minute_us);
        debut_minute = now; images_minute = lentes_minute = 0; pire_minute = 0;
    }
}
