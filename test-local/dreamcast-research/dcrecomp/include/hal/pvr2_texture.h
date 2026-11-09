#ifndef DCRECOMP_PVR2_TEXTURE_H
#define DCRECOMP_PVR2_TEXTURE_H
#include <stddef.h>
#include <stdint.h>
/* rgba capacity must cover 4 * width * height. Returns 0 for unsupported modes. */
int pvr2_decode_texture(const uint8_t *vram, uint32_t tsp, uint32_t tcw,
                        uint8_t *rgba, size_t capacity, int *width, int *height,
                        const uint32_t *palette, unsigned palette_format);
/* Row length of stride-select textures, from TEXT_CONTROL (0 = none set). */
void pvr2_texture_set_stride(unsigned pixels);
/* Empreinte de tout ce que lit le décodage de cette texture : ses octets en
 * mémoire vidéo (livre de codes et niveaux réduits compris), la part de palette
 * et le pas de ligne qu'elle utilise. Même empreinte = même image décodée. */
uint64_t pvr2_texture_fingerprint(const uint8_t *vram, uint32_t tsp, uint32_t tcw,
                                  const uint32_t *palette, unsigned palette_format);
#endif
