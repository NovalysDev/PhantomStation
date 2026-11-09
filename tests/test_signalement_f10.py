import os
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from phantom_report_hotkey import GameReportHotkey

class F10Tests(unittest.TestCase):
    def test_only_game_or_compiler_foreground_and_no_key_repeat(self):
        h=GameReportHotkey();h.update_pids({12345})
        h._on_key(0x79, 0x100, 88888);self.assertFalse(h.consume())
        h._on_key(0x79, 0x101, 88888)
        h._on_key(0x79, 0x100, 12345);h._on_key(0x79, 0x100, 12345)
        self.assertTrue(h.consume());self.assertFalse(h.consume())
        h._on_key(0x79, 0x101, 12345);h._on_key(0x79, 0x100, os.getpid())
        self.assertTrue(h.consume());self.assertFalse(h.consume())
    def test_hook_starts_and_stops(self):
        h=GameReportHotkey()
        try:self.assertTrue(h.start())
        finally:h.close()
        self.assertFalse(h.thread.is_alive())

if __name__ == '__main__':unittest.main()
