/**
 * Codes de triche Dreamcast.
 *
 * Le fichier lu est un .cht au format RetroArch, exactement celui de
 * libretro-database (dossier "Sega - Dreamcast"), que Flycast lit aussi :
 *
 *   cheat0_desc = "Argent infini"
 *   cheat0_address = "14869692"      offset decimal dans la RAM (16 Mo)
 *   cheat0_value = "99999999"        valeur decimale
 *   cheat0_memory_search_size = "5"  RetroArch : 3/4/5 pour 8/16/32 bits
 *   cheat0_enable = "false"
 *
 * Aucun dechiffrement n'est necessaire : la base ne stocke pas des codes
 * Action Replay mais des couples adresse/valeur deja resolus.
 *
 * Selection individuelle dans le menu F11. Les formats non pris en charge
 * ne sont pas proposes pour eviter une ecriture incorrecte en RAM.
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
#include <errno.h>
#include <limits.h>
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
    int       adresse_presente, valeur_presente, taille_presente;
    int       taille_retro, type, handler, repetition, big_endian;
    int       compatible;
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
static int         g_menu, g_selection;

static char *nettoie(char *s) {
    while (*s && isspace((unsigned char)*s)) ++s;
    char *fin = s + strlen(s);
    while (fin > s && isspace((unsigned char)fin[-1])) --fin;
    *fin = 0;
    return s;
}

static int entier_decimal(const char *s, unsigned *out) {
    char *end;
    if (!s || !isdigit((unsigned char)*s)) return 0;
    errno = 0;
    unsigned long value = strtoul(s, &end, 10);
    if (errno || *end || value > UINT_MAX) return 0;
    *out = (unsigned)value;
    return 1;
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
        else if (!strcmp(cle, "address")) c->adresse_presente = entier_decimal(valeur, &c->address);
        else if (!strcmp(cle, "value")) c->valeur_presente = entier_decimal(valeur, &c->value);
        else if (!strcmp(cle, "memory_search_size")) {
            unsigned size;
            c->taille_presente = entier_decimal(valeur, &size) && size <= 5;
            if (c->taille_presente) c->taille_retro = (int)size;
        }
        else if (!strcmp(cle, "cheat_type")) c->type = atoi(valeur);
        else if (!strcmp(cle, "handler")) c->handler = atoi(valeur);
        else if (!strcmp(cle, "repeat_count")) c->repetition = atoi(valeur);
        else if (!strcmp(cle, "big_endian")) c->big_endian = (valeur[0]=='t' || valeur[0]=='T' || valeur[0]=='1');
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
        c->size = c->taille_retro == 3 ? 1 : c->taille_retro == 4 ? 2 : c->taille_retro == 5 ? 4 : 0;
        unsigned off = c->address;
        if (off >= 0x8c000000u && off < 0x8d000000u) off -= 0x8c000000u;
        else if (off >= 0x0c000000u && off < 0x0d000000u) off -= 0x0c000000u;
        c->compatible = c->defini && c->adresse_presente && c->valeur_presente && c->taille_presente &&
            c->size && off < 0x01000000u && off + (unsigned)c->size <= 0x01000000u &&
            (c->size == 4 || c->value < (1u << (8 * c->size))) &&
            c->type == 1 && c->handler == 1 && c->repetition <= 1 && !c->big_endian;
        if (!c->compatible) c->enable = 0;
        if (c->defini) ++listes;
        if (c->defini && c->enable) ++actifs;
    }
    charge_liste_texte();
    actifs = 0;
    for (int i = 0; i < g_nb; ++i) {
        if (!g_codes[i].compatible) g_codes[i].enable = 0;
        if (g_codes[i].compatible && g_codes[i].enable) ++actifs;
    }

    printf("[CHEAT] fichier=%s codes=%d actifs=%d (F11 pour choisir)\n",
           g_fichier, listes, actifs);
    int montres = 0;
    for (int i = 0; i < g_nb && montres < CHEATS_LIGNES_LOG; ++i) {
        if (!g_codes[i].defini) continue;
        printf("[CHEAT] %s %3d : %s\n", g_codes[i].enable ? "*" : " ", i,
               g_codes[i].desc[0] ? g_codes[i].desc : "(sans nom)");
        ++montres;
    }
    if (!actifs)
        printf("[CHEAT] aucun code actif : selectionne-le dans le menu F11\n");
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
        if (!c->compatible || !c->enable) continue;
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

static int menu_count(void) {
    int count = 0;
    for (int i=0; i<g_nb; ++i) if (g_codes[i].compatible) ++count;
    return count;
}

static int menu_index(int row) {
    for (int i=0; i<g_nb; ++i)
        if (g_codes[i].compatible && row-- == 0) return i;
    return -1;
}

void dc_cheats_menu_toggle(void) {
    if (!g_charges) charge();
    g_menu = !g_menu;
    if (g_selection >= menu_count()) g_selection = 0;
}
int dc_cheats_menu_is_open(void) { return g_menu; }
void dc_cheats_menu_move(int direction) {
    int count = menu_count();
    if (!g_menu || !count || !direction) return;
    g_selection = (g_selection + (direction > 0 ? 1 : count - 1)) % count;
}
void dc_cheats_menu_select(void) {
    if (!g_menu) return;
    int index = menu_index(g_selection);
    if (index < 0) return;
    g_codes[index].enable = !g_codes[index].enable;
    printf("[CHEAT] %s : %s\n", g_codes[index].desc, g_codes[index].enable ? "actif" : "inactif");
}
const char *dc_cheats_menu_text(void) {
    static char text[1024];
    if (!g_menu) return NULL;
    int count = menu_count();
    /* Meme langue que PhantomStation (PHANTOM_LANGUAGE), comme dc_cinematic.c. */
    const char *language = getenv("PHANTOM_LANGUAGE");
    int en = language && strcmp(language, "en") == 0;
    size_t pos = (size_t)snprintf(text, sizeof text, en ? "CHEATS - THIS SESSION\n" : "CODES DE TRICHE - CETTE PARTIE\n");
    if (!count) {
        snprintf(text + pos, sizeof text - pos, en ? "NO COMPATIBLE CHEAT FOR THIS GAME\nF11 TO CLOSE"
                                                   : "AUCUN CODE COMPATIBLE POUR CE JEU\nF11 POUR FERMER");
        return text;
    }
    int start = (g_selection / 6) * 6;
    for (int row=start; row<count && row<start+6; ++row) {
        int index = menu_index(row);
        int written = snprintf(text + pos, sizeof text - pos, "%c %s %.42s\n",
            row == g_selection ? '>' : ' ', g_codes[index].enable ? "ON " : "OFF", g_codes[index].desc);
        if (written < 0 || (size_t)written >= sizeof text - pos) break;
        pos += (size_t)written;
    }
    snprintf(text + pos, sizeof text - pos, en ? "UP DOWN: CHOOSE  ENTER/A: TOGGLE\nF11/B: CLOSE  %d/%d"
                                               : "HAUT BAS : CHOISIR  ENTREE/A : ACTIVER\nF11/B : FERMER  %d/%d",
             g_selection+1, count);
    return text;
}
