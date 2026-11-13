#!/usr/bin/env python3
"""Real isolated train/evaluate worker. No simulator truth is used by deployed inference."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import signal
import sys
import tempfile
import time

OFFICIAL_STARTING_MODELS = ('yolo26n.pt', 'yolo26s.pt')


def resolve_model_source(selection, weights_dir, expected_hash=None):
    if selection in OFFICIAL_STARTING_MODELS:
        model_path = Path(weights_dir) / selection
        model_path.parent.mkdir(parents=True, exist_ok=True)
    else:
        model_path = Path(selection).expanduser()
        if not model_path.is_file():
            raise FileNotFoundError('Select an existing registered model or supported official starting model')
    if expected_hash:
        if not model_path.is_file() or hashlib.sha256(model_path.read_bytes()).hexdigest() != expected_hash:
            raise ValueError('Starting model hash changed before loading')
    return model_path


def atomic_json(path,data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    fd,temp=tempfile.mkstemp(prefix='.'+path.name,dir=path.parent)
    try:
        with os.fdopen(fd,'w') as stream:json.dump(data,stream,ensure_ascii=False,indent=2,allow_nan=False)
        os.replace(temp,path)
    finally:
        if os.path.exists(temp):os.unlink(temp)


def class_schema_hash(classes):
    return hashlib.sha256(json.dumps(list(classes),ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def clean_metrics(values):
    result={}
    for key,value in values.items():
        try:v=float(value)
        except (TypeError,ValueError):continue
        result[str(key)]=v if math.isfinite(v) else None
    return result


def split_images(root,value):
    values=value if isinstance(value,list) else [value]
    images=[]
    for item in values:
        if not item:continue
        path=Path(item);path=path if path.is_absolute() else root/path
        if path.is_dir():images.extend(p for p in path.rglob('*') if p.suffix.lower() in ('.jpg','.jpeg','.png','.webp'))
        elif path.suffix=='.txt' and path.is_file():
            for line in path.read_text().splitlines():
                p=Path(line.strip());images.append(p if p.is_absolute() else root/p)
        elif path.is_file():images.append(path)
        else:raise ValueError('Missing image split path: '+str(path))
    if not images:raise ValueError('An empty or missing split cannot be evaluated')
    return sorted(set(p.resolve() for p in images))


def dataset_audit(data,cfg):
    root=Path(cfg.get('path',data.parent));root=root if root.is_absolute() else data.parent/root
    names=cfg['names'];classes=[names[k] for k in sorted(names,key=int)] if isinstance(names,dict) else list(names)
    splits={name:split_images(root,cfg.get(name)) for name in ('train','val','test')}
    counts={name:[0]*len(classes) for name in splits};seen={};digest=hashlib.sha256()
    for name,files in splits.items():
        for image in files:
            raw=image.read_bytes();sha=hashlib.sha256(raw).hexdigest()
            if sha in seen and seen[sha]!=name:raise ValueError('Image bytes overlap '+seen[sha]+' and '+name+' splits')
            seen[sha]=name
            parts=list(image.parts)
            if 'images' not in parts:raise ValueError('YOLO dataset requires images/ and labels/ sibling paths')
            parts[parts.index('images')]='labels';label=Path(*parts).with_suffix('.txt')
            if not label.is_file():raise ValueError('Missing explicit label file (use empty file for negative frames): '+str(label))
            labels=label.read_text()
            for line in labels.splitlines():
                row=line.split()
                if len(row)!=5:raise ValueError('Expected YOLO box row')
                cls=int(row[0]);box=list(map(float,row[1:]));x,y,w,h=box
                if not 0<=cls<len(classes) or not all(math.isfinite(v) for v in box) or min(w,h)<=0:
                    raise ValueError('Invalid class or box')
                if min(x-w/2,y-h/2)<-1e-5 or max(x+w/2,y+h/2)>1.00001:raise ValueError('Box outside image')
                counts[name][cls]+=1
            digest.update(name.encode()+sha.encode()+hashlib.sha256(labels.encode()).digest())
    return classes,splits,counts,digest.hexdigest()


def per_class_metrics(metrics,classes,counts):
    box=metrics.box;indices=list(getattr(box,'ap_class_index',[]));result=[]
    for class_id,name in enumerate(classes):
        entry={'class_id':class_id,'name':name,'instances':counts[class_id],
               'precision':None,'recall':None,'map50':None,'map50_95':None}
        if class_id in indices:
            i=indices.index(class_id)
            for key,attr in [('precision','p'),('recall','r'),('map50','ap50'),('map50_95','ap')]:
                value=float(getattr(box,attr)[i]);entry[key]=value if math.isfinite(value) else None
        result.append(entry)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kind',choices=['train','evaluate'],required=True)
    parser.add_argument('--dataset',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--model',default='yolo26n.pt');parser.add_argument('--epochs',type=int,default=20)
    parser.add_argument('--expected-source-sha256',default=None)
    parser.add_argument('--imgsz',type=int,default=640);parser.add_argument('--batch',type=int,default=8)
    parser.add_argument('--device',choices=['cpu','mps'],default='mps')
    args=parser.parse_args();out=args.output.resolve();out.mkdir(parents=True,exist_ok=True)
    started=time.time();progress=out/'progress.json'
    def update(stage,completed=0,total=1,unit='steps',**extra):
        atomic_json(progress,dict(status='running',stage=stage,completed=completed,total=total,unit=unit,updated_at=time.time(),**extra))
    def cancel(*_):
        atomic_json(progress,{'status':'cancelled','stage':'cancelled','updated_at':time.time()});raise SystemExit(130)
    signal.signal(signal.SIGTERM,cancel);signal.signal(signal.SIGINT,cancel)
    os.environ.setdefault('YOLO_CONFIG_DIR',str(Path(__file__).resolve().parent/'.ultralytics'))
    os.environ.setdefault('YOLO_AUTOINSTALL','false')
    try:
        update('initializing')
        import torch,ultralytics
        from ultralytics.utils import YAML
        if args.device=='mps' and not torch.backends.mps.is_available():raise RuntimeError('MPS requested but unavailable; choose cpu explicitly')
        data=args.dataset.resolve();cfg=YAML.load(str(data))
        classes,splits,counts,content_hash=dataset_audit(data,cfg)
        manifest_file=data.parent/'manifest.json'
        manifest=json.loads(manifest_file.read_text()) if manifest_file.is_file() else {}
        data_hash=content_hash
        if manifest.get('dataset_sha256'):
            provenance=dict(classes=manifest['classes'],revision=manifest['revision'],frames=[{**{k:f[k] for k in ('id','episode','split','image_sha256','label_sha256','yolo_label_sha256')},**({'source_group':f.get('source_group')} if manifest.get('provenance_schema')==2 else {})} for f in manifest['frames']])
            computed=hashlib.sha256(json.dumps(provenance,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()
            if computed!=manifest['dataset_sha256']:raise ValueError('Frozen dataset manifest hash mismatch')
            for frame in manifest['frames']:
                image=data.parent/'images'/frame['split']/(frame['id']+'.jpg')
                label=data.parent/'labels'/frame['split']/(frame['id']+'.txt')
                if hashlib.sha256(image.read_bytes()).hexdigest()!=frame['image_sha256'] or hashlib.sha256(label.read_bytes()).hexdigest()!=frame['yolo_label_sha256']:
                    raise ValueError('Frozen dataset content hash mismatch')
            data_hash=computed
        model_path=resolve_model_source(args.model,Path(__file__).resolve().parent/'weights',args.expected_source_sha256)
        update('loading_model')
        model=ultralytics.YOLO(str(model_path))  # Known official weight name uses Ultralytics official download handler.
        source_hash=hashlib.sha256(model_path.read_bytes()).hexdigest()
        if args.kind=='train':
            def epoch_callback(trainer):
                losses=trainer.label_loss_items(trainer.tloss,prefix='train') if trainer.tloss is not None else {}
                update('training',int(trainer.epoch)+1,int(trainer.epochs),'epochs',epoch=int(trainer.epoch)+1,
                       epochs=int(trainer.epochs),loss=clean_metrics(losses),metrics=clean_metrics(trainer.metrics or {}))
            model.add_callback('on_fit_epoch_end',epoch_callback)
            model.train(data=str(data),epochs=args.epochs,imgsz=args.imgsz,batch=args.batch,device=args.device,
                workers=0,seed=42,deterministic=True,project=str(out),name='fit',exist_ok=False,
                optimizer='AdamW',lr0=.001,patience=max(10,args.epochs),plots=True,save=True,verbose=False,resume=False)
            weights=Path(model.trainer.best).resolve()
        else:weights=model_path.resolve()
        if not weights.is_file() or weights.stat().st_size==0:raise RuntimeError('No usable trained weights exist')
        # Reload the exact saved checkpoint, not the in-memory trainer object.
        model=ultralytics.YOLO(str(weights));metrics={};per_class={}
        if [model.names[k] for k in sorted(model.names)]!=classes:raise ValueError('Saved model classes differ from dataset classes')
        for i,(split,key) in enumerate([('val','validation'),('test','test')]):
            update('evaluating_'+split,i,2,'splits')
            evaluation=model.val(data=str(data),split=split,imgsz=args.imgsz,batch=args.batch,device=args.device,
                workers=0,plots=True,project=str(out),name='evaluate-'+split,verbose=False)
            metrics[key]=clean_metrics(evaluation.results_dict)
            per_class[key]=per_class_metrics(evaluation,classes,counts[split])
        update('rendering_held_out_gallery')
        gallery=out/'held-out';gallery.mkdir(exist_ok=True);samples=[]
        from PIL import Image
        for i,path in enumerate(splits['test'][:12]):
            result=model.predict(Image.open(path).convert('RGB'),imgsz=args.imgsz,device=args.device,verbose=False)[0]
            predicted=gallery/f'{i:03d}-pred.jpg';original=gallery/f'{i:03d}-image.jpg'
            result.save(filename=str(predicted));Image.open(path).convert('RGB').save(original,quality=90)
            samples.append({'split':'test','image':str(original.relative_to(out)),
                'prediction':str(predicted.relative_to(out)),'source_name':path.name,'detections':len(result.boxes)})
        score=metrics['test'].get('metrics/mAP50(B)') or 0.
        result={'status':'completed','kind':args.kind,'weights_path':str(weights),
            'weights_sha256':hashlib.sha256(weights.read_bytes()).hexdigest(),'classes':classes,
            'class_schema_sha256':class_schema_hash(classes),'metrics':metrics,'per_class':per_class,
            'class_counts':counts,'dataset_sha256':data_hash,'dataset_content_sha256':content_hash,'dataset_path':str(data),'source_weights_sha256':source_hash,
            'plots':[str(p.relative_to(out)) for p in out.rglob('*.png')],'samples':samples,
            'runtime':{'python':platform.python_version(),'platform':platform.platform(),'torch':torch.__version__,
                       'ultralytics':ultralytics.__version__,'device':args.device},
            'settings':{'epochs':args.epochs if args.kind=='train' else None,'imgsz':args.imgsz,'batch':args.batch},
            'elapsed_seconds':time.time()-started,'quality':'zero_test_map' if score==0 else 'synthetic_test_only',
            'physical_robot_validated':False,'warning':'Held-out rendered scenes test simulation generalization only; provided example photos may differ at competition.'}
        atomic_json(out/'result.json',result)
        atomic_json(progress,{'status':'completed','stage':'completed','completed':1,'total':1,'unit':'jobs','updated_at':time.time()})
        print(json.dumps({'status':'completed','weights_sha256':result['weights_sha256'],'metrics':metrics,'elapsed_seconds':result['elapsed_seconds']},ensure_ascii=False),flush=True)
    except Exception as exc:
        atomic_json(progress,{'status':'failed','stage':'failed','error':str(exc),'updated_at':time.time()})
        atomic_json(out/'result.json',{'status':'failed','error':str(exc),'elapsed_seconds':time.time()-started})
        raise

if __name__=='__main__':main()
