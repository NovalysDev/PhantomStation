/**
 * Codes de triche Dreamcast.
 *
 * Le fichier lu est un .cht au format RetroArch, exactement celui de
 * libretro-database (dossier "Sega - Dreamcast"), que Flycast lit aussi :
 *
 *   cheat0_desc = "Argent infini"
 *   cheat0_address = "14869692"      offset decimal dans la RAM (16 Mo)
 *   cheat0_value = "99999999"        valeur decimale
 *   cheat0_memory_search_size = "4"  1, 2 ou 4 octets
 *   cheat0_enable = "false"
 *
 * Aucun dechiffrement n'est necessaire : la base ne stocke pas des codes
 * Action Replay mais des couples adresse/valeur deja resolus.
 *
 * Activation d'un code : mettre cheatN_enable a "true", ou ecrire son
 * numero sur une ligne de phantom-cheats.txt (une ligne "# 3 Argent" est
 * un commentaire, retirer le "#" active le code 3).
 *
 * Fichier cherche dans cet ordre : PHANTOM_DC_CHEATS, phantom-cheats.cht,
 * cheats/phantom-cheats.cht.
 *
 * Les codes ne sont appliques qu'apres PHANTOM_DC_CHEATS_DELAI secondes
 * (5 par defaut) pour ne pas ecraser la RAM pendant le demarrage du jeu,
 * puis toutes les 30 images.
 */

#include "hal/dc_cheats.h"
#include "recompiler/sh4_cpu.h"

#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define CHEATS_MAX       512
#define CHEATS_PERIODE   30u
#define CHEATS_LIGNES_LOG 40

typedef struct {
    char      desc[96];
    unsigned  address;
    unsigned  value;
    int       size;
    int       enable;
    int       defini;
} CodeTriche;

static CodeTriche g_codes[CHEATS_MAX];
static int         g_nb;
static int         g_charges;
static int         g_actifs = 1;
static int         g_ecrit_log;
static unsigned    g_image;
static unsigned    g_delai_images = 300u;   /* 5 s a 60 images/s */
static char        g_fichier[400];
static char        g_base[400];

static char *nettoie(char *s) {
    while (*s && isspace((unsigned char)*s)) ++s;
    char *fin = s + strlen(s);
    while (fin > s && isspace((unsigned char)fin[-1])) --fin;
    *fin = 0;
    return s;
}

/* "cheat12_desc = \"texte\"" -> index 12, cle "desc", valeur "texte". */
static int decoupe(const char *ligne, int *index, char *cle, size_t cle_t,
                   char *valeur, size_t valeur_t) {
    if (strncmp(ligne, "cheat", 5) != 0) return 0;
    char *fin = 0;
    long n = strtol(ligne + 5, &fin, 10);
    if (!fin || fin == ligne + 5 || *fin != '_' || n < 0 || n >= CHEATS_MAX) return 0;
    const char *p = fin + 1;
    const char *egal = strchr(p, '=');
    if (!egal) return 0;
    size_t ncle = (size_t)(egal - p);
    while (ncle && isspace((unsigned char)p[ncle - 1])) --ncle;
    if (!ncle || ncle >= cle_t) return 0;
    memcpy(cle, p, ncle);
    cle[ncle] = 0;
    p = egal + 1;
    while (*p == ' ' || *p == '\t') ++p;
    if (*p == '"') {
        ++p;
        const char *q = strchr(p, '"');
        if (!q) return 0;
        size_t n = (size_t)(q - p);
        if (n >= valeur_t) n = valeur_t - 1;
        memcpy(valeur, p, n);
        valeur[n] = 0;
    } else {
        char tampon[128];
        size_t i = 0;
        while (*p && i + 1 < sizeof(tampon)) tampon[i++] = *p++;
        tampon[i] = 0;
        char *v = nettoie(tampon);
        snprintf(valeur, valeur_t, "%s", v);
    }
    *index = (int)n;
    return 1;
}

static int meme_debut(const char *a, const char *b) {
    while (*a && *b) {
        if (tolower((unsigned char)*a) != tolower((unsigned char)*b)) return 0;
        ++a; ++b;
    }
    return *b == 0;
}

static int contient(const char *texte, const char *motif) {
    if (!*motif) return 0;
    size_t n = strlen(motif);
    for (const char *p = texte; *p; ++p) {
        size_t i = 0;
        while (i < n && p[i] &&
               tolower((unsigned char)p[i]) == tolower((unsigned char)motif[i])) ++i;
        if (i == n) return 1;
    }
    return 0;
}

static void charge_liste_texte(void) {
    char base1[800], base2[800];
    const char *chemins[4];
    unsigned n = 0;
    if (g_base[0]) {
        snprintf(base1, sizeof(base1), "%sphantom-cheats.txt", g_base);
        snprintf(base2, sizeof(base2), "%scheats/phantom-cheats.txt", g_base);
        chemins[n++] = base1;
        chemins[n++] = base2;
    }
    chemins[n++] = "phantom-cheats.txt";
    chemins[n++] = "cheats/phantom-cheats.txt";
    for (unsigned i = 0; i < n; ++i) {
        FILE *f = fopen(chemins[i], "rb");
        if (!f) continue;
        char ligne[256];
        while (fgets(ligne, sizeof(ligne), f)) {
            char *l = nettoie(ligne);
            if (!*l || *l == '#') continue;
            char *fin = 0;
            long n = strtol(l, &fin, 10);
            if (fin && fin != l && *nettoie(fin) == 0) {
                if (n >= 0 && n < g_nb && g_codes[n].defini) g_codes[n].enable = 1;
                continue;
            }
            for (int c = 0; c < g_nb; ++c)
                if (g_codes[c].defini && contient(g_codes[c].desc, l)) g_codes[c].enable = 1;
        }
        fclose(f);
        printf("[CHEAT] liste d'activation : %s\n", chemins[i]);
        return;
    }
}

static int charge_fichier(const char *chemin) {
    FILE *f = fopen(chemin, "rb");
    if (!f) return 0;
    char ligne[512];
    while (fgets(ligne, sizeof(ligne), f)) {
        int index = -1;
        char cle[48], valeur[160];
        if (!decoupe(ligne, &index, cle, sizeof(cle), valeur, sizeof(valeur))) continue;
        CodeTriche *c = &g_codes[index];
        if (meme_debut(cle, "desc")) { snprintf(c->desc, sizeof(c->desc), "%s", valeur); c->defini = 1; }
        else if (meme_debut(cle, "address")) { c->address = (unsigned)strtoul(valeur, 0, 10); }
        else if (meme_debut(cle, "value")) { c->value = (unsigned)strtoul(valeur, 0, 10); }
        else if (meme_debut(cle, "memory_search_size")) { c->size = atoi(valeur); }
        else if (meme_debut(cle, "enable")) { c->enable = (valeur[0] == 't' || valeur[0] == 'T' || valeur[0] == '1'); }
    }
    fclose(f);
    g_nb = CHEATS_MAX;
    while (g_nb > 0 && !g_codes[g_nb - 1].defini) --g_nb;
    snprintf(g_fichier, sizeof(g_fichier), "%s", chemin);
    return 1;
}

static void charge(void) {
    const char *delai = getenv("PHANTOM_DC_CHEATS_DELAI");
    if (delai && *delai) {
        int s = atoi(delai);
        if (s >= 0) g_delai_images = (unsigned)s * 60u;
    }

    char base1[800], base2[800];
    const char *force = getenv("PHANTOM_DC_CHEATS");
    int ok = 0;
    if (force && *force) ok = charge_fichier(force);
    if (!ok && g_base[0]) {
        snprintf(base1, sizeof(base1), "%sphantom-cheats.cht", g_base);
        ok = charge_fichier(base1);
    }
    if (!ok && g_base[0]) {
        snprintf(base2, sizeof(base2), "%scheats/phantom-cheats.cht", g_base);
        ok = charge_fichier(base2);
    }
    if (!ok) ok = charge_fichier("phantom-cheats.cht");
    if (!ok) ok = charge_fichier("cheats/phantom-cheats.cht");

    if (!ok) {
        printf("[CHEAT] aucun fichier .cht (phantom-cheats.cht)\n");
        g_charges = 1;
        return;
    }

    int actifs = 0, listes = 0;
    for (int i = 0; i < g_nb; ++i) {
        CodeTriche *c = &g_codes[i];
        if (c->size != 1 && c->size != 2 && c->size != 4) c->size = 4;
        if (c->defini) ++listes;
        if (c->defini && c->enable) ++actifs;
    }
    charge_liste_texte();
    actifs = 0;
    for (int i = 0; i < g_nb; ++i) if (g_codes[i].defini && g_codes[i].enable) ++actifs;

    printf("[CHEAT] fichier=%s codes=%d actifs=%d (F11 pour couper)\n",
           g_fichier, listes, actifs);
    int montres = 0;
    for (int i = 0; i < g_nb && montres < CHEATS_LIGNES_LOG; ++i) {
        if (!g_codes[i].defini) continue;
        printf("[CHEAT] %s %3d : %s\n", g_codes[i].enable ? "*" : " ", i,
               g_codes[i].desc[0] ? g_codes[i].desc : "(sans nom)");
        ++montres;
    }
    if (!actifs)
        printf("[CHEAT] aucun code actif : ecris son numero dans phantom-cheats.txt\n");
    g_charges = 1;
}

static void applique(void) {
    uint8_t *ram = sh4_get_ram_ptr();
    if (!ram) return;
    unsigned taille = sh4_get_ram_size();
    unsigned masque = sh4_get_ram_mask();
    int ecrits = 0;
    for (int i = 0; i < g_nb; ++i) {
        CodeTriche *c = &g_codes[i];
        if (!c->defini || !c->enable) continue;
        /* Deux conventions circulent : offset brut dans la RAM, ou adresse
         * de jeu 0x8Cxxxxxx / 0x0Cxxxxxx. */
        unsigned off = c->address;
        if (off >= 0x8C000000u) off -= 0x8C000000u;
        else if (off >= 0x0C000000u) off -= 0x0C000000u;
        off &= masque;
        if (off + (unsigned)c->size > taille) continue;
        for (int b = 0; b < c->size; ++b)
            ram[off + (unsigned)b] = (uint8_t)((c->value >> (8 * b)) & 0xFFu);
        if (!g_ecrit_log) {
            printf("[CHEAT] ecriture RAM 0x%06X = %u (%d octet(s)) : %s\n",
                   off, c->value, c->size, c->desc);
            g_ecrit_log = 1;
        }
        ++ecrits;
    }
    (void)ecrits;
}

void dc_cheats_set_base(const char *base) {
    if (base && *base) snprintf(g_base, sizeof(g_base), "%s", base);
}

void dc_cheats_tick(void) {
    if (!g_charges) charge();
    if (!g_charges || !g_actifs) return;
    if (++g_image < g_delai_images) return;
    if ((g_image - g_delai_images) % CHEATS_PERIODE) return;
    applique();
}

void dc_cheats_toggle(void) {
    if (!g_charges) charge();
    g_actifs = !g_actifs;
    printf("[CHEAT] codes %s\n", g_actifs ? "actives" : "coupes");
}

int dc_cheats_active(void) { return g_actifs; }
