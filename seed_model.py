"""Install the bundled example-photo model without changing existing records."""
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parent

def install_dataset(bundle_dir=None):
    """Restore bundled original frames once; never replace an existing dataset."""
    bundle_dir = Path(bundle_dir) if bundle_dir is not None else ROOT/'models'
    info = json.loads((bundle_dir/'dataset-bundle.json').read_text(encoding='utf-8'))
    model = json.loads((bundle_dir/'model.json').read_text(encoding='utf-8'))
    if info['id'] != model['dataset_id'] or Path(info['id']).name != info['id']:
        raise RuntimeError('Bundled dataset identity mismatch')
    parent = ROOT/'data/workbench/datasets'
    target = parent/info['id']
    if target.exists():
        return target
    archive = bundle_dir/info['archive']
    if hashlib.sha256(archive.read_bytes()).hexdigest() != info['sha256']:
        raise RuntimeError('Bundled dataset checksum mismatch')
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.dataset-', dir=parent) as temporary:
        stage = Path(temporary)/'content'
        stage.mkdir()
        with zipfile.ZipFile(archive) as z:
            for name in z.namelist():
                if Path(name).is_absolute() or '..' in Path(name).parts or '\\' in name:
                    raise RuntimeError('Unsafe bundled dataset path')
            z.extractall(stage)
        manifest = json.loads((stage/'manifest.json').read_text(encoding='utf-8'))
        if manifest['dataset_sha256'] != model['dataset_sha256']:
            raise RuntimeError('Model and dataset hashes differ')
        # Ultralytics resolves a relative path against its own datasets directory.
        # Rebuild only this machine-specific configuration, never original labels.
        yaml = 'path: '+json.dumps(str(target.resolve()))+'\ntrain: images/train\nval: images/val\ntest: images/test\nnames: '+json.dumps(manifest['classes'])+'\n'
        (stage/'data.yaml').write_text(yaml, encoding='utf-8')
        stage.rename(target)
    return target

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
    install_dataset()
    print('Example-photo model ready:',model['id'])

if __name__ == '__main__':
    install()

