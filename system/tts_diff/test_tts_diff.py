import unittest
import importlib.util
import tempfile
from pathlib import Path
spec = importlib.util.spec_from_file_location('diff', Path(__file__).with_name('tts_diff.py'))
d = importlib.util.module_from_spec(spec)
spec.loader.exec_module(d)

class Tests(unittest.TestCase):
    def test_punctuation(self):
        for a,b in [('„Ala”, kot…','Ala kot'), ('Ala’s kot','Ala s kot'), ('Mam 5.','Mam 5'), ('1,5,','1,5')]:
            self.assertEqual(d.compare(a,b,polish=True)['status'],'ok', (a,b))
        for a,b in [('1,5','15'), ('3/4','34'), ('-5','5'), ('12:30','1230'), ('01','1'), ('21. marca','dwadzieścia jeden marca')]:
            self.assertEqual(d.compare(a,b,polish=True)['status'],'review', (a,b))
    def test_numbers(self):
        for a,b in [('21','dwadzieścia jeden'),('32','trzydziestu dwóch'),('100','stoma'),('19','dziewiętnastu')]:
            self.assertEqual(d.compare(a,b,polish=True)['status'],'number_format')
        for a,b in [('21','trzydzieści dwa'),('dwa','trzy'),('21 kot','dwadzieścia jeden pies'),('jedna osoba','jednego osoba'),('dwadzieścia. Jeden kot','21 kot')]:
            self.assertEqual(d.compare(a,b,polish=True)['status'],'review')
        self.assertEqual([p['type'] for p in d.compare('21 kot','dwadzieścia jeden pies',polish=True)['diff']],['number_format','replace'])
        self.assertEqual(d.compare('21','dwadzieścia jeden')['status'],'review')
    def test_stream(self):
        class Model:
            def transcribe(self, path): return {'text':'dwadzieścia jeden'}
        with tempfile.TemporaryDirectory() as path:
            root=Path(path); (root/'ok.wav').touch()
            rows=[{'id':1,'fileUrl':'/missing.wav','text':'x'}, {'id':2,'fileUrl':'/ok.wav','text':'21','language':'pl'}]
            records=list(d.run(rows,Model(),root,98))
            self.assertEqual([r['type'] for r in records], ['result','result','complete'])
            self.assertEqual([r['status'] for r in records[:2]], ['error','number_format'])
            self.assertEqual(records[1]['completed'],2)
class EndpointTests(unittest.TestCase):
    def setUp(self):
        import ast, asyncio, io, json, os, subprocess, sys, threading
        from types import SimpleNamespace
        from typing import List
        server = Path(__file__).resolve().parents[2] / 'tts_server.py'
        tree = ast.parse(server.read_text(encoding='utf-8'))
        nodes = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in ('AnalysisStreamingResponse', 'stream_analysis')]
        nodes[1].decorator_list = []
        class HTTPException(Exception):
            def __init__(self, status_code, detail): self.status_code = status_code
        class StreamingResponse:
            def __init__(self, content, media_type): self.content = content
            async def __call__(self, scope, receive, send):
                if scope['iterate']: next(self.content)
                raise RuntimeError('disconnect')
        class Input(io.StringIO):
            def close(self): self.saved = self.getvalue(); super().close()
        class Process:
            def __init__(self):
                self.stdin = Input(); self.stdout = io.StringIO('{"type":"result"}\n'); self.killed = False
            def poll(self): return 0 if self.killed else None
            def kill(self): self.killed = True
            def wait(self): return 0
        self.processes = []
        def popen(*args, **kwargs):
            self.processes.append(Process()); return self.processes[-1]
        self.lock = threading.Lock()
        namespace = {'StreamingResponse':StreamingResponse, 'asyncio':asyncio, '_analysis_lock':self.lock,
                     'threading':threading, 'HTTPException':HTTPException, 'Query':lambda default, **kw:default,
                     'List':List, 'TTSItem':object, 'json':json, 'os':os, 'sys':sys,
                     'this_dir':Path('.'), 'config':SimpleNamespace(get_output_directory=lambda:'output'),
                     'subprocess':SimpleNamespace(Popen=popen, PIPE=subprocess.PIPE)}
        exec(compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])), str(server), 'exec'), namespace)
        self.endpoint = namespace['stream_analysis']
        self.exception = HTTPException

    def test_disconnect_releases_lock_and_forwards_payload(self):
        import asyncio, json
        from types import SimpleNamespace
        item = SimpleNamespace(id=7, model_dump=lambda:{'id':7, 'text':'żółć'})
        for iterate in (False, True):
            response = self.endpoint([item], threshold=94, whisper_model='small')
            self.assertTrue(self.lock.locked())
            with self.assertRaises(self.exception): self.endpoint([item])
            with self.assertRaisesRegex(RuntimeError, 'disconnect'):
                asyncio.run(response({'iterate':iterate}, None, None))
            self.assertFalse(self.lock.locked())
            if iterate:
                process = self.processes[-1]
                self.assertEqual(json.loads(process.stdin.saved), [{'id':7, 'text':'żółć'}])
                self.assertTrue(process.killed)
                self.assertTrue(process.stdout.closed)
            response.cleanup()  # Idempotent after response cleanup.
            self.assertFalse(self.lock.locked())

    def test_cleanup_failure_releases_lock(self):
        from types import SimpleNamespace
        item = SimpleNamespace(id=7, model_dump=lambda:{'id':7})
        response = self.endpoint([item])
        next(response.content)
        process = self.processes[-1]
        def fail(): raise OSError('kill failed')
        process.kill = fail
        with self.assertRaisesRegex(OSError, 'kill failed'):
            response.cleanup()
        self.assertFalse(self.lock.locked())
        response.cleanup()
        response.content.close()

if __name__ == '__main__': unittest.main()
