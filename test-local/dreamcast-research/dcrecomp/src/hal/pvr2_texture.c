/* Experimental PowerVR2 texture decode; unsupported encodings return false. */
#include <stdint.h>
#include <stddef.h>
#include <stdlib.h>
#include <string.h>
#include "hal/pvr2_texture.h"

#define VRAM_MASK ((8u * 1024u * 1024u) - 1u)

/* Rectangular Morton layout: interleave the available Y and X bits. */
static uint32_t morton(unsigned x, unsigned y, unsigned width, unsigned height) {
    uint32_t out = 0; unsigned bit = 0;
    for (width >>= 1, height >>= 1; width || height; width >>= 1, height >>= 1) {
        if (height) { out |= (y & 1u) << bit++; y >>= 1; }
        if (width)  { out |= (x & 1u) << bit++; x >>= 1; }
    }
    return out;
}
static uint16_t read16(const uint8_t *vram, uint32_t at, uint32_t mask) {
    return (uint16_t)(vram[at & mask] | ((uint16_t)vram[(at + 1) & mask] << 8));
}
static unsigned g_stride;
void pvr2_texture_set_stride(unsigned pixels) { g_stride = pixels; }
static uint8_t clamp255(int v) { return (uint8_t)(v < 0 ? 0 : v > 255 ? 255 : v); }

/* Les mêmes calculs d'adresse que pvr2_decode_texture : de l'adresse de base
 * (livre de codes d'une texture compressée, niveaux réduits avant le niveau
 * principal) jusqu'à la fin du niveau principal. */
uint64_t pvr2_texture_fingerprint(const uint8_t *vram, uint32_t tsp, uint32_t tcw,
                                  const uint32_t *palette, unsigned palette_format) {
    static int reserve = -1;
    if (reserve < 0) reserve = getenv("PHANTOM_DC_TEXTURE_RESERVE") != NULL;
    uint32_t mask = reserve ? 2u*(VRAM_MASK+1u)-1u : VRAM_MASK;
    unsigned w = 8u << ((tsp >> 3) & 7u), h = 8u << (tsp & 7u);
    unsigned format = (tcw >> 27) & 7u, linear = (tcw >> 26) & 1u;
    unsigned stride = (tcw >> 25) & 1u, vq = (tcw >> 30) & 1u;
    unsigned mipmap = tcw >> 31;
    if (mipmap && (!linear || vq)) { h = w; if (vq) linear = 0; }
    else mipmap = 0;
    int paletted = format == 5 || format == 6;
    unsigned pitch = w;
    if (stride && !paletted && linear && g_stride) pitch = g_stride;
    uint32_t base = (tcw & 0x1fffffu) * 8u, data_base = base;
    if (mipmap) {
        unsigned level = ((tsp >> 3) & 7u) + 3u;
        if (vq) {
            uint32_t preceding = 1, area = 1;
            for (unsigned i=1; i<level; ++i) { preceding += area; area *= 4; }
            data_base += 2048u + preceding;
        } else {
            uint32_t preceding = 3, area = 1;
            for (unsigned i=0; i<level; ++i) { preceding += area; area *= 4; }
            data_base += format == 5 ? preceding / 2u : format == 6 ? preceding : preceding * 2u;
        }
    } else if (vq) data_base += 2048u;
    uint32_t size = vq ? (w/2u)*(h/2u) : format == 5 ? w*h/2u : format == 6 ? w*h
                  : (h - 1u)*pitch*2u + w*2u;
    uint32_t end = data_base + size;
    uint64_t f = 1469598103934665603ull ^ ((uint64_t)tsp << 32 | tcw) ^ ((uint64_t)pitch << 20);
    if (end - base <= mask + 1u && (base & mask) + (end - base) <= mask + 1u && !((base | end) & 7u)) {
        const uint8_t *p = vram + (base & mask);
        for (uint32_t i = 0; i < end - base; i += 8u) {
            uint64_t v; memcpy(&v, p + i, 8); f = (f ^ v) * 1099511628211ull;
        }
    } else {
        for (uint32_t a = base; a != end; a++) f = (f ^ vram[a & mask]) * 1099511628211ull;
    }
    if (paletted && palette) {
        unsigned selection = (tcw >> 21) & 63u;
        unsigned first = format == 6 ? (selection >> 4) * 256u : selection * 16u, count = format == 6 ? 256u : 16u;
        for (unsigned i = 0; i < count; i++) f = (f ^ palette[(first + i) & 1023u]) * 1099511628211ull;
        f ^= palette_format;
    }
    return f;
}
int pvr2_decode_texture(const uint8_t *vram, uint32_t tsp, uint32_t tcw,
                        uint8_t *rgba, size_t capacity, int *width, int *height,
                        const uint32_t *palette, unsigned palette_format) {
    if (!vram || !rgba || !width || !height) return 0;
    static int reserve = -1;   /* lue une fois : getenv à chaque texture coûtait */
    if (reserve < 0) reserve = getenv("PHANTOM_DC_TEXTURE_RESERVE") != NULL;
    uint32_t mask=reserve ? 2u*(VRAM_MASK+1u)-1u : VRAM_MASK;
    unsigned w = 8u << ((tsp >> 3) & 7u), h = 8u << (tsp & 7u);
    unsigned format = (tcw >> 27) & 7u, linear = (tcw >> 26) & 1u;
    unsigned stride = (tcw >> 25) & 1u, vq = (tcw >> 30) & 1u;
    unsigned mipmap = tcw >> 31;
    if (mipmap && (!linear || vq)) { h = w; if (vq) linear = 0; }
    else mipmap = 0;
    int paletted = format == 5 || format == 6, yuv = format == 3;
    /* Stride select only exists for non-twiddled textures; for paletted ones
     * that bit is part of the palette selector. Movie frames come this way:
     * the YUV converter writes rows of TEXT_CONTROL's length. */
    unsigned pitch = w;
    if (stride && !paletted) { if (!linear || !g_stride) return 0; pitch = g_stride; }
    if ((format > 3 && !paletted) || (yuv && vq) ||
        (paletted && (!palette || vq)) || capacity < (size_t)w * h * 4) return 0;
    uint32_t base = (tcw & 0x1fffffu) * 8u;
    uint32_t data_base = base;
    if (mipmap) {
        unsigned level = ((tsp >> 3) & 7u) + 3u;
        if (vq) {
            uint32_t preceding = 1, area = 1;
            for (unsigned i=1; i<level; ++i) { preceding += area; area *= 4; }
            data_base += 2048u + preceding;
        } else {
            uint32_t preceding = 3, area = 1;
            for (unsigned i=0; i<level; ++i) { preceding += area; area *= 4; }
            data_base += format == 5 ? preceding / 2u : format == 6 ? preceding : preceding * 2u;
        }
    } else if (vq) data_base += 2048u;
    for (unsigned y=0; y<h; ++y) for (unsigned x=0; x<w; ++x) {
        uint32_t at;
        if (vq) {
            uint32_t index = linear ? (y/2)*(w/2)+(x/2) : morton(x/2,y/2,w/2,h/2);
            unsigned code = vram[(data_base + index) & mask];
            at = base + code*8u + ((x&1u)*2u+(y&1u))*2u;
        } else {
            uint32_t index = linear ? y*pitch+x : morton(x,y,w,h);
            at = data_base + index*2u;
        }
        uint32_t p; unsigned pixel_format = format;
        uint8_t *d = rgba + ((size_t)y*w+x)*4;
        if (yuv) {
            /* YUV422: each texel holds its Y in the high byte; the even one
             * of a horizontal pair carries U, the odd one V. */
            unsigned x0 = x & ~1u, x1 = x | 1u;
            uint32_t a0 = data_base + (linear ? y*pitch+x0 : morton(x0,y,w,h))*2u;
            uint32_t a1 = data_base + (linear ? y*pitch+x1 : morton(x1,y,w,h))*2u;
            int Y = read16(vram, at, mask) >> 8;
            int U = (read16(vram, a0, mask) & 255) - 128, V = (read16(vram, a1, mask) & 255) - 128;
            d[0] = clamp255(Y + (V * 11) / 8);
            d[1] = clamp255(Y - (U * 11) / 32 - (V * 11) / 16);
            d[2] = clamp255(Y + (U * 55) / 32);
            d[3] = 255;
            continue;
        }
        if (paletted) {
            uint32_t ix = morton(x,y,w,h);
            unsigned selection = (tcw >> 21) & 63u, index;
            if (format == 6) index = (selection >> 4)*256u + vram[(data_base+ix)&mask];
            else {
                uint8_t packed = vram[(data_base+ix/2u)&mask];
                index = selection*16u + ((packed >> ((ix&1u)*4u)) & 15u);
            }
            p = palette[index]; pixel_format = palette_format & 3u;
            if (pixel_format == 3) {
                d[0]=(uint8_t)(p>>16); d[1]=(uint8_t)(p>>8);
                d[2]=(uint8_t)p; d[3]=(uint8_t)(p>>24); continue;
            }
        } else p = read16(vram, at, mask);
        if (pixel_format == 0) {
            unsigned r=(p>>10)&31, g=(p>>5)&31, b=p&31;
            d[0]=(uint8_t)((r<<3)|(r>>2)); d[1]=(uint8_t)((g<<3)|(g>>2));
            d[2]=(uint8_t)((b<<3)|(b>>2)); d[3]=(p&0x8000)?255:0;
        } else if (pixel_format == 1) {
            unsigned r=(p>>11)&31, g=(p>>5)&63, b=p&31;
            d[0]=(uint8_t)((r<<3)|(r>>2)); d[1]=(uint8_t)((g<<2)|(g>>4));
            d[2]=(uint8_t)((b<<3)|(b>>2)); d[3]=255;
        } else {
            d[0]=(uint8_t)(((p>>8)&15)*17); d[1]=(uint8_t)(((p>>4)&15)*17);
            d[2]=(uint8_t)((p&15)*17); d[3]=(uint8_t)(((p>>12)&15)*17);
        }
    }
    *width=(int)w; *height=(int)h; return 1;
}
