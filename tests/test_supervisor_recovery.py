import unittest,tempfile,subprocess,sys,time,json,os,signal
from pathlib import Path
from controller_recovery import stop_owned_controllers
class RecoveryTests(unittest.TestCase):
 def test_orphan_participant_is_verified_and_killed(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);dep=root/'data/workbench/deployments/deployment-fixture';code=dep/'code';code.mkdir(parents=True);out=root/'data/workbench/controllers/controller-fixture';out.mkdir(parents=True);weights=dep/'weights.pt';weights.write_bytes(b'x')
   script=code/'team_node.py';script.write_text('import signal,time\nsignal.signal(signal.SIGTERM,signal.SIG_IGN)\nwhile True:time.sleep(.1)\n')
   started=time.time();proc=subprocess.Popen([sys.executable,str(script),'--server','http://fixture','--output',str(out),'--weights',str(weights)])
   # Recorded supervisor is already gone; strict child command identity must recover it.
   (out/'launch.json').write_text(json.dumps(dict(output_dir=str(out),deployment_id=dep.name,code_path=str(script),pid=999999,started_at=started)))
   try:
    time.sleep(.2);result=stop_owned_controllers(root,'http://fixture',sys.executable);proc.wait(3);self.assertIsNotNone(proc.returncode);self.assertEqual(result['remaining'],[]);self.assertIn(str(out.resolve()),result['stopped'])
   finally:
    if proc.poll() is None:proc.kill();proc.wait()
