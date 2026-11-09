import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'test-local' / 'dreamcast-research'))
from apprentissage import indexed_table_pointers


class IndexedTableTests(unittest.TestCase):
    def test_indexed_callback_table_and_repeated_ids(self):
        base = 0x8C400000
        data = bytearray(4096)
        prologue = bytes.fromhex('e62fd62f224ff07f')
        expected = set()
        for i in range(8):
            target = base + 0x400 + i * 0x40
            data[target-base:target-base+len(prologue)] = prologue
            offset = 0x100 + i * 8
            data[offset:offset+4] = (i + 16).to_bytes(4, 'little')
            data[offset+4:offset+8] = (target & 0x1fffffff).to_bytes(4, 'little')
            expected.add(target)
        self.assertTrue(expected <= indexed_table_pointers(bytes(data), base))
        for i in range(8):
            data[0x100+i*8:0x104+i*8] = (1).to_bytes(4, 'little')
        self.assertFalse(expected & indexed_table_pointers(bytes(data), base))


if __name__ == '__main__':
    unittest.main()
