# Private publication review — 2 November 2026

This is a technical review of the private project and its test archives. It is
not a legal opinion and does not approve publication. The public repository
and the downloadable Windows archive must be assessed separately.

## Material to resolve before public distribution

- PSXRecomp, NESRecomp and SNESRecomp use PolyForm Noncommercial 1.0.0.
  The archive contains their license texts. Permitted use and required notices
  must be checked against the intended distribution and any commercial plans.
  See [the license](https://polyformproject.org/licenses/noncommercial/1.0.0/).
- The portable test archive includes a GoldenEye-specific runtime and support
  files. Its `n64-engine/goldeneye-pal/licences/GoldenEye-NOTICE.txt` states
  that the port's MIT license does not cover all inherited decompilation
  material. Determine rights and any alternative before sharing this archive
  publicly. See the [upstream notice](https://github.com/jkdansereau/goldeneye-pc-port/blob/main/NOTICE).
- The portable archive contains console logos and startup/error recordings.
  `assets/SOURCE-son-demarrage-ps2.txt` describes one sound as for personal
  use and says it is excluded from public updates. Its
  [source page](https://www.freesoundslibrary.com/ps2-startup-sound/) describes
  context-specific fair use; that is not a general permission established by
  this review. Check rights or obtain authorized replacements before public
  redistribution. This review does not determine legal ownership.
- A license for original PhantomStation code has not been selected. The owner
  should choose one if they wish to grant reuse permissions, after the
  third-party terms and scope of original material have been reviewed.

## Technical checks

- The repository is private; its two release assets remain in a draft. The
  local archives matched GitHub's recorded SHA-256 digests and byte sizes.
- The archive passed a ZIP integrity check. No game image or save was found by
  filename/extension inspection. A blank PS2 card and the OpenBIOS-derived
  binary are intentionally included. Filename checks do not prove that every
  possible protected or personal item has been found.
- A targeted scan covered the five Git commits and 118 unique tracked blobs.
  No matching common GitHub/OpenAI token, private-key marker or owner-specific
  Windows path was found in scanned text. A targeted scan of 21,914 text files
  in the portable archive likewise found no matching patterns. These are not
  exhaustive secret or malware scans. The archive also contains public CA
  certificate bundles and two CMake temporary-key templates; their presence is
  not evidence of the owner's private signing key.
- On the developer's Windows PC, local tests prepared a PS1 game and produced
  one packaged EXE, generated NES, SNES and N64 examples including GoldenEye,
  and built the PS2 runtime using the supplied game image. These tests do not
  establish complete playability. The portable GUI and bundled dependencies
  were checked in an isolated directory; this is not a separate clean PC.
- The signed update endpoint responded successfully during a read-only check.
  A review found six application files missing from the update allowlist; the
  correction and local preservation/security tests are tracked in the code.
  The updater does not yet offer general updates to all bundled toolchains or
  Nintendo engine sources. An end-to-end signed update of a release artifact
  and upgrade from older drafts remain to be validated.
- Error reports are sent only when the user requests it; they can include game
  names and local paths. Optional online cover, SBI and cheat lookups can send
  queries to their respective services. Describe this to users before release.

## Remaining work

1. Resolve the third-party rights and notices for the actual public artifact.
2. Audit full history and packaged binary content more broadly; consider a
   dedicated security/secret scan before changing visibility.
3. Test a fresh install and representative game builds on an independent
   Windows PC, including a full signed update while preserving user data.
4. Make development setup reproducible, verify the published release matches
   its source and document known compatibility limitations.
