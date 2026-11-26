/* Jeux Dreamcast sur plusieurs disques : voir hal/dc_disques.h. */
#include "hal/dc_disques.h"
#include "platform/platform.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define PLATEAU_OUVERT_US 1500000ull   /* le jeu doit voir le plateau ouvert */
#define MESSAGE_US        4000000ull

static int g_nombre = 1, g_courant = 0, g_cible = -1;
static int (*g_changer)(int index);
static uint64_t g_ouvert_depuis, g_message_depuis;
static int g_message_erreur, g_message_actif;

void dc_disques_configure(int nombre, int (*changer)(int index)) {
    g_nombre = nombre > 0 ? nombre : 1;
    g_courant = 0; g_cible = -1;
    g_changer = changer;
}

int dc_disques_nombre(void) { return g_nombre; }
int dc_disques_courant(void) { return g_courant; }

void dc_disques_suivant(void) {
    g_message_depuis = platform_get_ticks_us();
    g_message_erreur = 0; g_message_actif = 1;
    if (g_nombre < 2 || !g_changer) return;          /* le message dit « un seul disque » */
    if (g_cible < 0) g_ouvert_depuis = g_message_depuis;
    g_cible = ((g_cible < 0 ? g_courant : g_cible) + 1) % g_nombre;
    printf("[DISQUE] plateau ouvert : disque %d demande\n", g_cible + 1);
}

int dc_disques_etat_lecteur(void) {
    if (g_cible < 0) return 2;
    if (platform_get_ticks_us() - g_ouvert_depuis < PLATEAU_OUVERT_US) return 6;
    int cible = g_cible;
    g_cible = -1;
    if (cible != g_courant) {
        if (g_changer(cible) == 0) {
            g_courant = cible;
            printf("[DISQUE] disque %d en place, plateau ferme\n", cible + 1);
        } else {
            g_message_erreur = 1; g_message_actif = 1;
            g_message_depuis = platform_get_ticks_us();
            printf("[DISQUE] disque %d illisible : le disque %d reste en place\n", cible + 1, g_courant + 1);
        }
    }
    return 2;
}

const char *dc_disques_message(void) {
    static char texte[96];
    if (!g_message_actif || platform_get_ticks_us() - g_message_depuis >= MESSAGE_US) { g_message_actif = 0; return NULL; }
    const char *langue = getenv("PHANTOM_LANGUAGE");
    int en = langue && strcmp(langue, "en") == 0;
    if (g_nombre < 2) {
        snprintf(texte, sizeof texte, en ? "THIS GAME HAS ONLY ONE DISC" : "CE JEU N'A QU'UN DISQUE");
    } else if (g_message_erreur) {
        snprintf(texte, sizeof texte, en ? "DISC UNREADABLE - DISC %d STAYS IN" : "DISQUE ILLISIBLE - DISQUE %d GARDE", g_courant + 1);
    } else {
        int vu = (g_cible >= 0 ? g_cible : g_courant) + 1;
        snprintf(texte, sizeof texte, en ? "DISC %d/%d - F4: NEXT DISC" : "DISQUE %d/%d - F4 : DISQUE SUIVANT", vu, g_nombre);
    }
    return texte;
}
