#include "hal/aica_pcm.h"
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int main(void) {
    uint32_t regs[64*32]={0}, saved_regs[64*32];
    uint8_t *ram=calloc(0x200000,1);
    int16_t lead[14], expected[256], actual[256];
    assert(ram);
    for (int i=0;i<64;++i) ram[0x100+i]=(uint8_t)(i*3+10);
    regs[0]=0xC280; regs[1]=0x100; regs[3]=64;
    regs[4]=31; regs[5]=31; regs[9]=15<<8;
    aica_pcm_reset();
    aica_pcm_mix(regs,ram,lead,1);
    aica_pcm_key_execute(regs);
    aica_pcm_mix(regs,ram,lead,7);
    size_t size=aica_pcm_checkpoint_size();
    uint8_t *snapshot=malloc(size), *before=malloc(size), *after=malloc(size);
    assert(snapshot && before && after);
    assert(!aica_pcm_checkpoint_capture(snapshot,size-1));
    assert(aica_pcm_checkpoint_capture(snapshot,size));
    memcpy(saved_regs,regs,sizeof regs);
    aica_pcm_mix(regs,ram,expected,128);
    aica_pcm_set_master(3,1);
    assert(aica_pcm_checkpoint_capture(before,size));
    snapshot[size-1] ^= 1;
    assert(!aica_pcm_checkpoint_restore(snapshot,size,ram));
    assert(aica_pcm_checkpoint_capture(after,size));
    assert(!memcmp(before,after,size));
    snapshot[size-1] ^= 1;
    assert(!aica_pcm_checkpoint_restore(snapshot,size-1,ram));
    assert(!aica_pcm_checkpoint_restore(snapshot,size,NULL));
    memcpy(regs,saved_regs,sizeof regs);
    assert(aica_pcm_checkpoint_restore(snapshot,size,ram));
    aica_pcm_mix(regs,ram,actual,128);
    assert(!memcmp(expected,actual,sizeof expected));
    free(snapshot); free(before); free(after); free(ram);
    puts("Audio checkpoint reproduces samples and rejects corruption");
    return 0;
}
