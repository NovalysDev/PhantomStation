"""Exercise replacement of a running Windows launcher without touching user data."""
import hashlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import phantom_updates as updates


@unittest.skipUnless(os.name == 'nt', 'The running executable test requires Windows')
class DeferredUpdateTest(unittest.TestCase):
    def test_waits_for_launcher_preserves_saves_and_restarts_updated_exe(self):
        csc = Path(os.environ.get('WINDIR', r'C:\Windows')) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
        with tempfile.TemporaryDirectory(prefix='phantom-update-test-') as folder:
            base = Path(folder)
            shutil.copy2(ROOT / 'phantom_updates.py', base / 'phantom_updates.py')
            old_source = base / 'old.cs'
            new_source = base / 'new.cs'
            old_source.write_text('using System;using System.IO;using System.Threading;class X{static void Main(){File.WriteAllText(Environment.GetEnvironmentVariable("PHANTOM_OLD_STARTED"),"ok");Thread.Sleep(3500);}}')
            new_source.write_text('using System;using System.IO;class X{static void Main(){File.WriteAllText(Environment.GetEnvironmentVariable("PHANTOM_NEW_STARTED"),"ok");}}')
            old_exe, new_exe = base / 'PhantomStation.exe', base / 'replacement.exe'
            for source, output in ((old_source, old_exe), (new_source, new_exe)):
                subprocess.run([str(csc), '/nologo', '/target:winexe', '/out:' + str(output), str(source)],
                               check=True, capture_output=True)
            old_bytes, new_bytes = old_exe.read_bytes(), new_exe.read_bytes()
            saved = base / 'sauvegardes/jeu.mcd'
            saved.parent.mkdir(); saved.write_bytes(b'preserve this save')
            archive_bytes = io.BytesIO()
            with zipfile.ZipFile(archive_bytes, 'w') as archive:
                archive.writestr('PhantomStation.exe', new_bytes)
            data = archive_bytes.getvalue()
            manifest = {'version': '1.0.2', 'taille': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
            marker_old, marker_new = base / 'old-started', base / 'new-started'
            env = os.environ.copy()
            env['PHANTOM_OLD_STARTED'] = str(marker_old)
            env['PHANTOM_NEW_STARTED'] = str(marker_new)
            previous_old, previous_new = os.environ.get('PHANTOM_OLD_STARTED'), os.environ.get('PHANTOM_NEW_STARTED')
            os.environ['PHANTOM_OLD_STARTED'], os.environ['PHANTOM_NEW_STARTED'] = str(marker_old), str(marker_new)
            parent = subprocess.Popen([str(old_exe)], cwd=base, env=env)
            try:
                deadline = time.monotonic() + 10
                while not marker_old.exists() and time.monotonic() < deadline:
                    time.sleep(.05)
                self.assertTrue(marker_old.exists())
                helper = updates.defer_update(manifest, data, base=base, parent_pid=parent.pid, python=sys.executable)
                time.sleep(.3)
                self.assertIsNone(parent.poll())
                self.assertEqual(old_exe.read_bytes(), old_bytes)
                parent.wait(timeout=10)
                deadline = time.monotonic() + 20
                while not marker_new.exists() and time.monotonic() < deadline:
                    time.sleep(.05)
                self.assertTrue(marker_new.exists(), 'Updated launcher did not restart')
                self.assertEqual(helper.wait(timeout=10), 0)
                self.assertEqual(old_exe.read_bytes(), new_bytes)
                self.assertEqual(saved.read_bytes(), b'preserve this save')
            finally:
                if parent.poll() is None:
                    parent.terminate(); parent.wait(timeout=5)
                for name, old in (('PHANTOM_OLD_STARTED', previous_old), ('PHANTOM_NEW_STARTED', previous_new)):
                    if old is None: os.environ.pop(name, None)
                    else: os.environ[name] = old


if __name__ == '__main__':
    unittest.main()
