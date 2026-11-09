import base64
import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from rapport_erreur import send_game_diagnostic

class Response:
    status=200
    def __init__(self, body):self.body=json.dumps(body).encode()
    def __enter__(self):return self
    def __exit__(self,*args):return False
    def read(self,n):return self.body

class FakeOpener:
    def __init__(self, supported):self.supported=supported;self.calls=[]
    def open(self, request, timeout):
        data=json.loads(request.data.decode());self.calls.append(data)
        if data.get('diagnostic_probe'):return Response({'diagnostic_complet':1 if self.supported else 0})
        return Response({'message':'ok','diagnostic_recu':True})

class SendTests(unittest.TestCase):
    def test_full_archive_sent_after_capability_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'report.zip';path.write_bytes(b'complete-zip-content')
            opener=FakeOpener(True)
            with patch.dict(os.environ, {'LOCALAPPDATA': tmp}), patch('rapport_erreur.urllib.request.build_opener',return_value=opener):
                send_game_diagnostic({'message':'error'},path)
                trace=(Path(tmp)/'PhantomStation/Journaux/envoi-rapport.log').read_text(encoding='utf-8')
            self.assertIn('Réception confirmée',trace)
            self.assertNotIn('@',trace)
            self.assertEqual(len(opener.calls),2)
            self.assertEqual(base64.b64decode(opener.calls[1]['diagnostic_b64']),b'complete-zip-content')
    def test_old_service_cannot_send_summary_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'report.zip';path.write_bytes(b'content');opener=FakeOpener(False)
            with patch.dict(os.environ, {'LOCALAPPDATA': tmp}), patch('rapport_erreur.urllib.request.build_opener',return_value=opener):
                with self.assertRaises(OSError):send_game_diagnostic({'message':'error'},path)
            self.assertEqual(len(opener.calls),1)

    def test_http_failure_is_recorded_without_addresses(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'report.zip';path.write_bytes(b'content')
            opener=FakeOpener(True)
            original=opener.open
            def fail_send(request, timeout):
                if json.loads(request.data).get('diagnostic_probe'):
                    return original(request, timeout)
                raise urllib.error.HTTPError(request.full_url, 503, 'unavailable', {}, None)
            opener.open=fail_send
            with patch.dict(os.environ, {'LOCALAPPDATA': tmp}), patch('rapport_erreur.urllib.request.build_opener',return_value=opener):
                with self.assertRaises(OSError):send_game_diagnostic({'message':'error'},path)
                trace=(Path(tmp)/'PhantomStation/Journaux/envoi-rapport.log').read_text(encoding='utf-8')
            self.assertIn('HTTP 503',trace)
            self.assertNotIn('@',trace)

if __name__=='__main__':unittest.main()
