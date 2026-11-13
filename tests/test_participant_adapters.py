import importlib.util
import json
import pathlib
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'workspace'))
from llm_adapter import TextLLM, LatestTask, validate_order, public_evidence

class AdapterTests(unittest.TestCase):
    def test_http_official_only_estimates_are_command_derived(self):
        from transport_facade import ParticipantClient
        from unittest.mock import patch
        with patch.dict('os.environ',{'MAICON_TRANSPORT':'http'},clear=True):
            client=ParticipantClient('http://localhost','fixture')
        class Fake:
            def sim_get(self,name):return {'hp':0}
            def sim_post(self,name,payload):return {'message':'ok' if name=='gimbal' else 'fired'}
        client.robot=Fake()
        client.sim_post('gimbal',{'pan':1.,'tilt':20.});client.sim_post('fire',{})
        state=client.sim_get('state')
        self.assertEqual(state['ammo'],7);self.assertEqual(state['tilt'],20.);self.assertTrue(state['laser'])
        self.assertIn('ammo',state['estimated_fields'])
    def test_ros_wrapper_preserves_url_and_mount_boundaries(self):
        import os,subprocess,tempfile
        with tempfile.TemporaryDirectory() as directory:
            path=pathlib.Path(directory);(path/'code').mkdir();(path/'output').mkdir();(path/'weights.pt').write_bytes(b'fixture');(path/'code'/'participant_supervisor.py').write_text('')
            docker=path/'docker';docker.write_text('#!/usr/bin/env python3\nimport json,sys;print(json.dumps(sys.argv[1:]))\n');docker.chmod(0o755)
            env=dict(os.environ,PATH=str(path)+os.pathsep+os.environ['PATH'])
            result=subprocess.run(['bash',str(ROOT/'ros2'/'run-participant.sh'),str(path/'code'/'participant_supervisor.py'),'--server','http://127.0.0.1:8887','--weights',str(path/'weights.pt'),'--output',str(path/'output')],env=env,capture_output=True,text=True,check=True)
            argv=json.loads(result.stdout);self.assertIn('http://host.docker.internal:8887',argv)
            self.assertIn(str(path/'code')+':'+str(path/'code')+':ro',argv)
            self.assertNotIn(str(ROOT)+':'+str(ROOT),argv)
    def test_configuration_fails_closed(self):
        with self.assertRaises(ValueError): TextLLM({'MAICON_LLM_MODE':'provided_api'})
    def test_enum_validation(self):
        with self.assertRaises(ValueError):validate_order({'target':'ADMIN','search_sectors':['CM'],'base':'RED_COMMAND_POST'})
    def test_private_data_never_sent(self):
        result=public_evidence({'tracks':{'R-1':{'distance':.1,'observed_frames':2,'hp':100,'ammo':3}},'frames':2,'hp':20})
        self.assertNotIn('hp',json.dumps(result));self.assertNotIn('ammo',json.dumps(result))
    def test_m6_rejects_extra_keys_and_non_string_callsign(self):
        adapter=TextLLM({'MAICON_LLM_MODE':'provided_api','LLM_ENDPOINT':'http://localhost/test','LLM_MODEL':'fake','LLM_KEY':'fake'})
        for value in ({'ranking':['R-1','R-2','R-3'],'extra':True},{'ranking':['R-1','R-2',{}]}):
            adapter.request=lambda *args,value=value:value
            with self.assertRaises(ValueError):adapter.rank({},'BLUE')
    def test_old_generation_discarded(self):
        task=LatestTask(); gate=threading.Event()
        task.submit('old',lambda:(gate.wait(.5),'old')[1]);task.submit('new',lambda:'new');gate.set()
        for _ in range(100):
            value=task.poll('new')
            if value:break
            time.sleep(.01)
        self.assertEqual(value,('ok','new'));self.assertIsNone(task.poll('old'))
    def test_openai_and_anthropic_wire(self):
        received=[]
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                received.append((self.headers, json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
                answer=json.dumps({'target':'RED_RADAR','search_sectors':['CM'],'base':'RED_MAINTENANCE'})
                data={'choices':[{'message':{'content':answer}}]} if self.path=='/openai' else {'content':[{'type':'text','text':answer}]}
                self.send_response(200);self.end_headers();self.wfile.write(json.dumps(data).encode())
            def log_message(self,*args):pass
        server=HTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
        try:
            for provider in ('openai','anthropic'):
                adapter=TextLLM({'MAICON_LLM_MODE':'provided_api','MAICON_LLM_PROVIDER':provider,'LLM_ENDPOINT':f'http://127.0.0.1:{server.server_port}/{provider}','LLM_MODEL':'test','LLM_KEY':'local-fake'})
                self.assertEqual(adapter.interpret('command','BLUE')['target'],'RED_RADAR')
            self.assertEqual(received[0][0]['Authorization'],'Bearer local-fake')
            self.assertEqual(received[1][0]['x-api-key'],'local-fake')
        finally:server.shutdown();server.server_close()
if __name__=='__main__':unittest.main()
