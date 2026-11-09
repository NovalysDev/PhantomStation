#ifndef DCRECOMP_DC_RALENTIS_H
#define DCRECOMP_DC_RALENTIS_H
/* Surveillance des ralentissements et des gels (voir src/hal/dc_ralentis.c). */
enum { RAL_DISQUE, RAL_TEXTURES, RAL_DESSIN, RAL_AFFICHAGE, RAL_SON, RAL_ARM7, RAL_N };
struct SH4CPU;
void dc_ralentis_debut(int quoi);
void dc_ralentis_fin(int quoi);
void dc_ralentis_image(void);                          /* à chaque image dessinée */
void dc_ralentis_surveiller(const struct SH4CPU *cpu); /* lance la veille des gels */
#endif
