#ifndef DCRECOMP_PVR2_VIEWPORT_H
#define DCRECOMP_PVR2_VIEWPORT_H
/* Letterbox a 4:3 Dreamcast picture inside a drawable; returns false for zero sizes. */
int pvr2_viewport(int width,int height,int *x,int *y,int *view_width,int *view_height);
#endif
