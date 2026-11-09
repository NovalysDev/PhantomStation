#!/usr/bin/env python3
"""Experimental, read-only identification of Dreamcast GD-ROM CHDs.

This is an analysis tool, not a game launcher or a completed recompiler.
It uses the existing libchdr binding from PSXRecomp and does not extract game
files or alter the source image. GD track layout follows the CHD metadata;
unsupported layouts are rejected rather than guessed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "psxrecomp" / "tools"))
from psx_chd import ChdDisc, ChdError, LibChdr, _make_tag  # noqa: E402

HD_START = 45000
SECTOR = 2048
MAX_BOOT_BYTES = 32 * 1024 * 1024
MAX_DIRECTORY_BYTES = 8 * 1024 * 1024
GD_TAGS = (_make_tag("C", "H", "G", "D"), _make_tag("C", "H", "G", "T"))


class DreamcastChd(ChdDisc):
    def _read_track_table(self):
        tracks = []
        disc_frame = 0
        stored_frame = 0
        for index in range(99):
            text = next((value for tag in GD_TAGS if (value := self._metadata(tag, index)) is not None), None)
            if text is None:
                break
            fields = dict(re.findall(r"([A-Z]+):([^\s\x00]+)", text))
            try:
                number, frames = int(fields["TRACK"]), int(fields["FRAMES"])
                pad, pregap = int(fields.get("PAD", "0")), int(fields.get("PREGAP", "0"))
                pgtype, kind = fields.get("PGTYPE", ""), fields["TYPE"]
            except (KeyError, ValueError) as exc:
                raise ChdError("Métadonnées de piste Dreamcast invalides") from exc
            inside = pregap if pregap and pgtype.startswith("V") else 0
            if (number != index + 1 or frames <= 0 or pregap < 0
                    or not 0 <= pad <= frames or not 0 <= inside <= frames):
                raise ChdError("Disposition des pistes Dreamcast invalide")
            start = disc_frame + pregap
            end = start + frames - inside - pad
            tracks.append({"number": number, "type": kind, "start": start, "end": end,
                           "stored": stored_frame + inside, "frames": frames})
            disc_frame = start + frames - inside
            stored_frame += (frames + 3) & ~3
        if len(tracks) < 3 or stored_frame > self.total_hunks * self.frames_per_hunk + 3:
            raise ChdError("Ce CHD ne contient pas une table GD-ROM complète et valide")
        return tracks

    def sector(self, lba: int) -> bytes:
        for track in self.tracks:
            if track["start"] <= lba < track["end"]:
                if track["type"] in ("MODE1_RAW", "MODE1/2352"):
                    offset = 16
                elif track["type"] in ("MODE1", "MODE1/2048"):
                    offset = 0
                else:
                    raise ChdError(f"Type de piste non pris en charge : {track['type']}")
                frame = track["stored"] + lba - track["start"]
                chunk = self._hunk(frame // self.frames_per_hunk)
                begin = (frame % self.frames_per_hunk) * 2448 + offset
                sector = chunk[begin:begin + SECTOR]
                if len(sector) != SECTOR:
                    raise ChdError(f"Secteur {lba} tronqué")
                return sector
        raise ChdError(f"Secteur {lba} absent des pistes de données")


def read_extent(disc, lba: int, size: int, limit: int) -> bytes:
    if size < 0 or size > limit:
        raise ChdError("Taille de fichier ou de répertoire excessive")
    return b"".join(disc.sector(lba + i) for i in range((size + SECTOR - 1) // SECTOR))[:size]


def identify_with_boot(disc) -> tuple[dict, bytes]:
    boot = disc.sector(HD_START)
    if not boot.startswith(b"SEGA SEGAKATANA"):
        raise ChdError("Signature Dreamcast absente de la zone haute densité")
    name = boot[0x60:0x70].decode("ascii", "replace").strip(" \0")
    if not name or "/" in name or "\\" in name:
        raise ChdError("Nom du programme de démarrage invalide")
    pvd = disc.sector(HD_START + 16)
    if pvd[0] != 1 or pvd[1:6] != b"CD001":
        raise ChdError("Système de fichiers ISO9660 introuvable")
    root = pvd[156:190]
    if len(root) < 34 or root[0] < 34:
        raise ChdError("Répertoire racine ISO invalide")
    directory = read_extent(disc, int.from_bytes(root[2:6], "little"),
                            int.from_bytes(root[10:14], "little"), MAX_DIRECTORY_BYTES)
    pos = 0
    while pos < len(directory):
        length = directory[pos]
        if not length:
            pos = (pos // SECTOR + 1) * SECTOR
            continue
        if length < 34 or pos + length > len(directory) or pos // SECTOR != (pos + length - 1) // SECTOR:
            raise ChdError("Entrée de répertoire ISO invalide")
        record = directory[pos:pos + length]
        if 33 + record[32] > len(record):
            raise ChdError("Nom de fichier ISO invalide")
        filename = record[33:33 + record[32]].decode("ascii", "replace").split(";")[0]
        if filename.upper() == name.upper() and not record[25] & 2:
            length_bytes = int.from_bytes(record[10:14], "little")
            payload = read_extent(disc, int.from_bytes(record[2:6], "little"), length_bytes, MAX_BOOT_BYTES)
            return ({"title": boot[0x80:0x100].decode("ascii", "replace").strip(" \0"),
                     "product": boot[0x40:0x50].decode("ascii", "replace").strip(" \0"),
                     "region": boot[0x30:0x38].decode("ascii", "replace").strip(" \0"),
                     "boot_file": name, "boot_bytes": length_bytes,
                     "boot_sha1": hashlib.sha1(payload).hexdigest(),
                     "status": "analyse uniquement ; moteur Dreamcast non terminé"}, payload)
        pos += length
    raise ChdError(f"Programme de démarrage {name} introuvable")


def identify(disc) -> dict:
    """Return metadata only; retain the existing read-only public interface."""
    return identify_with_boot(disc)[0]


def main():
    parser = argparse.ArgumentParser(description="Analyse expérimentale et non destructive d'un CHD Dreamcast")
    parser.add_argument("image", type=Path)
    parser.add_argument("--libchdr", required=True, type=Path, help="chemin de la bibliothèque libchdr")
    args = parser.parse_args()
    try:
        if args.image.suffix.lower() != ".chd":
            raise ChdError("Ce prototype accepte uniquement les images CHD GD-ROM")
        with DreamcastChd(args.image.resolve(), LibChdr(args.libchdr.resolve())) as disc:
            print(json.dumps(identify(disc), ensure_ascii=False, indent=2))
    except (ChdError, OSError) as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
