import unittest,tempfile,json
from pathlib import Path
from types import SimpleNamespace
from engine import Engine
from experiment_journal import ExperimentJournal
class JournalTests(unittest.TestCase):
 def test_restart_never_overwrites_finished_result(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);store=root/'data/workbench';directory=store/'controllers/controller-old';directory.mkdir(parents=True);dep=store/'deployments/deployment-old';dep.mkdir(parents=True)
   (directory/'launch.json').write_text(json.dumps({'deployment_id':'deployment-old','started_at':1}));(dep/'manifest.json').write_text(json.dumps({'mode':'match'}));out=root/'data/experiments/controller-old';out.mkdir(parents=True);(out/'experiment.json').write_text('{"status":"completed","score":40}');(out/'match.json').write_text('{"preserve":true}')
   wb=SimpleNamespace(controller_directory=directory,store=store,controller_process=None);server=SimpleNamespace(workbench=wb,engine=Engine(),practice=Engine(practice_mode=True),rehearsal=Engine())
   ExperimentJournal(server,root).observe('user_stop')
   self.assertEqual((out/'experiment.json').read_text(),'{"status":"completed","score":40}');self.assertEqual((out/'match.json').read_text(),'{"preserve":true}')
