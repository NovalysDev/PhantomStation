import base64
import io
import json
import sys
import tempfile
import unittest
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
            with patch('rapport_erreur.urllib.request.build_opener',return_value=opener):
                send_game_diagnostic({'message':'error'},path)
            self.assertEqual(len(opener.calls),2)
            self.assertEqual(base64.b64decode(opener.calls[1]['diagnostic_b64']),b'complete-zip-content')
    def test_old_service_cannot_send_summary_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'report.zip';path.write_bytes(b'content');opener=FakeOpener(False)
            with patch('rapport_erreur.urllib.request.build_opener',return_value=opener):
                with self.assertRaises(OSError):send_game_diagnostic({'message':'error'},path)
            self.assertEqual(len(opener.calls),1)

if __name__=='__main__':unittest.main()
