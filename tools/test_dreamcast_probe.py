import hashlib
import unittest

from dreamcast_probe import ChdError, HD_START, MAX_BOOT_BYTES, identify, identify_with_boot, read_extent


class FakeDisc:
    def __init__(self):
        boot = bytearray(2048)
        boot[:15] = b"SEGA SEGAKATANA"
        boot[0x30:0x38] = b"E       "
        boot[0x40:0x50] = b"TEST-001 V1.000 "
        boot[0x60:0x70] = b"1ST_READ.BIN    "
        boot[0x80:0x89] = b"TEST GAME"
        pvd = bytearray(2048)
        pvd[:6] = b"\x01CD001"
        pvd[156] = 34
        pvd[158:162] = (HD_START + 20).to_bytes(4, "little")
        pvd[166:170] = (2048).to_bytes(4, "little")
        directory = bytearray(2048)
        filename = b"1ST_READ.BIN;1"
        directory[0] = 33 + len(filename)
        directory[2:6] = (HD_START + 21).to_bytes(4, "little")
        directory[10:14] = (3).to_bytes(4, "little")
        directory[32] = len(filename)
        directory[33:33 + len(filename)] = filename
        self.sectors = {HD_START: bytes(boot), HD_START + 16: bytes(pvd),
                        HD_START + 20: bytes(directory), HD_START + 21: b"abc" + bytes(2045)}

    def sector(self, lba):
        return self.sectors[lba]


class ProbeTests(unittest.TestCase):
    def test_identification_and_hash(self):
        info = identify(FakeDisc())
        self.assertEqual(info["title"], "TEST GAME")
        self.assertEqual(info["region"], "E")
        self.assertEqual(info["boot_sha1"], hashlib.sha1(b"abc").hexdigest())

    def test_boot_bytes_and_metadata_match(self):
        info, payload = identify_with_boot(FakeDisc())
        self.assertEqual(payload, b"abc")
        self.assertEqual(info, identify(FakeDisc()))
        self.assertEqual(info["boot_bytes"], len(payload))

    def test_rejects_wrong_signature(self):
        disc = FakeDisc()
        disc.sectors[HD_START] = bytes(2048)
        with self.assertRaises(ChdError):
            identify(disc)

    def test_rejects_invalid_directory_record(self):
        disc = FakeDisc()
        disc.sectors[HD_START + 20] = b"\x01" + bytes(2047)
        with self.assertRaises(ChdError):
            identify(disc)

    def test_extent_size_is_bounded(self):
        with self.assertRaises(ChdError):
            read_extent(FakeDisc(), HD_START, MAX_BOOT_BYTES + 1, MAX_BOOT_BYTES)


if __name__ == "__main__":
    unittest.main()
