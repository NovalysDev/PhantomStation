#ifndef DC_CINEMATIC_H
#define DC_CINEMATIC_H
#include <stdint.h>
#include "hal/dc_hardware.h"
/* Detect films and extend the player's START press briefly, only during a film. */
void dc_cinematic_update(DCHardware *hw);
uint16_t dc_cinematic_buttons(void);
void dc_cinematic_offer_skip(void);
const char *dc_cinematic_message(void);
#endif
