#include "hal/dc_vmu.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>
int main(int argc,char **argv) {
    if(argc!=2) return 2;
    unsigned char b[512], payload[128];
    memset(payload,0x5a,sizeof payload);
    if(!dc_vmu_open(argv[1])) {fprintf(stderr,"VMU open failed\n"); return 1;}
    assert(dc_vmu_read(255,b));
    for(int i=0;i<16;i++) assert(b[i]==0x55);
    assert(b[0x40]==255 && b[0x46]==254 && b[0x4a]==253 && b[0x4c]==13);
    assert(dc_vmu_read(254,b)); assert(b[0]==0xfc && b[1]==0xff);
    assert(dc_vmu_write(7,2,payload,sizeof payload));
    assert(!dc_vmu_write(256,0,payload,sizeof payload));
    assert(!dc_vmu_write(7,4,payload,sizeof payload));
    assert(!dc_vmu_write(7,0,payload,129));
    dc_vmu_close();
    assert(dc_vmu_open(argv[1])); assert(dc_vmu_read(7,b));
    for(int i=0;i<128;i++) assert(b[256+i]==0x5a);
    dc_vmu_close(); puts("VMU storage tests passed"); return 0;
}
