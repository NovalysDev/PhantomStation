/* Self-contained tests for the experimental PowerVR texture decoder. */
#include "hal/pvr2_texture.h"
#include <assert.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>

static void put16(uint8_t *v, unsigned offset, uint16_t p) {
    v[offset]=(uint8_t)p; v[offset+1]=(uint8_t)(p>>8);
}
static void expect(const uint8_t *rgba, unsigned x, unsigned y,
                   unsigned r, unsigned g, unsigned b, unsigned a) {
    const uint8_t *p=rgba+(y*8+x)*4;
    assert(p[0]==r && p[1]==g && p[2]==b && p[3]==a);
}
int main(void) {
    uint8_t *v=calloc(8u*1024u*1024u,1), rgba[8*8*4]; int w=0,h=0; uint32_t palette[1024]={0};
    assert(v);
    /* Linear ARGB1555: red at (2,1), transparent blue at (0,0). */
    put16(v,0,0x001f); put16(v,(1*8+2)*2,0xfc00);
    assert(pvr2_decode_texture(v,0,1u<<26,rgba,sizeof rgba,&w,&h,palette,0));
    assert(w==8 && h==8); expect(rgba,2,1,255,0,0,255); expect(rgba,0,0,0,0,255,0);
    /* Twiddled RGB565: (x=2,y=1) maps to word index 9. */
    memset(v,0,128); put16(v,9*2,0x07e0);
    assert(pvr2_decode_texture(v,0,1u<<27,rgba,sizeof rgba,&w,&h,palette,0));
    expect(rgba,2,1,0,255,0,255);
    /* VQ ARGB4444: codebook index 3 for 2x2 block (1,0). */
    memset(v,0,4096); v[2048+2]=3;
    put16(v,3*8+0,0xff00); put16(v,3*8+2,0xf0f0);
    put16(v,3*8+4,0xf00f); put16(v,3*8+6,0xffff);
    assert(pvr2_decode_texture(v,0,(2u<<27)|(1u<<30),rgba,sizeof rgba,&w,&h,palette,0));
    expect(rgba,2,0,255,0,0,255); expect(rgba,2,1,0,255,0,255);
    expect(rgba,3,0,0,0,255,255); expect(rgba,3,1,255,255,255,255);
    /* PAL8 with palette bank 2, twiddled index 9 at pixel (2,1). */
    memset(v,0,4096); v[9]=7; palette[2*256+7]=0xff336699u;
    assert(pvr2_decode_texture(v,0,(6u<<27)|(32u<<21),rgba,sizeof rgba,&w,&h,palette,3));
    expect(rgba,2,1,0x33,0x66,0x99,0xff);
    /* Full-size mip level starts after the smaller levels and padding. */
    memset(v,0,4096); put16(v,24*2+9*2,0x07e0);
    assert(pvr2_decode_texture(v,0,(1u<<31)|(1u<<27),rgba,sizeof rgba,&w,&h,palette,0));
    expect(rgba,2,1,0,255,0,255);
    memset(v,0,4096); v[2048+6+2]=3; put16(v,3*8,0xff00);
    assert(pvr2_decode_texture(v,0,(1u<<31)|(1u<<30)|(2u<<27),rgba,sizeof rgba,&w,&h,palette,0));
    expect(rgba,2,0,255,0,0,255);
    assert(!pvr2_decode_texture(v,0,0,rgba,sizeof rgba-1,&w,&h,palette,0));
    free(v); puts("PowerVR texture tests passed"); return 0;
}
