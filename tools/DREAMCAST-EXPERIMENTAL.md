# Dreamcast experimental integration

The Dreamcast compiler and runtime support user-supplied CHD images. Some games reach playable scenes; compatibility, graphics, sound and saves still vary, and a successful build does not establish that a game can be completed.

The source under `test-local/dreamcast-research/` contains the build and learning scripts, the runtime and the modified dcrecomp source. It requires separately obtained GLEW, libchdr, SDL2 and the expected Windows build tools. The startup artwork and sound are not part of this source snapshot. The earlier v1.0.4 portable release does not contain this integration.

The runtime uses the GPL-2.0-licensed [dcrecomp](https://github.com/sp00nznet/dcrecomp) project; its included `LICENSE` and `NOTICE` describe the upstream origin. Generated sources, extracted game programs, game images, per-disc learning data and user saves are not included. Learning records are local to the user's machine and can be reused during a later build of the same disc.
