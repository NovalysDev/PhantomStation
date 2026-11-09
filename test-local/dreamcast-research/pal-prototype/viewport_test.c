#include "hal/pvr2_viewport.h"
#include <assert.h>
#include <stdio.h>
int main(void) {
 int x,y,w,h;
 assert(pvr2_viewport(1920,1080,&x,&y,&w,&h) && x==240 && y==0 && w==1440 && h==1080);
 assert(pvr2_viewport(1280,1024,&x,&y,&w,&h) && x==0 && y==32 && w==1280 && h==960);
 assert(pvr2_viewport(1280,960,&x,&y,&w,&h) && x==0 && y==0 && w==1280 && h==960);
 assert(!pvr2_viewport(0,960,&x,&y,&w,&h)); puts("Viewport tests passed");return 0;
}
