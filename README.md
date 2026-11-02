# PhantomStation

**An experimental Windows front end for building and launching retro games from your own disc images and cartridges.**

PhantomStation brings several existing recompilation and native-port projects into one French-language interface. It can prepare a standalone Windows game package, manage a library, open per-game settings and keep save data outside the package. Its current focus includes European/PAL releases.

> **Development snapshot:** compatibility and installation are not complete. A generated executable, a boot screen or a playable opening does **not** prove that a game can be completed. The present repository is a private source snapshot for further development; it is not a ready-to-install release.

## Which download should I choose?

**To use PhantomStation, download `PhantomStation-portable-essai.zip`** from the [private test draft](https://github.com/NovalysDev/PhantomStation/releases). Extract it into a folder, then open `PhantomStation.exe`. Python, Git, the compilers and the available console engines are included. The download is approximately 836 MiB and takes approximately 2.4 GiB once extracted; building games requires additional space.

**For developers only:** `PhantomStation-sources-sans-dependances.zip` contains a smaller source snapshot. It does not include `PhantomStation.exe`, the engines, Python or the build tools and cannot run by itself. Its source, upstream version pins and local patches are also available in this repository.

The portable package has been tested locally, not yet on a clean independent PC. Both archives are experimental; no public stable release has been published. Game images and user saves are not included. Before any public distribution, third-party components and media, including the GoldenEye port, still require a review of their redistribution terms.

## What it does

- Select a console, provide a game image, choose a title and output folder, and start the build.
- Handle multiple PlayStation discs, with CUE files describing their BIN tracks.
- Package the runtime and game data in an executable with a game-specific launcher and artwork when available.
- Show a library organized by console, with per-game names, launch controls and save management.
- Warn before building a possible duplicate based on its title or a sampled image fingerprint. This is a warning, not a guarantee: versions, filenames and formats may differ.
- Provide controller, video, audio and cheat controls where implemented by the respective engine.
- Keep new games' saves under the user's Documents folder and offer backups of saves.
- Present a console-themed intro when the player starts a game.

The interface is currently **French**. Features and settings are not yet identical across consoles.

## Platforms and scope

| Platform | Inputs | Current approach | Status |
| --- | --- | --- | --- |
| PlayStation | `.cue` with its BIN tracks, `.bin`, or `.iso`; multiple discs | PSXRecomp, an experimental runtime and PhantomStation integrations | Experimental. Game, disc, BIOS and peripheral compatibility vary. |
| PlayStation 2 | `.iso` | PS2 native recompilation/runtime integration | Experimental. Games may fail to build, boot or progress. |
| NES | `.nes` | RetroRecomp / NESRecomp integration | Experimental in PhantomStation; mapper, region and game support vary. |
| Super Nintendo | `.sfc`, `.smc` | RetroRecomp / SNESRecomp integration | Experimental in PhantomStation; special chips and games require individual testing. |
| Nintendo 64 | `.z64`, `.n64`, `.v64` | Native Console hybrid recompilation and interpretation; a separate GoldenEye 007 (Nintendo 64, Europe/PAL) native-port path | Experimental. A successful build or boot is not evidence of full playability. |

Archives such as `.7z` must be extracted first; CHD is not currently supported. Selecting a French/PAL image does not translate a game. Compatibility depends on the exact image and revision. GoldenEye 007 for Nintendo 64, European/PAL version, uses a dedicated adaptation rather than the generic N64 path.

### Controllers and settings

The creation interface currently allows up to four configured players for PS1/N64, five for supported Super Nintendo multitap games, and two for NES/PS2. This does not add multiplayer to a single-player game. NES and PS2 multitap support is not currently implemented in the integrated engines. GoldenEye's dedicated port uses its own multiplayer menu.

Generic N64 controller mappings are configurable and shared between player slots. Video, audio and cheat settings differ by runtime. Generic N64 cheats support simple 8-bit and 16-bit direct-write codes; conditional, encrypted and multi-line formats are not implemented. NES/SNES cheat menus are not implemented.

## How packaging and saves work

The game package can contain the user's game image or embedded game data. On first launch, package data is extracted to `%LOCALAPPDATA%\\PhantomStation\\Jeux`; therefore the single EXE does not eliminate the storage needed by the extracted copy. Newer packages use `Documents\\PhantomStation\\Sauvegardes` for their saves and per-game settings. Older packages may use their original locations. Keep backups and test each game before removing its original images.

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

For each dependency, clone its upstream repository into the path expected by the application, check out the revision listed in the JSON file, then inspect and apply the corresponding patch with `git apply --check` followed by `git apply`. Native Console belongs under `n64-recomp-lab/moteur-n64-commun`. These steps restore source modifications only; toolchain setup, game-independent assets and runtime builds are still needed. GoldenEye's local generated game assets and prebuilt runtime are deliberately absent.

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

The license for original PhantomStation code has **not yet been selected**. This private snapshot should not be treated as a public release or a grant of a new license.

## Project status

This is a work in progress by **NovalysDev**. Priorities include reproducible installation, accurate per-game compatibility reporting, runtime stability, complete controller support, consistent settings and broader PAL testing. Dreamcast is a future idea, not a current feature.
