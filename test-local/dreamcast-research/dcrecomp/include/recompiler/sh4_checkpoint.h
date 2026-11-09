#ifndef SH4_CHECKPOINT_H
#define SH4_CHECKPOINT_H

#include <stddef.h>
#include <stdint.h>
#include "recompiler/sh4_cpu.h"

/* CPU and its three memory regions only. This is NOT a complete Dreamcast
 * save state: devices, audio, graphics and the native call chain must also be
 * captured at a resumable instruction before it can be used by a game.
 * The caller must stop all writers while copying and must supply a separate,
 * non-overlapping buffer. Images are valid only for the same ABI/build. */
size_t sh4_checkpoint_size(const SH4CPU *cpu);
int sh4_checkpoint_capture(const SH4CPU *cpu, void *buffer, size_t capacity);
int sh4_checkpoint_restore(SH4CPU *cpu, const void *buffer, size_t size);

#endif
