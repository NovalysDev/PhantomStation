#include <assert.h>
#include <stdint.h>
#include <string.h>
#include "hal/dc_cinematic.h"
static MapleController pad; static uint64_t now_us; static int movie;
uint64_t platform_get_ticks_us(void) { return now_us; }
MapleController *dc_maple_get_controller(DCHardware *hw, int port) { (void)hw; (void)port; return &pad; }
int dc_video_movie_active(void) { return movie; }
int main(void) {
 DCHardware *hw=(DCHardware *)(uintptr_t)1; pad.buttons=0xffff; now_us=1000000;
 dc_cinematic_update(hw); assert(!dc_cinematic_message() && !dc_cinematic_buttons());
 movie=1; dc_cinematic_update(hw); assert(!dc_cinematic_message());
 now_us+=9000000; dc_cinematic_update(hw); assert(!dc_cinematic_message());
 pad.buttons &= (uint16_t)~CONT_A; dc_cinematic_update(hw); assert(!dc_cinematic_buttons());
 pad.buttons |= CONT_A; pad.buttons &= (uint16_t)~CONT_START; dc_cinematic_update(hw);
 assert(dc_cinematic_buttons()==CONT_START && !dc_cinematic_message());
 pad.buttons |= CONT_START; now_us+=100000; dc_cinematic_update(hw); assert(dc_cinematic_buttons()==CONT_START);
 now_us+=150001; dc_cinematic_update(hw); assert(!dc_cinematic_buttons());
 now_us+=4000000; dc_cinematic_update(hw); assert(!dc_cinematic_message());
 pad.buttons &= (uint16_t)~CONT_START; dc_cinematic_update(hw); assert(dc_cinematic_buttons()==CONT_START);
 movie=0; dc_cinematic_update(hw); assert(!dc_cinematic_message() && !dc_cinematic_buttons());
 now_us+=1000000; dc_cinematic_update(hw); assert(!dc_cinematic_buttons());
 return 0;
}
