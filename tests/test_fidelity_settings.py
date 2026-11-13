import unittest,tempfile,sys,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import fidelity_settings as f
class SettingsTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);(self.root/'data').mkdir()
 def tearDown(self):self.tmp.cleanup()
 def test_secret_not_public_and_private_permissions(self):
  p=f.save(self.root,{'llm_key':'test-secret','seed':4});self.assertNotIn('test-secret',json.dumps(p));self.assertEqual((self.root/'data/llm-key.txt').stat().st_mode&0o777,0o600)
 def test_invalid_setting_does_not_mutate_secret(self):
  f.save(self.root,{'llm_key':'old'})
  with self.assertRaises(ValueError):f.save(self.root,{'llm_key':'new','seed':-1})
  self.assertEqual((self.root/'data/llm-key.txt').read_text(),'old')
 def test_unknown_modes_rejected(self):
  for k in ['sensor_profile','opponent_mode','transport','llm_mode']:
   with self.assertRaises(ValueError):f.save(self.root,{k:'bad'})
