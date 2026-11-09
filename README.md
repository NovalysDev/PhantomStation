# PhantomStation

**An experimental Windows front end for building and launching retro games from your own disc images and cartridges.**

PhantomStation brings several existing recompilation and native-port projects into one interface. It can prepare a standalone Windows game package, manage a library, open per-game settings and keep save data outside the package. Its current focus includes European/PAL releases.

> **Experimental project:** compatibility and installation are not complete. A generated executable, a boot screen or a playable opening does **not** prove that a game can be completed. The portable download is a test version, not a stable release.

> **v1.0.5 test release:** all supported consoles can have bugs. Dreamcast support has been added and remains especially experimental. Please report issues with the in-game **F10** command or **Report a bug** button.

## Which download should I choose?

**To use PhantomStation, download `PhantomStation-portable-essai.zip`** from the [test release](https://github.com/NovalysDev/PhantomStation/releases). Extract it into a folder, then open `PhantomStation.exe`. Python, Git, the compilers and the available console engines are included. The download and extracted installation require substantial disk space; building games requires additional space.

**For developers only:** `PhantomStation-sources-sans-dependances.zip` contains a smaller source snapshot. It does not include `PhantomStation.exe`, the engines, Python or the build tools and cannot run by itself. Its source, upstream version pins and local patches are also available in this repository.

An earlier portable package was tested locally and on a separate test laptop; this version has been checked locally, compatibility is not established for every game or system. Both archives are experimental; no stable release has been published. Game images and user saves are not included. The project owner reports having permission to distribute the PS2 startup sound and GoldenEye component; those permissions have not been independently verified. Other third-party components remain subject to their respective licenses; see [the publication review](docs/PUBLICATION_REVIEW.md).

The latest test build improves handling of special characters in build paths, automatic SBI lookup when matching sources agree, console artwork, and reports for launch errors. Large PS2 discs are compressed when needed. If a packaged PS2 game is still 4 GiB or larger, start it from the PhantomStation library: Windows cannot execute that oversized file directly, so the library prepares a small launcher automatically. These changes do not establish that a game will run correctly from start to finish.

## Reporting a game problem

In the current development version, press **F10** during a game opened from the library, or after returning to PhantomStation. Describe the problem and choose whether to include the save and, for Dreamcast, any recorded memory blocks. After confirmation, the game closes before its final log is collected and the report is sent. Shared memory cards may contain other games' saves, and recorded blocks may contain game-memory data. The receiving address is not shown. One bounded log is kept per game and replaced at its next launch. F10 is also passed to the game, so a game that already uses F10 can react to it. The published v1.0.5 release retains its earlier reporting flow until the next release.

## What it does

- Select a console, provide a game image, choose a title and output folder, and start the build.
- Handle multiple PlayStation discs, with CUE files describing their BIN tracks.
- Package the runtime and game data in an executable with a game-specific launcher and artwork when available.
- Show a library organized by console, with per-game names, launch controls and save management.
- Warn before building a possible duplicate based on its title or a sampled image fingerprint. This is a warning, not a guarantee: versions, filenames and formats may differ.
- Provide controller, video, audio and cheat controls where implemented by the respective engine.
- Keep saves in the user's Documents folder, offer backups and select, import, name or rename memory cards from the library.
- Present a console-themed intro when the player starts a game.
- Retain per-disc observations from PS2 and Dreamcast builds and play sessions for subsequent builds of the same disc.

The compiler has a **French / English** selector that remembers the user's choice. Newly created PS2 and Nintendo launchers use the same setting; the PS1 launcher includes translated PhantomStation controls. Some engine messages and existing game launchers may remain in their original language. This setting does not translate the game itself: its language depends on the disc or cartridge. Features and settings are not yet identical across consoles.

## Platforms and scope

| Platform | Inputs | Current approach | Status |
| --- | --- | --- | --- |
| PlayStation | `.cue` with its BIN tracks, `.bin`, or `.iso`; multiple discs | PSXRecomp, an experimental runtime and PhantomStation integrations | Experimental. Game, disc, BIOS and peripheral compatibility vary. |
| PlayStation 2 | `.iso` | PS2 native recompilation/runtime integration | Experimental. Games may fail to build, boot or progress. |
| NES | `.nes` | RetroRecomp / NESRecomp integration | Experimental in PhantomStation; mapper, region and game support vary. |
| Super Nintendo | `.sfc`, `.smc` | RetroRecomp / SNESRecomp integration | Experimental in PhantomStation; special chips and games require individual testing. |
| Nintendo 64 | `.z64`, `.n64`, `.v64` | Native Console hybrid recompilation and interpretation; a separate GoldenEye 007 (Nintendo 64, Europe/PAL) native-port path | Experimental. A successful build or boot is not evidence of full playability. |
| Dreamcast | `.chd` | Experimental SH-4 recompilation and hardware runtime | Experimental. Graphics, audio, controls, saves and playability differ across games. |

Archives such as `.7z` must be extracted first. CHD input is supported by the experimental Dreamcast path; PS1 and PS2 use the formats listed above. Selecting a French/PAL image does not translate a game. Compatibility depends on the exact image and revision. GoldenEye 007 for Nintendo 64, European/PAL version, uses a dedicated adaptation rather than the generic N64 path.

### Controllers and settings

The creation interface currently allows up to four configured players for PS1/N64, five for supported Super Nintendo multitap games, and two for NES/PS2. This does not add multiplayer to a single-player game. NES and PS2 multitap support is not currently implemented in the integrated engines. GoldenEye's dedicated port uses its own multiplayer menu. The Dreamcast engine can detect up to four compatible controllers, but the current creation selector is limited to one player and multiplayer has not been verified across games. Joining with Start depends on the game and its multiplayer mode.

Generic N64 controller mappings are configurable and shared between player slots. Video, audio and cheat settings differ by runtime. Generic N64 cheats support simple 8-bit and 16-bit direct-write codes; conditional, encrypted and multi-line formats are not implemented. NES/SNES cheat menus are not implemented.

## Learning from builds and play sessions

For PS2 and Dreamcast, the compiler keeps observations for each disc locally. Exploration during a build can discover additional code; a play session can record previously missed code or execution information. A later build of the same disc can reuse the applicable discoveries. Dreamcast builds perform at least one exploration attempt. These records can contain game-derived data and stay on the local PC; they are not part of the public repository.

This is experimental: it does not automatically fix every problem or guarantee that a game runs to completion. A game already built does not change automatically when new information is recorded; it must be built again to incorporate applicable discoveries. Known-error records can identify previously encountered symptoms, but recording an error does not by itself create a repair.

## How packaging and saves work

The game package can contain the user's game image or embedded game data. On first launch, package data is extracted to `%LOCALAPPDATA%\\PhantomStation\\Jeux`; therefore the single EXE does not eliminate the storage needed by the extracted copy. Newer packages use `Documents\\PhantomStation\\Sauvegardes` for their saves and per-game settings. Older packages may use their original locations. Keep backups and test each game before removing its original images.

The library can create, import, name and rename memory cards; the label is stored separately from the card contents. Nintendo systems use per-game save profiles. PS1 and PS2 offer two selectors, while the current Dreamcast engine exposes one VMU. Older PS1 games and older PS2 games using the second slot may need to be built again to use these library selections.

Some optional features use network services: cover lookup can send the game name, optional cheat lookup requests publicly hosted catalogues, and update checks contact the configured update server. The error-report feature transmits a report only when the player selects its send action; the report can contain local file paths. Review the implementation and endpoints before deploying a release.

## Source snapshot: what is and is not included

This repository contains PhantomStation's front-end and packaging source, selected project-owned artwork, references to upstream versions, and patches for locally modified upstream projects. It intentionally excludes:

- Game ROMs, ISO/BIN/CUE images, compiled game packages and generated game data.
- User saves, settings, reports, personal files, build caches and downloaded toolchains.
- Third-party startup/error recordings, the N64 logo asset, downloaded game covers, cheat databases and other assets whose redistribution needs separate review.
- Compiled engines and several generated resources. The OpenBIOS-derived source is provided with its third-party license; no BIOS binary is included.

Consequently, **a clean checkout is not yet an immediately working installation**. Supplying or rebuilding the omitted assets, licensed dependencies, compilers and runtime binaries is part of the remaining work. Do not publish an installation package until those dependencies and their redistribution terms have been reviewed.

## Development layout

- `lanceur.py`, `phantom_ui.py`, `phantom_library_ui.py`: application and library.
- `detection_doublons.py`, `bibliotheque.py`: duplicate detection, metadata and saves.
- `emballer.py`, `unique_launcher.cs`: PS1 packaging and the shared package launcher.
- `phantom_ps2.py`, `phantom_ps2_*.cs`: PS2 build, package and launcher.
- `phantom_nintendo.py`, `phantom_nintendo_launcher.cs`, `phantom_n64_rom.py`: Nintendo integrations.
- `phantom_embedded_game.cs`, `phantom_compiler_game.py`: game-window integration.
- `cartes_memoire.py`, `gestion_jeux.py`: named cards, profiles and library storage.
- `phantom_dreamcast.py`, `phantom_dreamcast_*.cs`, `test-local/dreamcast-research/`: experimental Dreamcast integration, compiler and runtime source.
- `erreurs_connues.py`, `erreurs-connues.json`: local known-error records.
- `installer_*.py`: targeted changes applied to a generated PS1 project.
- `bios-personnalise/`: OpenBIOS-derived generated source and license, without a BIOS binary.
- `patches/` and `docs/upstream-revisions.json`: pinned dependency revisions and local modifications.
- `tests/`: a small selection of focused tests; these are not a full compatibility suite.

### Upstream source dependencies

The relevant dependency versions are recorded in `docs/upstream-revisions.json`. The patch files are generated against those revisions:

| Dependency | Upstream | Patch |
| --- | --- | --- |
| PSXRecomp | [RetroPortingToolKit/psxrecomp](https://github.com/RetroPortingToolKit/psxrecomp) | `patches/psxrecomp.patch` |
| PS2 native recomp | [exoteric33/ps2-native-recomp](https://github.com/exoteric33/ps2-native-recomp) | `patches/ps2-native-recomp.patch` |
| RetroRecomp | [ArthurReboulSalze/RetroRecomp](https://github.com/ArthurReboulSalze/RetroRecomp) | `patches/retrorecomp.patch` |
| Native Console | [RedReaper1515/native-console](https://github.com/RedReaper1515/native-console) | `patches/native-console.patch` |
| GoldenEye PC port | [jkdansereau/goldeneye-pc-port](https://github.com/jkdansereau/goldeneye-pc-port) | `patches/goldeneye-pc-pal.patch` |
| dcrecomp | [sp00nznet/dcrecomp](https://github.com/sp00nznet/dcrecomp) | Modified source under `test-local/dreamcast-research/dcrecomp` (GPL-2.0) |

For each dependency, clone its upstream repository into the path expected by the application, check out the revision listed in the JSON file, then inspect and apply the corresponding patch with `git apply --check` followed by `git apply`. Native Console belongs under `n64-recomp-lab/moteur-n64-commun`. These steps restore source modifications only; toolchain setup, game-independent assets and runtime builds are still needed. GoldenEye's local generated game assets and prebuilt runtime are deliberately absent. Dreamcast also needs separately obtained GLEW, libchdr, SDL2, build tools and startup assets; generated game data and binaries are excluded.

The current development environment uses Windows, Python with Tkinter, Pillow and CustomTkinter, Git, CMake/Ninja and C/C++ toolchains, plus the respective engine dependencies. The Python packages used directly by the front end are listed in `requirements.txt`. Exact build instructions and a reproducible dependency installer remain to be completed.

### Focused checks

From a configured Windows development environment, examples include:

```powershell
python -m pip install -r requirements.txt
python -m py_compile lanceur.py bibliotheque.py detection_doublons.py emballer.py phantom_nintendo.py phantom_ps2.py
$env:PYTHONPATH = '.'
python tests/test_detection_doublons.py
```

Other tests in `tests/` need the corresponding engines, artwork and tooling. Tests do not establish complete game compatibility.

## Credits and licensing

PhantomStation integrates independent upstream projects; it is not a claim of authorship over their engines. Their licenses and attribution requirements apply independently. In particular, check the non-commercial terms of the integrated PS1/NES/SNES components and the licenses of bundled dependencies before redistribution. OpenBIOS-derived material retains its included MIT notice. Logos and trademarks belong to their respective owners.

The license for original PhantomStation code has **not yet been selected**. Public access to this repository does not itself grant a new license to reuse that code.

## Project status

This is a work in progress by **NovalysDev**. Priorities include reproducible installation, accurate per-game compatibility reporting, runtime stability, complete controller support, consistent settings and broader PAL testing. The Dreamcast path is included as an experimental feature in the v1.0.5 test release.
