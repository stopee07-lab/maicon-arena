import hashlib,json,pathlib,tempfile,unittest
from unittest.mock import patch
import seed_model
ROOT=pathlib.Path(__file__).resolve().parents[1]
class DatasetSeed(unittest.TestCase):
 def test_installs_original_training_data_with_portable_paths_and_preserves_edits(self):
  self.assertTrue(callable(getattr(seed_model,'install_dataset',None)), 'shared training dataset installer is missing')
  with tempfile.TemporaryDirectory() as temp:
   root=pathlib.Path(temp)
   with patch.object(seed_model,'ROOT',root):
    result=seed_model.install_dataset(ROOT/'models')
    manifest=json.loads((result/'manifest.json').read_text())
    self.assertEqual(manifest['count'],720)
    self.assertEqual(manifest['dataset_sha256'],json.loads((ROOT/'models/model.json').read_text())['dataset_sha256'])
    self.assertNotIn('/Users/',(result/'data.yaml').read_text())
    for f in manifest['frames']:
     for path,expected in [(result/'raw'/(f['id']+'.jpg'),f['image_sha256']),(result/'images'/f['split']/(f['id']+'.jpg'),f['image_sha256']),(result/'labels'/f['split']/(f['id']+'.txt'),f['yolo_label_sha256'])]:
      self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),expected)
    marker=result/'manifest.json';marker.write_text('user edit')
    seed_model.install_dataset(ROOT/'models')
    self.assertEqual(marker.read_text(),'user edit')
if __name__=='__main__':unittest.main()
