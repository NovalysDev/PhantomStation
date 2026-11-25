/* Essais du processeur d'effets (DSP) de la puce son.
 *
 * Inclut aica_pcm.c pour atteindre ses fonctions internes (format de
 * l'anneau). Programmes écrits à la main ; aucune donnée de jeu.
 *   - un son envoyé au DSP et recopié vers une sortie sort au même niveau
 *     que le même son en sortie directe ;
 *   - un écho passé par l'anneau en mémoire son arrive avec le retard voulu,
 *     au même niveau ;
 *   - le format 16 bits de l'anneau garde 11 bits de précision. */
#include "../dcrecomp/src/hal/aica_pcm.c"
#include <assert.h>

static uint32_t regs[0x8000 / 4];

/* Une instruction : les quatre mots de 16 bits d'un pas. */
static void pas(int n, uint32_t i0, uint32_t i1, uint32_t i2, uint32_t i3) {
    regs[DSP_MPRO + n * 4 + 0] = i0; regs[DSP_MPRO + n * 4 + 1] = i1;
    regs[DSP_MPRO + n * 4 + 2] = i2; regs[DSP_MPRO + n * 4 + 3] = i3;
}

#define XSEL 0x8000u
#define YCOEF (1u << 13)
#define IRA(n) ((uint32_t)(n) << 7)
#define IWT(n) (0x40u | ((uint32_t)(n) << 1))
#define MWT 0x4000u
#define MRD 0x2000u
#define EWT(n) (0x1000u | ((uint32_t)(n) << 8))
#define ZERO 0x2u
#define MASA(n) ((uint32_t)(n) << 9)
#define UN 0x7FF8u                      /* coefficient 4095/4096 */

/* Voix 0 : PCM 16 bits en boucle sur deux échantillons égaux, attaque
 * instantanée. Envoi DSP : bus 3 au plein niveau ; directe : DISDL 15, centre. */
static void voix(uint8_t *ram, int vers_dsp) {
    ram[0x100] = 0x40; ram[0x101] = 0x1F; ram[0x102] = 0x40; ram[0x103] = 0x1F;   /* 8000, 8000 */
    for (int i = 0; i < 32; ++i) regs[i] = 0;
    regs[0] = 0xC200; regs[1] = 0x100; regs[3] = 2; regs[4] = 31; regs[5] = 31;
    if (vers_dsp) regs[8] = (15u << 4) | 3u;
    else regs[9] = 15u << 8;
}

static int16_t jouer(uint8_t *ram, int images, int image) {
    static int16_t out[2 * 400];
    aica_pcm_reset();
    aica_pcm_mix(regs, ram, out, 1);
    aica_pcm_key_execute(regs);
    aica_pcm_mix(regs, ram, out, (size_t)images);
    assert(out[image * 2] == out[image * 2 + 1]);   /* sorties au centre */
    return out[image * 2];
}

int main(void) {
    uint8_t *ram = calloc(0x200000, 1);
    assert(ram);

    /* Format de l'anneau. Un mot à 0 n'est pas zéro : exposant 0, bit caché
     * présent, il vaut +0,5 (comme dans Flycast et MAME) ; zéro s'écrit 0x6000. */
    assert(dsp_unpack(0) == 0x400000);
    assert(dsp_unpack(dsp_pack(0)) == 0 && dsp_pack(0) == 0x6000);
    for (int32_t v = -0x7FFFFF; v <= 0x7FFFFF; v += 4099) {
        int32_t r = dsp_unpack(dsp_pack(v));
        int32_t ecart = r > v ? r - v : v - r;
        int32_t a = v < 0 ? -v : v;
        assert(ecart <= (a >> 10) + 16);
    }

    /* Recopie : EFREG2 = MIXS3 x 1. */
    pas(0, 0, XSEL | YCOEF | IRA(0x23), ZERO, 0);
    pas(1, 0, 0, EWT(2) | ZERO, 0);
    regs[DSP_COEF + 0] = UN;
    regs[DSP_SORTIES + 2] = 15u << 8;            /* EFSDL 15, EFPAN 0 : centre */
    voix(ram, 0);
    int16_t direct = jouer(ram, 8, 6);
    voix(ram, 1);
    int16_t par_dsp = jouer(ram, 8, 6);
    printf("direct %d, par le DSP %d\n", direct, par_dsp);
    assert(direct > 4000);
    assert(abs(direct - par_dsp) <= 2);

    /* Écho : écrit en MADRS0, relu en MADRS1 = MADRS0 + 100, donc 100
     * échantillons plus tard ; puis EFREG0 = MEMS0 x 1. */
    memset(regs + DSP_MPRO, 0, 128 * 4 * 4);
    pas(0, 0, XSEL | YCOEF | IRA(0x23), ZERO, 0);   /* ACC = entrée */
    pas(1, 0, 0, MWT | ZERO, MASA(0));               /* anneau <- entrée */
    pas(3, 0, 0, MRD | ZERO, MASA(1));               /* lecture 100 plus tôt */
    pas(5, 0, IWT(0), ZERO, 0);                      /* MEMS0 <- lecture */
    pas(6, 0, XSEL | YCOEF | IRA(0), ZERO, 0);       /* ACC = MEMS0 */
    pas(7, 0, 0, EWT(0) | ZERO, 0);                  /* EFREG0 <- ACC */
    regs[DSP_COEF + 0] = UN; regs[DSP_COEF + 6] = UN;
    regs[DSP_MADRS + 0] = 0x10; regs[DSP_MADRS + 1] = 0x10 + 100;
    regs[DSP_SORTIES + 0] = 15u << 8;
    regs[DSP_SORTIES + 2] = 0;
    regs[DSP_ANNEAU] = 0x100;                        /* RBP = 0x100 x 2048 octets, 8192 mots */
    for (uint32_t a = 0x80000; a < 0x80000 + 8192 * 2; a += 2) { ram[a] = 0x00; ram[a + 1] = 0x60; }   /* anneau à zéro */
    voix(ram, 1);
    int16_t avant = jouer(ram, 60, 50);
    int16_t apres = jouer(ram, 160, 150);
    printf("echo : avant %d, apres %d\n", avant, apres);
    assert(avant == 0);
    assert(abs(apres - direct) <= 6);

    free(ram);
    puts("Essais du DSP reussis");
    return 0;
}
