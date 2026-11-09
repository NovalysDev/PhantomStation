#include "hal/pvr2_viewport.h"
#include <stdint.h>
int pvr2_viewport(int width,int height,int *x,int *y,int *vw,int *vh) {
    if(width<=0 || height<=0 || !x || !y || !vw || !vh) return 0;
    int w=width,h=height;
    if ((int64_t)width*3 > (int64_t)height*4) w=(int)((int64_t)height*4/3);
    else h=(int)((int64_t)width*3/4);
    if (w<=0 || h<=0) return 0;
    *x=(width-w)/2;*y=(height-h)/2;*vw=w;*vh=h;return 1;
}
