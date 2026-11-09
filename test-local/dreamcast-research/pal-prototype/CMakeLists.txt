cmake_minimum_required(VERSION 3.16)
project(CrazyTaxiPALPrototype C)
set(CMAKE_C_STANDARD 11)
set(SDL2_DIR "${CMAKE_CURRENT_SOURCE_DIR}/../../../n64-engine/generique/SDL2/lib/cmake/SDL2" CACHE PATH "" FORCE)
add_library(pal_glew STATIC ../glew-2.2.0/src/glew.c)
target_include_directories(pal_glew PUBLIC ../glew-2.2.0/include)
target_compile_definitions(pal_glew PUBLIC GLEW_STATIC)
target_link_libraries(pal_glew PUBLIC opengl32)
add_subdirectory(../dcrecomp dcrecomp-build)
target_compile_definitions(dcrecomp_core PRIVATE HAS_OPENGL GLEW_STATIC)
target_include_directories(dcrecomp_core PRIVATE ../glew-2.2.0/include)
get_target_property(CORE_SOURCES dcrecomp_core SOURCES)
list(REMOVE_ITEM CORE_SOURCES "src/hal/dc_bios.c")
set_property(TARGET dcrecomp_core PROPERTY SOURCES ${CORE_SOURCES} ${CMAKE_CURRENT_SOURCE_DIR}/dc_bios.c)
set(PAL_GENERATED_DIR "${CMAKE_CURRENT_SOURCE_DIR}/../generated-pal-next" CACHE PATH "Translated private game sources")
file(GLOB GAME_SOURCES CONFIGURE_DEPENDS "${PAL_GENERATED_DIR}/src/game/*.c")
option(PHANTOM_DC_GENERIC_TEST "Build the game-independent research bootstrap" OFF)
if(PHANTOM_DC_GENERIC_TEST)
  if(PHANTOM_DC_RESEARCH_MAIN)
    add_executable(pal_probe "${PHANTOM_DC_RESEARCH_MAIN}" pal_chd.c learn.c threads.c ${GAME_SOURCES})
  else()
    add_executable(pal_probe generic_main.c pal_chd.c learn.c threads.c ${GAME_SOURCES})
  endif()
else()
  add_executable(pal_probe main.c pal_chd.c vmu_fibers.c texture_fallback.c learn.c threads.c ${GAME_SOURCES})
endif()
target_include_directories(pal_probe PRIVATE "${PAL_GENERATED_DIR}/include" ../dcrecomp/include ../libchdr-6cde5348eb118da3baf94f75a69577a005a484fd/include)
if(PHANTOM_DC_GENERIC_TEST)
  # Recompiled games often loop across function boundaries using tail calls.
  # Optimisation lets clang turn those calls into jumps rather than recursively
  # consuming the host stack during normal guest code (e.g. clearing RAM).
  # -O2 was measured against -O1 on the same scenario (Grandia II, first minute):
  # 56 447 ms of game time against 56 481 ms, and 54.5 frames a second either
  # way. The translated code is not where the time goes, so -O1 stays.
  target_compile_options(pal_probe PRIVATE -O1 -foptimize-sibling-calls -w)
  # The Debug configuration left the core at -O0: every guest memory access,
  # interrupt poll and sound-processor step unoptimised. Grandia II spent its
  # time in its own interrupt handler and its movie ran at a frame every few
  # seconds.
  # "SHELL:" keeps CMake from dropping it as a duplicate of the -O2 before -O0.
  target_compile_options(dcrecomp_core PRIVATE "SHELL:-O2")
  # Main RAM accessed inline from translated code (recompiler/sh4_cpu.h).
  target_compile_definitions(pal_probe PRIVATE SH4_FAST_RAM)
else()
  target_compile_options(pal_probe PRIVATE -O0 -w)
endif()
target_compile_definitions(pal_probe PRIVATE DCRECOMP_ABI_CHECK)
target_link_libraries(pal_probe PRIVATE dcrecomp_core pal_glew ${CMAKE_CURRENT_SOURCE_DIR}/../libchdr-build/libchdr.dll.a dbghelp)
target_link_options(pal_probe PRIVATE -Wl,--stack,67108864)
add_executable(pal_chd_test chd_test.c pal_chd.c)
target_include_directories(pal_chd_test PRIVATE ../libchdr-6cde5348eb118da3baf94f75a69577a005a484fd/include)
target_link_libraries(pal_chd_test PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/../libchdr-build/libchdr.dll.a)
add_executable(pal_texture_test texture_test.c ../dcrecomp/src/hal/pvr2_texture.c)
target_include_directories(pal_texture_test PRIVATE ../dcrecomp/include)
add_executable(pal_vmu_test vmu_test.c ../dcrecomp/src/hal/dc_vmu.c)
target_include_directories(pal_vmu_test PRIVATE ../dcrecomp/include)
add_executable(pal_aica_pcm_test aica_pcm_test.c ../dcrecomp/src/hal/aica_pcm.c)
target_include_directories(pal_aica_pcm_test PRIVATE ../dcrecomp/include)
add_executable(pal_viewport_test viewport_test.c ../dcrecomp/src/hal/pvr2_viewport.c)
target_include_directories(pal_viewport_test PRIVATE ../dcrecomp/include)
add_executable(pal_ta_test ta_test.c ../dcrecomp/src/hal/pvr2_ta.c)
target_include_directories(pal_ta_test PRIVATE ../dcrecomp/include)
