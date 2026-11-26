/**
 * Jeux Dreamcast sur plusieurs disques.
 *
 * Tous les disques d'un jeu sont fournis au moteur ; F4 passe au disque
 * suivant comme sur la console : le plateau reste « ouvert » un instant (le
 * BIOS répond 6 à GetDrvStat), puis le nouveau disque est en place et le
 * plateau refermé. Le jeu qui demandait « insérez le disque 2 » le voit.
 */
#ifndef DC_DISQUES_H
#define DC_DISQUES_H

#include <stdint.h>

/* Le programme principal donne le nombre de disques et la fonction qui
 * remplace le disque en place (0 si réussi). */
void dc_disques_configure(int nombre, int (*changer)(int index));
int dc_disques_nombre(void);
int dc_disques_courant(void);          /* 0 pour le premier disque */
void dc_disques_suivant(void);         /* touche F4 */
/* État du lecteur pour GetDrvStat : 2 (disque au repos) ou 6 (plateau
 * ouvert). Fait aussi le changement de disque quand le plateau se referme. */
int dc_disques_etat_lecteur(void);
/* Texte du bandeau à l'écran pendant quelques secondes après F4, sinon NULL. */
const char *dc_disques_message(void);

#endif
