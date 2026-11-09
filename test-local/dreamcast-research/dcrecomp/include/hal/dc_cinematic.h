#ifndef DC_CINEMATIC_H
#define DC_CINEMATIC_H

#include <stdint.h>
#include "hal/dc_hardware.h"

/* Detection only: no automatic button press unless explicitly requested. */

/* Called once per host frame. Detects Sofdec/YUV movie playback. By default it
 * presses nothing: the game itself offers to skip with START. */
void dc_cinematic_update(DCHardware *hw);

/* Buttons to force pressed in the Maple controller response while a skip
 * burst is running: the opt-in automatic mode, or the burst armed by the
 * player's own START press during a movie. Active-low Dreamcast convention:
 * the returned bits are cleared there. */
uint16_t dc_cinematic_buttons(void);

/* Only while a detected movie is playing; NULL during ordinary gameplay. */
const char *dc_cinematic_message(void);

#endif /* DC_CINEMATIC_H */
