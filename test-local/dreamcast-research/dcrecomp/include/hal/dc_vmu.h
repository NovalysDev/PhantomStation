#ifndef DCRECOMP_PVR2_VMU_H
#define DCRECOMP_PVR2_VMU_H
#include <stddef.h>
#include <stdint.h>
/* Diagnostic VMU. Existing files are opened only if exactly 128 KiB. */
int dc_vmu_open(const char *path);
#ifdef _WIN32
#include <wchar.h>
int dc_vmu_open_w(const wchar_t *path);
#endif
void dc_vmu_close(void);
int dc_vmu_active(void);
int dc_vmu_read(unsigned block, uint8_t out[512]);
int dc_vmu_write(unsigned block, unsigned phase, const uint8_t *data, size_t size);
#endif
#ifdef _WIN32
int dc_vmu_autoopen_w(const wchar_t *path);
#endif
