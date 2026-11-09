/* Les 64 voix de la puce son AICA de la Dreamcast (sans le processeur
 * d'effets DSP ni le filtre). Comportement des voies vérifié face à Flycast
 * (GPL-2.0) : https://github.com/flyinghead/flycast/blob/master/core/hw/aica/sgc_if.cpp
 *
 * Chaque voie joue un échantillon (PCM 16 ou 8 bits, ADPCM Yamaha, ou bruit)
 * et le façonne comme la vraie puce :
 *  - enveloppe de volume (attaque, déclin 1, déclin 2, relâchement) : une note
 *    qu'on lâche s'éteint selon sa vitesse de relâchement au lieu d'être coupée ;
 *  - LFO : vibrato (hauteur) et trémolo (volume) ;
 *  - volume total (TL), niveau direct (DISDL) et placement gauche-droite
 *    (DIPAN) par pas de décibels, volume général (MVOL) ;
 *  - une voie qui finit (échantillon sans boucle arrivé au bout, enveloppe
 *    éteinte) remet à zéro son bit « touche enfoncée » (KYONB), comme la puce.
 *    Sans ça, chaque nouvelle note de n'importe quelle voie relançait tous les
 *    bruitages déjà finis : le son « bugé » de Grandia II.
 *  - l'état relu par le pilote son (registre 0x2810 : fin de boucle, étape et
 *    niveau de l'enveloppe ; 0x2814 : position). */
#include <stdio.h>
#include "hal/aica_pcm.h"
#include <math.h>
#include <stdlib.h>
#include <string.h>

#define CHANNELS 64
#define RAM_MASK 0x1fffffu
#define EG_BITS 16
#define KYONB 0x4000u

enum { ATTAQUE, DECLIN1, DECLIN2, RELACHE };

typedef struct {
    int active;
    uint32_t ca, frac;            /* position : échantillon et fraction sur 10 bits */
    int s0, s1;                   /* échantillon courant et suivant (interpolation) */
    int quant, loop_quant, loop_s0, in_loop;   /* décodeur ADPCM */
    int looped;                   /* fin de boucle atteinte (bit LP de 0x2810) */
    unsigned joues;               /* echantillons joues depuis le key-on */
    int32_t eg;                   /* atténuation de l'enveloppe : 10 bits << 16 */
    int etat;
    uint32_t lfo_compte; uint8_t lfo_pas;
    uint32_t bruit;
    uint32_t lea;                 /* dernière fin de boucle vue (pour 0x2814) */
} Voie;

static Voie voies[CHANNELS];
static unsigned g_mvol = 15, g_mono;

/* Atténuation en 1/16 de doublement (0,375 dB) -> gain x.15 ; >= 256 muet. */
static int32_t tl_lut[1024];
static int32_t volume_lut[16];
static uint32_t att_sps[64], dsr_sps[64];
static int plfo_tab[8][256];
static int tables_pretes;

/* DISDL, IMXL et le côté atténué de DIPAN : 3 dB par pas, 0 = muet. */
static const uint32_t send_level[16] = {
    255, 14 << 3, 13 << 3, 12 << 3, 11 << 3, 10 << 3, 9 << 3, 8 << 3,
    7 << 3, 6 << 3, 5 << 3, 4 << 3, 3 << 3, 2 << 3, 1 << 3, 0
};
/* Durée (ms) d'une attaque complète, et d'un déclin complet, pour chaque vitesse. */
static const double attack_ms[64] = {
    -1,-1,8100.0,6900.0,6000.0,4800.0,4000.0,3400.0,3000.0,2400.0,2000.0,1700.0,1500.0,
    1200.0,1000.0,860.0,760.0,600.0,500.0,430.0,380.0,300.0,250.0,220.0,190.0,150.0,130.0,110.0,95.0,
    76.0,63.0,55.0,47.0,38.0,31.0,27.0,24.0,19.0,15.0,13.0,12.0,9.4,7.9,6.8,6.0,4.7,3.8,3.4,3.0,2.4,
    2.0,1.8,1.6,1.3,1.1,0.93,0.85,0.65,0.53,0.44,0.40,0.35,0.0,0.0
};
static const double dsr_ms[64] = {
    -1,-1,118200.0,101300.0,88600.0,70900.0,59100.0,50700.0,44300.0,35500.0,29600.0,25300.0,22200.0,17700.0,
    14800.0,12700.0,11100.0,8900.0,7400.0,6300.0,5500.0,4400.0,3700.0,3200.0,2800.0,2200.0,1800.0,1600.0,1400.0,1100.0,
    920.0,790.0,690.0,550.0,460.0,390.0,340.0,270.0,230.0,200.0,170.0,140.0,110.0,98.0,85.0,68.0,57.0,49.0,43.0,34.0,
    28.0,25.0,22.0,18.0,14.0,12.0,11.0,8.5,7.1,6.1,5.4,4.3,3.6,3.1
};
/* Profondeur du vibrato, en centièmes de demi-ton, pour PLFOS 0..7. */
static const float plfos_cents[8] = { 0.f, 3.61f, 7.22f, 14.44f, 28.88f, 57.75f, 115.5f, 231.f };

static void tables(void) {
    if (tables_pretes) return;
    for (int i = 0; i < 1024; ++i) tl_lut[i] = i < 256 ? (int32_t)(32768.0 / pow(2.0, i / 16.0)) : 0;
    volume_lut[0] = 0;
    for (int i = 1; i < 16; ++i) volume_lut[i] = (int32_t)(32768.0 / pow(2.0, (15 - i) / 2.0));
    for (int i = 0; i < 64; ++i) {
        double t = attack_ms[i];
        att_sps[i] = t < 0 ? 0 : t == 0 ? 1u << 16
                   : (uint32_t)lround((1.0 / (1.0 - 1.0 / pow(0x280, 1.0 / (44.1 * t)))) * (1 << 16));
        double tous = 1024.0 * (1 << EG_BITS) - 1;
        t = dsr_ms[i];
        dsr_sps[i] = t < 0 ? 0 : t == 0 ? (uint32_t)tous : (uint32_t)lround(tous / (44.1 * t));
    }
    for (int s = 0; s < 8; ++s)
        for (int i = -128; i < 128; ++i)
            plfo_tab[s][i + 128] = (int)(1024.f * powf(2.f, plfos_cents[s] * i / 128.f / 1200.f));
    tables_pretes = 1;
}

void aica_pcm_reset(void) {
    tables();
    memset(voies, 0, sizeof voies);
    for (int ch = 0; ch < CHANNELS; ++ch) { voies[ch].etat = RELACHE; voies[ch].eg = 0x3FF << EG_BITS; }
    g_mvol = 15; g_mono = 0;
}

void aica_pcm_set_master(unsigned mvol, unsigned mono) { g_mvol = mvol & 15; g_mono = mono != 0; }

int aica_pcm_channel_active(unsigned ch) { return ch < CHANNELS && voies[ch].active; }

uint32_t aica_pcm_position(unsigned ch) {
    if (ch >= CHANNELS) return 0;
    uint32_t ca = voies[ch].ca, lea = voies[ch].lea;
    /* Comme la vraie puce, la position lue ne colle jamais tout à fait à la fin. */
    if (lea > 3 && ca > lea - 3) ca = lea - 3;
    return ca & 0xffffu;
}

uint32_t aica_pcm_status_ex(unsigned ch, int clear) {
    if (ch >= CHANNELS) return 0x1FFF | (RELACHE << 13);
    Voie *c = &voies[ch];
    int32_t aeg = c->eg >> EG_BITS;
    uint32_t v = (c->looped ? 0x8000u : 0) | ((uint32_t)c->etat << 13) | (aeg > 0x3BF ? 0x1FFFu : (uint32_t)aeg);
    if (clear) c->looped = 0;   /* LP s'efface sur le mot ou l'octet haut, pas l'octet bas */
    return v;
}

uint32_t aica_pcm_status(unsigned ch) {
    return aica_pcm_status_ex(ch, 1);
}

static int adpcm(unsigned nib, int prev, int *quant) {
    static const int qs[8] = { 0x0e6, 0x0e6, 0x0e6, 0x0e6, 0x133, 0x199, 0x200, 0x266 };
    int d = (*quant * (int)(2 * (nib & 7u) + 1)) >> 3;
    if (d > 0x7FFF) d = 0x7FFF;
    int v = prev + ((nib & 8u) ? -d : d);
    int q = (*quant * qs[nib & 7u]) >> 8;
    *quant = q < 127 ? 127 : q > 24576 ? 24576 : q;
    return v < -32768 ? -32768 : v > 32767 ? 32767 : v;
}

typedef struct { unsigned fmt, lpctl, lpslnk; uint32_t sa, lsa, lea; } Flux;

static void flux(const uint32_t *v, Flux *f) {
    f->fmt = (v[0] & 0x400u) ? 4 : (v[0] >> 7) & 3u;     /* SSCTL = bruit */
    f->lpctl = (v[0] >> 9) & 1u;
    f->lpslnk = (v[5] >> 14) & 1u;
    f->sa = ((v[0] & 0x7fu) << 16) | (v[1] & 0xffffu);
    if (f->fmt == 0) f->sa &= ~1u;
    f->lsa = v[2] & 0xffffu;
    f->lea = v[3] & 0xffffu;
    if (f->fmt == 3) f->lea = (f->lea + 3u) & ~3u;        /* ADPCM long : fin alignée sur 4 */
}

static int nibble(const uint8_t *ram, uint32_t sa, uint32_t i) {
    return (ram[(sa + (i >> 1)) & RAM_MASK] >> ((i & 1u) * 4u)) & 15;
}

static int pcm(const uint8_t *ram, Voie *c, const Flux *f, uint32_t i) {
    if (f->fmt == 0) {
        uint32_t at = (f->sa + i * 2u) & RAM_MASK;
        return (int16_t)(ram[at] | ((uint16_t)ram[(at + 1) & RAM_MASK] << 8));
    }
    if (f->fmt == 1) return (int8_t)ram[(f->sa + i) & RAM_MASK] * 256;
    c->bruit = c->bruit * 0x41c64e6du + 0x3039u;
    return (int16_t)(c->bruit >> 16);
}

/* Décode l'échantillon à la position ca (et, si dernier, le suivant). */
static void decode(const uint8_t *ram, Voie *c, const Flux *f, int dernier) {
    uint32_t suivant = c->ca + 1;
    /* Hors boucle, la puce joue jusqu'a LEA et s'arrete. Revenir a LSA pour
     * l'echantillon suivant faisait sauter la fin des effets courts au debut
     * du son : un claquement net a chaque coffre ouvert. On tient la
     * derniere valeur, seule la boucle demandee ramene a LSA. */
    /* Comme la puce : on ne revient a LSA que si LEA est bien apres LSA.
     * Quand LSA > LEA, l'echantillon continue au-dela de LEA. */
    if (suivant >= f->lea && f->lea > f->lsa) suivant = f->lsa;
    if (f->fmt < 2 || f->fmt == 4) {
        if (!dernier) return;
        c->s0 = pcm(ram, c, f, c->ca);
        c->s1 = pcm(ram, c, f, suivant);
        return;
    }
    int q = c->quant;
    if (c->ca == f->lsa) {
        if (!c->in_loop) { c->in_loop = 1; c->loop_quant = q; c->loop_s0 = c->s0; }
        else if (f->fmt == 2) { q = c->loop_quant; c->s0 = c->loop_s0; }
    }
    c->s0 = adpcm((unsigned)nibble(ram, f->sa, c->ca), c->s0, &q);
    c->quant = q;
    if (dernier) {
        int prev = c->s0;
        if (f->fmt == 2 && suivant == f->lsa && c->in_loop) { q = c->loop_quant; prev = c->loop_s0; }
        c->s1 = adpcm((unsigned)nibble(ram, f->sa, suivant), prev, &q);
    } else c->s1 = 0;
}

static void eteindre(Voie *c, uint32_t *v) {
    c->active = 0; c->etat = RELACHE; c->eg = 0x3FF << EG_BITS; c->ca = 0;
    v[0] &= ~KYONB;
}

static uint32_t lfo_periode(const uint32_t *v) {
    int n = (v[7] >> 10) & 31, s = n >> 2, m = (~n) & 3, g = 128 >> s;
    return (uint32_t)(((g - 1) << 2) + g * (m + 1));
}

static void lfo_reset(Voie *c, const uint32_t *v) { c->lfo_pas = 0; c->lfo_compte = lfo_periode(v); }

static unsigned onde(unsigned forme, uint8_t pas) {
    switch (forme & 3u) {
    case 0: return pas;
    case 1: return (pas & 0x80) ? 255u : 0u;
    case 2: return (((unsigned)pas & 0x7fu) ^ ((pas & 0x80) ? 0x7fu : 0u)) << 1;
    default: return (pas * 0x41c64e6du + 0x3039u) & 0xffu;
    }
}

static unsigned vitesse_effective(unsigned vitesse, const uint32_t *v) {
    unsigned r = vitesse * 2, krs = (v[5] >> 10) & 15u;
    if (krs < 15) {
        int oct = (int)(((v[6] >> 11) & 15u) ^ 8u) - 8;
        r += (v[6] >> 9) & 1u;
        int k = ((int)krs + oct) * 2;
        if (k > 0) r += (unsigned)k;
    }
    return r > 0x3f ? 0x3f : r;
}

static void key_on(Voie *c, const uint32_t *v, const uint8_t *ram) {
    memset(c, 0, sizeof *c);
    c->active = 1; c->etat = ATTAQUE; c->eg = 0x280 << EG_BITS;
    c->quant = 127;
    lfo_reset(c, v);
    if (ram) { Flux f; flux(v, &f); c->lea = v[3] & 0xffffu; decode(ram, c, &f, 1); }
}

static const uint8_t *g_ram;   /* dernière mémoire son vue, pour décoder au démarrage */

static unsigned g_courts, g_kyons, g_fins;

/* Same-build mixer section only. This omits sound registers, sound RAM, ARM7,
 * queued host audio, and all other devices. It cannot resume a game alone. */
typedef struct {
    uint32_t magic, version, voice_size, mvol, mono, checksum;
    Voie channels[CHANNELS];
} PcmCheckpoint;

static uint32_t pcm_checkpoint_hash(const uint8_t *data, size_t size) {
    uint32_t hash = 2166136261u;
    while (size--) { hash ^= *data++; hash *= 16777619u; }
    return hash;
}

size_t aica_pcm_checkpoint_size(void) { return sizeof(PcmCheckpoint); }

int aica_pcm_checkpoint_capture(void *buffer, size_t size) {
    if (!buffer || size != sizeof(PcmCheckpoint)) return 0;
    PcmCheckpoint image;
    memset(&image, 0, sizeof image);
    image.magic=0x504d4341u; image.version=1; image.voice_size=sizeof(Voie);
    image.mvol=g_mvol; image.mono=g_mono;
    for (int i=0; i<CHANNELS; ++i) {
        const Voie *s=&voies[i]; Voie *d=&image.channels[i];
        d->active=s->active; d->ca=s->ca; d->frac=s->frac;
        d->s0=s->s0; d->s1=s->s1; d->quant=s->quant;
        d->loop_quant=s->loop_quant; d->loop_s0=s->loop_s0; d->in_loop=s->in_loop;
        d->looped=s->looped; d->joues=s->joues; d->eg=s->eg; d->etat=s->etat;
        d->lfo_compte=s->lfo_compte; d->lfo_pas=s->lfo_pas;
        d->bruit=s->bruit; d->lea=s->lea;
    }
    image.checksum=pcm_checkpoint_hash((const uint8_t *)&image, sizeof image);
    memcpy(buffer, &image, sizeof image);
    return 1;
}

int aica_pcm_checkpoint_restore(const void *buffer, size_t size, const uint8_t *sound_ram) {
    if (!buffer || !sound_ram || size != sizeof(PcmCheckpoint)) return 0;
    PcmCheckpoint image;
    memcpy(&image, buffer, sizeof image);
    uint32_t expected=image.checksum;
    image.checksum=0;
    if (image.magic != 0x504d4341u || image.version != 1 || image.voice_size != sizeof(Voie) ||
        image.mvol > 15 || image.mono > 1 ||
        expected != pcm_checkpoint_hash((const uint8_t *)&image, sizeof image)) return 0;
    for (int i=0; i<CHANNELS; ++i) {
        const Voie *c=&image.channels[i];
        if ((c->active != 0 && c->active != 1) || c->etat < ATTAQUE || c->etat > RELACHE ||
            (c->in_loop != 0 && c->in_loop != 1) || (c->looped != 0 && c->looped != 1)) return 0;
    }
    tables();
    memcpy(voies, image.channels, sizeof voies);
    g_mvol=image.mvol; g_mono=image.mono; g_ram=sound_ram;
    return 1;
}

void aica_pcm_key_execute(uint32_t *r) {
    tables();
    for (int ch = 0; ch < CHANNELS; ++ch) {
        Voie *c = &voies[ch];
        int on = (r[ch * 32] & KYONB) != 0;
        if (on && (!c->active || c->etat == RELACHE)) {
            if (g_kyons < 200) {
                Flux f; flux(r + ch * 32, &f);
                printf("[KYON] ch=%d sa=%06X lsa=%04X lea=%04X fmt=%u lp=%u oct=%u disdl=%u imxl=%u tl=%u",
                       ch, f.sa, f.lsa, f.lea, f.fmt, f.lpctl,
                       (r[ch*32+6] >> 11) & 15u, (r[ch*32+9] >> 8) & 15u,
                       (r[ch*32+8] >> 4) & 15u, (r[ch*32+10] >> 8) & 255u);
                putchar(10);
                g_kyons++;
            }
            key_on(c, r + ch * 32, g_ram);
        }
        else if (!on && c->active && c->etat != RELACHE) c->etat = RELACHE;
    }
}

void aica_pcm_reg_write(const uint32_t *r, uint32_t off) {
    /* LFORE écrit à 1 : le LFO de la voie repart de zéro. */
    if (off < 0x2000 && (off & 0x7f) == 0x1c && (r[off / 4] & 0x8000u)) {
        unsigned ch = off >> 7;
        lfo_reset(&voies[ch], r + ch * 32);
    }
}

static void enveloppe(Voie *c, uint32_t *v) {
    switch (c->etat) {
    case ATTAQUE: {
        uint32_t rate = att_sps[vitesse_effective(v[4] & 31u, v)];
        if (rate) {
            c->eg -= (int32_t)((((uint64_t)(uint32_t)c->eg) << 16) / rate) + 1;
            if (c->eg <= 0) { c->eg = 0; if (!((v[5] >> 14) & 1u)) c->etat = DECLIN1; }
        }
        break; }
    case DECLIN1:
        c->eg += (int32_t)dsr_sps[vitesse_effective((v[4] >> 6) & 31u, v)];
        if ((uint32_t)(c->eg >> EG_BITS) >= (((v[5] >> 5) & 31u) << 5)) c->etat = DECLIN2;
        break;
    case DECLIN2:
        c->eg += (int32_t)dsr_sps[vitesse_effective((v[4] >> 11) & 31u, v)];
        if ((c->eg >> EG_BITS) >= 0x3FF) { c->eg = 0x3FF << EG_BITS; c->etat = RELACHE; v[0] &= ~KYONB; }
        break;
    default:
        c->eg += (int32_t)dsr_sps[vitesse_effective(v[5] & 31u, v)];
        if ((c->eg >> EG_BITS) >= 0x3FF) eteindre(c, v);
        break;
    }
}

/* Le mixage additionne des voix qui peuvent chacune approcher la pleine
 * echelle : les pics de voix par-dessus la musique depassaient 32767 et la
 * coupure seche s'entendait comme une saturation. On garde une petite marge
 * avant le plafond, puis un coude doux arrondit les pics au lieu de les
 * tronquer. */
#define MIX_MARGE  23170   /* 0,707 : -3 dB de marge, la place des voix */
#define MIX_SEUIL  24576   /* -2,5 dBFS : debut du coude */

static int16_t limite(int64_t v) {
    v = (v * (int64_t)MIX_MARGE) >> 15;
    if (v > MIX_SEUIL) {
        int64_t marge = 32767 - MIX_SEUIL, d = v - MIX_SEUIL;
        v = MIX_SEUIL + (d * marge) / (d + marge);
    } else if (v < -MIX_SEUIL) {
        int64_t marge = 32768 - MIX_SEUIL, d = -MIX_SEUIL - v;
        v = -MIX_SEUIL - (d * marge) / (d + marge);
    }
    return (int16_t)v;
}

void aica_pcm_mix(uint32_t *r, const uint8_t *ram, int16_t *out, size_t frames) {
    if (!r || !ram || !out) return;
    tables();
    g_ram = ram;
    for (size_t i = 0; i < frames; ++i) {
        int64_t left = 0, right = 0;
        for (int ch = 0; ch < CHANNELS; ++ch) {
            Voie *c = &voies[ch]; if (!c->active) continue;
            uint32_t *v = r + ch * 32;
            Flux f; flux(v, &f);
            c->lea = v[3] & 0xffffu;

            /* Compatibilité Grandia II : l'alarme est un bourdon de 262 Hz
             * forme de plusieurs voix PCM16 qui rejouent la meme onde carree :
             * six fois 0x3FAC (LEA 0x54, octave 15) et une septieme, 0x4070
             * (LEA 0xA9, octave 0), qui donne la meme note a vitesse normale.
             * Sur la puce, ce bruit est noye dans le mixage DSP ; notre
             * mixage direct le rend comme un bourdonnement sature. On coupe
             * seule la famille tenue (0x3FAC) : le bip de selection (0x4070) reste audible. */
            int mute_alarm_voice;
            { static int mute_alarm = -1;
              if (mute_alarm < 0) {
                  const char *e = getenv("PHANTOM_DC_MUTE_ALARM");
                  mute_alarm = e ? atoi(e) : 1;   /* bourdon 0x3FAC coupe par defaut */
              }
              unsigned oct_alarme = (v[6] >> 11) & 15u;
              mute_alarm_voice = mute_alarm && f.lsa == 0 &&
                  f.sa == 0x3FAC && f.lea == 0x54 && oct_alarme == 15;
            }

            /* LFO : valeurs du trémolo (alfo) et du vibrato (plfo) à ce pas. */
            unsigned alfos = v[7] & 7u, plfos = (v[7] >> 5) & 7u;
            unsigned alfo = alfos ? onde((v[7] >> 3) & 3u, c->lfo_pas) >> (8 - alfos) : 0;
            int plfo = plfos ? plfo_tab[plfos][onde((v[7] >> 8) & 3u, c->lfo_pas) & 0xffu] : 1024;

            /* Sortie : échantillon interpolé, puis toutes les atténuations
             * additionnées (enveloppe, trémolo, volume, niveau, côté). */
            int sample = c->s0 + (int)(((int64_t)(c->s1 - c->s0) * (int64_t)c->frac) >> 10);
            unsigned voff = (v[10] >> 6) & 1u;
            unsigned ofsatt = voff ? 0 : alfo + (unsigned)((c->eg >> EG_BITS) >> 2);
            if (ofsatt > 255) ofsatt = 255;
            unsigned tl = voff ? 0 : (v[10] >> 8) & 255u;
            unsigned disdl = (v[9] >> 8) & 15u, dipan = v[9] & 31u;
            unsigned max_att = 255 - ofsatt;
            if (mute_alarm_voice) {
                /* Silence the output only: envelopes, playback position and
                 * key-off must keep running so the driver can reuse the voice. */
            } else if (disdl) {
                unsigned full = tl + send_level[disdl], pan = full + send_level[(~dipan) & 15u];
                unsigned dl = (dipan & 0x10) ? full : pan, dr = (dipan & 0x10) ? pan : full;
                if (dl > max_att) dl = max_att;
                if (dr > max_att) dr = max_att;
                left += ((int64_t)sample * tl_lut[ofsatt + dl]) >> 15;
                right += ((int64_t)sample * tl_lut[ofsatt + dr]) >> 15;
            } else {
                /* Pas de sortie directe : le son part seulement vers le DSP
                 * (effets), que nous n'avons pas. On le rend au centre, à son
                 * niveau d'envoi, plutôt que de le perdre. */
                unsigned ds = tl + send_level[(v[8] >> 4) & 15u];
                if (ds > max_att) ds = max_att;
                int64_t s = ((int64_t)sample * tl_lut[ofsatt + ds]) >> 15;
                left += s; right += s;
            }

            enveloppe(c, v);
            if (!c->active) {
                if (c->joues < 64 && g_courts < 120) {
                    g_courts++;
                    printf("[KYON-COURT] ch=%d joues=%u sa=%06X lsa=%04X lea=%04X fmt=%u lp=%u oct=%u disdl=%u imxl=%u",
                           ch, c->joues, f.sa, f.lsa, f.lea, f.fmt, f.lpctl,
                           (v[6] >> 11) & 15u, (v[9] >> 8) & 15u, (v[8] >> 4) & 15u);
                    putchar(10);
                }
                continue;
            }

            /* Avance dans l'échantillon, au pas de la note (et du vibrato). */
            uint32_t rate = 1024u | (v[6] & 1023u), oct = (v[6] >> 11) & 15u;
            rate = (oct & 8) ? rate >> (16 - oct) : rate << oct;
            uint32_t pas = c->frac + (uint32_t)(((uint64_t)rate * (uint32_t)plfo) >> 10);
            uint32_t n = pas >> 10; c->frac = pas & 1023u;
            while (n--) {
                uint32_t ca = c->ca + 1;
                if (f.lpslnk && c->etat == ATTAQUE && ca >= f.lsa) c->etat = DECLIN1;
                if (ca >= f.lea) {
                    if (f.lsa > f.lea) {
                        /* LSA > LEA : la puce ne s'arrete pas a LEA, elle
                         * poursuit jusqu'a LSA puis libere la voie, meme si
                         * LPCTL est arme (Flycast, sgc_if.cpp). Couper a LEA
                         * rendait muets les bruitages courts dont le pilote
                         * son laisse LEA a zero. */
                        if (ca >= f.lsa) {
                            c->looped = 1;
                            if (g_fins < 40) {
                                g_fins++;
                                printf("[KYON-FIN] ch=%d sam=%u lsa=%04X lea=%04X lp=%u",
                                       ch, ca, f.lsa, f.lea, f.lpctl);
                                putchar(10);
                            }
                            eteindre(c, v);
                            break;
                        }
                    } else if (f.lpctl) {
                        c->looped = 1;
                        ca = f.lsa;
                    } else {
                        /* Fin de l'echantillon : la puce arrete et libere la
                         * voie (sans quoi le pilote son attend une fin qui
                         * n'arrive jamais et le jeu se fige). */
                        c->looped = 1;
                        eteindre(c, v);
                        break;
                    }
                }
                c->ca = ca;
                c->joues++;
                decode(ram, c, &f, n == 0);
            }
            if (!c->active) continue;

            /* LFO : un pas tous les « période » échantillons. */
            if (--c->lfo_compte == 0) { c->lfo_pas++; c->lfo_compte = lfo_periode(v); }
        }
        if (g_mono) left = right = (left + right) / 2;
        left = (left * volume_lut[g_mvol]) >> 15;
        right = (right * volume_lut[g_mvol]) >> 15;
        out[i * 2] = limite(left);
        out[i * 2 + 1] = limite(right);
    }
}
