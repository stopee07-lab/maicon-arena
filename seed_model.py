"""Install the bundled example-photo model without changing existing records."""
import hashlib
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parent

def install():
    model = json.loads((ROOT/'models/model.json').read_text())
    source = ROOT/'models/example-photo-yolo.pt'
    expected = model['weights_sha256']
    if hashlib.sha256(source.read_bytes()).hexdigest() != expected:
        raise RuntimeError('Bundled model checksum mismatch')
    directory = ROOT/'data/workbench/models'/model['id']
    directory.mkdir(parents=True, exist_ok=True)
    target = directory/'weights.pt'
    if target.exists():
        if hashlib.sha256(target.read_bytes()).hexdigest() != expected:
            raise RuntimeError('Existing model differs; refusing to replace it')
    else:
        shutil.copy2(source,target)
    record = directory/'model.json'
    if not record.exists():
        record.write_text(json.dumps(model, ensure_ascii=False, indent=2))
    print('Example-photo model ready:',model['id'])

if __name__ == '__main__':
    install()

