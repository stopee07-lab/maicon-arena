import unittest,tempfile,io,json,sys,threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import run_evaluation
from subgame import Subgame
from engine import Engine,APIError
class OwnershipTests(unittest.TestCase):
 def test_batch_preflight_does_not_stop_existing_run(self):
  calls=[]
  def fetch(request,**kw):
   url=request if isinstance(request,str) else request.full_url;method='GET' if isinstance(request,str) else request.get_method();calls.append((url,method))
   payload={'admin_key':'fixture'} if url.endswith('/api/config') else {'physics':{'running':True},'controller':{'pid':12,'status':'running'}}
   return io.BytesIO(json.dumps(payload).encode())
  with tempfile.TemporaryDirectory() as d,patch.object(run_evaluation,'ROOT',Path(d)),patch.object(sys,'argv',['x','--model-id','fixture','--execute']),patch('urllib.request.urlopen',fetch):
   with self.assertRaises(RuntimeError):run_evaluation.main()
  self.assertFalse(any(method=='POST' for _,method in calls))
 def test_subgame_cancel_during_start_stays_stopped(self):
  with tempfile.TemporaryDirectory() as d:
   entered=threading.Event();release=threading.Event();generation=[0];e=Engine(practice_mode=True);started=[]
   def start(*a):e.paused=False;started.append(True);entered.set();release.wait(2)
   def check(ticket):
    if ticket!=generation[0]:raise APIError(409,'cancelled')
   physics=SimpleNamespace(stop=lambda:None,start=start)
   server=SimpleNamespace(engine=Engine(),rehearsal=Engine(),practice=e,physics=physics,workbench=None,require_no_ros=lambda:None,check_execution_ticket=check)
   s=Subgame(server,d);errors=[]
   def begin():
    try:s.begin(ticket=0)
    except APIError as exc:errors.append(exc.status)
   t=threading.Thread(target=begin);t.start();self.assertTrue(entered.wait(2));generation[0]+=1;s.stop();release.set();t.join(3)
   self.assertEqual(errors,[409]);self.assertTrue(e.paused);self.assertEqual(s.trial.status,'aborted')
