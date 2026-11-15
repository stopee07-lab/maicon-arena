"""Explicit live lifecycle regression; run with MAICON_LIFECYCLE_LIVE=1.

Uses a temporary source copy, a fresh port, no browser and no real robot.
"""
import json,os,shutil,signal,socket,subprocess,sys,tempfile,time,unittest
from pathlib import Path
from urllib.request import urlopen,Request

@unittest.skipUnless(os.environ.get('MAICON_LIFECYCLE_LIVE')=='1','explicit live lifecycle check')
class LauncherLifecycle(unittest.TestCase):
 def setUp(self):
  import psutil
  self.psutil=psutil
  self.tmp=tempfile.TemporaryDirectory(prefix='maicon-lifecycle-');self.addCleanup(self.tmp.cleanup)
  self.root=Path(self.tmp.name)/'arena'
  shutil.copytree(Path(__file__).resolve().parents[1],self.root,ignore=shutil.ignore_patterns('.git','.venv','data','runtime','physics_data','__pycache__'))
  with socket.socket() as s:s.bind(('127.0.0.1',0));self.port=s.getsockname()[1]
  self.url=f'http://127.0.0.1:{self.port}'
  self.env=dict(os.environ,MAICON_PYTHON=sys.executable)
  self.command=[sys.executable,str(self.root/'launch.py'),'--no-browser','--port',str(self.port)]
  self.owned=[]
 def ready(self):
  until=time.monotonic()+30
  while time.monotonic()<until:
   try:
    with urlopen(self.url+'/api/config',timeout=.3) as r:return json.load(r)
   except (OSError,ValueError):time.sleep(.1)
  self.fail('server not ready')
 def track(self,pid):
  self.owned=self.psutil.Process(pid).children(recursive=True)
  self.addCleanup(self.cleanup_owned)
 def cleanup_owned(self):
  for p in reversed(self.owned):
   try:p.kill()
   except self.psutil.NoSuchProcess:pass
 def assert_closed(self):
  until=time.monotonic()+18
  while time.monotonic()<until:
   with socket.socket() as s:closed=s.connect_ex(('127.0.0.1',self.port))!=0
   alive=[p for p in self.owned if p.is_running() and p.status()!=self.psutil.STATUS_ZOMBIE]
   if closed and not alive:return
   time.sleep(.1)
  self.fail(f'port or owned children survived: port_closed={closed}, children={len(alive)}')
 def test_forced_launcher_death_cleans_server_and_workers(self):
  p=subprocess.Popen(self.command,env=self.env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
  self.addCleanup(lambda:p.kill() if p.poll() is None else None)
  access=self.ready()
  # Run both physical cameras and the real participant/model process.
  headers={'X-Admin-Key':access['admin_key'],'Content-Type':'application/json'}
  with urlopen(Request(self.url+'/api/workbench/status',headers=headers),timeout=5) as r:state=json.load(r)
  body=json.dumps({'mode':'practice','policy':'observe','model_id':state['models'][0]['id']}).encode()
  with urlopen(Request(self.url+'/api/development/apply-run',data=body,headers=headers),timeout=50) as r:started=json.load(r)
  self.assertTrue(started['started']);self.assertTrue(started['controller']['loaded'])
  self.track(p.pid);self.assertGreaterEqual(len(self.owned),3)
  p.kill();p.wait(timeout=5);self.assert_closed()
 @unittest.skipUnless(os.name=='posix','POSIX terminal hangup')
 def test_closing_actual_pty_cleans_server(self):
  import pty
  pid,master=pty.fork()
  if pid==0:os.execve(sys.executable,self.command,self.env)
  try:
   self.ready();self.track(pid);os.close(master);master=None
   self.assert_closed()
  finally:
   if master is not None:os.close(master)
   try:os.kill(pid,signal.SIGKILL)
   except ProcessLookupError:pass
   os.waitpid(pid,0)

if __name__=='__main__':unittest.main()
