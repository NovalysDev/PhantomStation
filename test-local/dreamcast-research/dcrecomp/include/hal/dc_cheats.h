#pragma once

/* Codes de triche Dreamcast.
 *
 * Lit un fichier .cht au format RetroArch (celui de libretro-database pour
 * "Sega - Dreamcast", egalement lu par Flycast) et reapplique les codes
 * actives a la RAM SH-4. F11 coupe et remet les codes en jeu.
 */

void dc_cheats_set_base(const char *base);   /* dossier de l'exe */
void dc_cheats_tick(void);      /* a appeler une fois par image */
void dc_cheats_toggle(void);    /* bascule actif/inactif */
int  dc_cheats_active(void);    /* 1 si les codes sont appliques */
