/* Check that an intensity-reuse polygon keeps its previous face colour. */
#include "hal/pvr2.h"
#include <assert.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
void dc_hw_raise_istnrm(int bit) { (void)bit; }
static uint32_t bits(float f) { uint32_t u; memcpy(&u,&f,4); return u; }
static void triangle(void) {
    for(int i=0;i<3;i++) {
        uint32_t v[8]={0};v[0]=0xe0000000u|(i==2?0x10000000u:0);v[1]=bits((float)i);v[2]=bits(20.0f);v[3]=bits(1.0f);v[6]=bits(0.5f);pvr2_ta_write(v);
    }
}
int main(void) {
    pvr2_ta_init();
    uint32_t h[8]={0x80000020u,0,0,0,0,0,0,0};
    h[4]=bits(1);h[5]=bits(0.8f);h[6]=bits(0.4f);h[7]=bits(0.2f);pvr2_ta_write(h);triangle();
    uint32_t reuse[8]={0x80000030u,0,0,0,0,0,0,0};pvr2_ta_write(reuse);triangle();
    const PVR2TriList *list=pvr2_ta_get_list(PVR2_LIST_OPAQUE);
    assert(list&&list->count==6);
    for(int i=0;i<6;i++) {
        assert(fabsf(list->vertices[i].r-0.4f)<0.001f);
        assert(fabsf(list->vertices[i].g-0.2f)<0.001f);
        assert(fabsf(list->vertices[i].b-0.1f)<0.001f);
    }
    /* Type 6: 16-bit UV and four floats in the following packet. */
    uint32_t float_header[8]={0x80000019u,0,0,0,0,0,0,0};pvr2_ta_write(float_header);
    for(int i=0;i<3;i++) {
        uint32_t first[8]={0},second[8]={0};
        first[0]=0xe0000000u|(i==2?0x10000000u:0);first[1]=bits(2.0f);first[2]=bits(4.0f);first[3]=bits(1.0f);
        first[4]=(bits(0.5f)&0xffff0000u)|(bits(0.25f)>>16);
        second[0]=bits(1.0f);second[1]=bits(0.8f);second[2]=bits(0.4f);second[3]=bits(0.2f);
        pvr2_ta_write(first);pvr2_ta_write(second);
    }
    assert(list->count==9);
    assert(fabsf(list->vertices[6].u-0.5f)<0.001f && fabsf(list->vertices[6].v-0.25f)<0.001f);
    assert(fabsf(list->vertices[6].r-0.8f)<0.001f && fabsf(list->vertices[6].g-0.4f)<0.001f);
    pvr2_ta_destroy();puts("TA reused face colour: PASS");return 0;
}
