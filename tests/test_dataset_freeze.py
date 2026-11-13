import unittest,tempfile,threading,base64,io,sys,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from workbench import Workbench,WorkbenchError
from PIL import Image
class FreezeTests(unittest.TestCase):
 def test_source_groups_frozen_hash_and_clone(self):
  with tempfile.TemporaryDirectory() as d:
   w=Workbench.__new__(Workbench);w.root=Path(d);w.store=Path(d)/'data/workbench';w.lock=threading.RLock();(w.store/'datasets').mkdir(parents=True)
   files=[]
   for i in range(18):
    f=io.BytesIO();Image.new('RGB',(32,32),(i*12,100,80)).save(f,'JPEG')
    files.append(dict(name=f'{i}.jpg',image_base64=base64.b64encode(f.getvalue()).decode(),episode='same-session',source_group=f'original-{i//6}',labels=[dict(class_id=i%6,bbox=[.5,.5,.5,.5])]))
   m=w.import_dataset({'files':files});f=w.freeze(m['id'],independent=True);self.assertTrue(f['source_audit']['independent_source_test']);path,got=w._dataset(m['id']);w._verify_dataset(path,got)
   clone=w.clone_dataset(m['id'],{});self.assertEqual([f['source_group'] for f in clone['frames']],[f['source_group'] for f in got['frames']])
   got['frames'][0]['source_group']='tampered'
   with self.assertRaises(WorkbenchError):w._verify_dataset(path,got)
 def test_malformed_source_group_rejected(self):
  with tempfile.TemporaryDirectory() as d:
   w=Workbench.__new__(Workbench);w.root=Path(d);w.store=Path(d);w.lock=threading.RLock();f=io.BytesIO();Image.new('RGB',(32,32)).save(f,'JPEG')
   with self.assertRaises(WorkbenchError):w.import_dataset({'files':[dict(name='x.jpg',image_base64=base64.b64encode(f.getvalue()).decode(),source_group=['bad'])]})
