import unittest,tempfile,subprocess,sys,shutil,time,json,os
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
class SupervisorRuntimeTests(unittest.TestCase):
 def run_fixture(self,d,child,phase='ready'):
  p=Path(d);shutil.copyfile(ROOT/'workspace/participant_supervisor.py',p/'participant_supervisor.py');(p/'team_node.py').write_text(child)
  (p/'robot_client.py').write_text('class ApiError(Exception):pass\nclass TeamClient:\n def __init__(self,*a,**k):pass\n def report_get(self,*a,**k):return '+repr({'phase':phase})+'\n')
  out=p/'out';out.mkdir();proc=subprocess.Popen([sys.executable,str(p/'participant_supervisor.py'),'--server','http://unused','--mission-code','fixture','--output',str(out)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
  return proc,out
 def test_actual_four_crashes_then_stop(self):
  with tempfile.TemporaryDirectory() as d:
   proc,out=self.run_fixture(d,'raise SystemExit(7)\n');proc.wait(15)
   events=[json.loads(x) for x in (out/'supervisor.jsonl').read_text().splitlines()]
   self.assertEqual(proc.returncode,7);self.assertEqual(sum(x['event']=='launch' for x in events),4);self.assertEqual(sum(x['event']=='retry_scheduled' for x in events),3)
 def test_manual_stop_no_respawn(self):
  with tempfile.TemporaryDirectory() as d:
   proc,out=self.run_fixture(d,'import time\ntime.sleep(30)\n');time.sleep(.4);proc.terminate();proc.wait(8)
   events=[json.loads(x) for x in (out/'supervisor.jsonl').read_text().splitlines()];self.assertEqual(sum(x['event']=='launch' for x in events),1);self.assertFalse(any(x['event']=='retry_scheduled' for x in events))
 def test_finished_never_launches(self):
  with tempfile.TemporaryDirectory() as d:
   proc,out=self.run_fixture(d,'raise Exception("must not run")','finished');proc.wait(3)
   self.assertEqual(proc.returncode,0);self.assertIn('finished_no_restart',(out/'supervisor.jsonl').read_text())
