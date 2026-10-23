"""A deterministic gateway compatibility 503 has no chance of being delivered."""
import importlib.util
import ast
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
MODULE=ROOT/'leadfinder'/'ai_agent.py'
# Compile only the classifier to avoid importing optional Gemini/Streamlit deps
mod=ast.parse(MODULE.read_text(encoding='utf-8'))
func=next(n for n in mod.body if isinstance(n,ast.FunctionDef) and n.name=='is_definite_media_pre_send_rejection')
namespace={}
exec(compile(ast.fix_missing_locations(ast.Module(body=[func],type_ignores=[])),str(MODULE),'exec'),namespace)
class TestPreSend(unittest.TestCase):
    def test_compatibility_503_definite_failure(self):
        self.assertTrue(namespace['is_definite_media_pre_send_rejection'](
            RuntimeError('HTTP 503: {"error":"WhatsApp media compatibility unavailable: Cannot safely locate end of outgoing media message object"}')))
    def test_other_errors_are_uncertain(self):
        for message in ('HTTP 503: server could not process upload',
                        'HTTP 504: send operation timed out',
                        'HTTP 503: WhatsApp media upload timeout',
                        'server_ack_timeout',
                        'HTTP 400: unsafe media'):
            with self.subTest(message=message):
                self.assertFalse(namespace['is_definite_media_pre_send_rejection'](RuntimeError(message)))
if __name__=='__main__':unittest.main()
