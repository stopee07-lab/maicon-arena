import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
class DatasetIdentityTests(unittest.TestCase):
 def test_shared_source_detected_despite_distinct_frames(self):
  from dataset_identity import source_audit
  a=source_audit([{'split':'train','source_group':'photo1'},{'split':'test','source_group':'photo1'},{'split':'val','source_group':'photo2'}])
  self.assertFalse(a['independent_source_test']);self.assertEqual(a['shared_source_groups'],['photo1'])
 def test_missing_sources_not_claimed_independent(self):
  from dataset_identity import source_audit
  self.assertFalse(source_audit([{'split':'train'},{'split':'val'},{'split':'test'}])['independent_source_test'])
 def test_disjoint_groups(self):
  from dataset_identity import source_audit
  self.assertTrue(source_audit([{'split':s,'source_group':s} for s in ['train','val','test']])['independent_source_test'])
if __name__=='__main__':unittest.main()
