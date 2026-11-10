#ifndef DCRECOMP_AICA_PCM_H
#define DCRECOMP_AICA_PCM_H
#include <stdint.h>
#include <stddef.h>
/* Les 64 voix de la puce son (enveloppes, LFO, volumes) ; pas de DSP ni de filtre.
 * Les registres sont modifiables : une voie qui finit remet son bit KYONB à 0. */
void aica_pcm_reset(void);
void aica_pcm_key_execute(uint32_t *registers);
void aica_pcm_reg_write(const uint32_t *registers, uint32_t offset);
void aica_pcm_set_master(unsigned mvol, unsigned mono);
uint32_t aica_pcm_position(unsigned channel);
uint32_t aica_pcm_status(unsigned channel);
uint32_t aica_pcm_status_ex(unsigned channel, int clear);
int aica_pcm_channel_active(unsigned channel);
void aica_pcm_mix(uint32_t *registers, const uint8_t *ram,
                  int16_t *stereo, size_t frames);
/* Aggregate numeric diagnostics only; never records sound samples. */
void aica_pcm_diagnostic_flush(void);
/* Internal component of a future complete checkpoint, not a game save. The
 * caller must stop the mixer and restore sound registers and RAM separately.
 * The supplied sound RAM must remain alive while the mixer is in use. */
size_t aica_pcm_checkpoint_size(void);
int aica_pcm_checkpoint_capture(void *buffer, size_t size);
int aica_pcm_checkpoint_restore(const void *buffer, size_t size, const uint8_t *sound_ram);
#endif
