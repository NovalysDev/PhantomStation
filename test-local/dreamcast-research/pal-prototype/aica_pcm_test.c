#include "hal/aica_pcm.h"
#include <assert.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
/* Essais du mélangeur des voix de la puce son. Attaque instantanée (AR = 31),
 * volume plein (TL = 0, DISDL = 15, centre) : un échantillon sort tel quel. */
int main(void) {
 uint32_t regs[64*32]={0}; uint8_t *ram=calloc(0x200000,1); int16_t out[64]={0}; assert(ram);
 /* PCM 16 bits, deux échantillons en boucle. */
 ram[0x100]=0xe8; ram[0x101]=0x03; ram[0x102]=0x18; ram[0x103]=0xfc;   /* 1000, -1000 */
 regs[0]=0xC200; regs[1]=0x100; regs[2]=0; regs[3]=2; regs[4]=31; regs[5]=31; regs[9]=15<<8;
 aica_pcm_reset(); aica_pcm_mix(regs,ram,out,1);   /* la mémoire son est connue avant le départ */
 aica_pcm_key_execute(regs); aica_pcm_mix(regs,ram,out,4);
 /* Le tout premier échantillon sort encore atténué : l'attaque part de 0x280. */
 assert(out[2]==-1000 && out[3]==-1000 && out[4]==1000 && out[6]==-1000);
 assert(aica_pcm_channel_active(0) && (regs[0] & 0x4000));
 /* Touche lâchée : le son s'éteint en quelques millisecondes (RR = 31, 3,6 ms),
  * au lieu d'être coupé net, puis la voie se libère. */
 { static int16_t long_out[2*200];
   regs[0]=0x8200; aica_pcm_key_execute(regs); aica_pcm_mix(regs,ram,long_out,8);
   assert(aica_pcm_channel_active(0));
   aica_pcm_mix(regs,ram,long_out,200);
   assert(!aica_pcm_channel_active(0)); }
 /* PCM 8 bits sans boucle : la voie s'éteint au bout et remet KYONB à 0,
  * pour qu'une note jouée ailleurs ne la relance pas. */
 aica_pcm_reset(); ram[0x200]=64; ram[0x201]=192; memset(regs,0,sizeof regs);
 regs[0]=0xC080; regs[1]=0x200; regs[3]=2; regs[4]=31; regs[5]=31; regs[9]=15<<8;
 aica_pcm_mix(regs,ram,out,1); aica_pcm_key_execute(regs); aica_pcm_mix(regs,ram,out,4);
 assert(out[2]==-16384 && out[4]==0);
 assert(!aica_pcm_channel_active(0) && !(regs[0] & 0x4000));
 regs[32]=0xC080; regs[33]=0x200; regs[35]=2; aica_pcm_key_execute(regs);   /* note sur la voie 1 */
 assert(!aica_pcm_channel_active(0) && aica_pcm_channel_active(1));
 /* Volume : TL = 16 divise par deux (6 dB). */
 aica_pcm_reset(); memset(regs,0,sizeof regs);
 regs[0]=0xC200; regs[1]=0x100; regs[3]=2; regs[4]=31; regs[5]=31; regs[9]=15<<8; regs[10]=16<<8;
 aica_pcm_mix(regs,ram,out,1); aica_pcm_key_execute(regs); aica_pcm_mix(regs,ram,out,2);
 assert(out[2]==-500 && out[3]==-500);
 /* État relu par le pilote (0x2810) : fin de boucle vue, puis effacée. */
 aica_pcm_mix(regs,ram,out,2);
 assert(aica_pcm_status(0) & 0x8000); assert(!(aica_pcm_status(0) & 0x8000));
 free(ram); puts("Essais du melangeur de son reussis"); return 0;
}
