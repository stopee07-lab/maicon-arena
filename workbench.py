"""Single-user local ML workbench. Real processes and immutable, hash-linked artifacts.

HTTP integration authenticates X-Admin-Key before calling handle(). No Torch import
occurs in the server process. Learned controllers receive only their own credential.
"""
from __future__ import annotations
import base64
import copy
import hashlib
import io
import json
import math
import mimetypes
import os
from pathlib import Path
import queue
import random
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from collections import Counter, defaultdict
from PIL import Image
from development import CodeStore, DevelopmentError

JSON_TYPE='application/json; charset=utf-8'
TERMINAL={'succeeded','failed','cancelled','interrupted'}
DEFAULT_CLASSES=['military_truck','mortar','tank','missile','ammo_box','drum']
SAFE_ID=re.compile(r'^[A-Za-z0-9_-]{1,96}$')
TRAINING_BASES={'yolo26n.pt':'YOLO26n · 빠른 실험용', 'yolo26s.pt':'YOLO26s · 더 큰 모델'}


class WorkbenchError(Exception):
    def __init__(self,status,message):self.status=status;super().__init__(message)


def digest(data):return hashlib.sha256(data).hexdigest()
def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()
def now():return time.time()
def uid(prefix):return f'{prefix}-{time.strftime("%Y%m%d-%H%M%S")}-{uuid.uuid4().hex[:8]}'


def atomic_json(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(path.name+'.'+uuid.uuid4().hex[:8]+'.tmp')
    temporary.write_bytes(canonical(value)+b'\n');os.replace(temporary,path)


def read_json(path,default=None):
    try:return json.loads(Path(path).read_text())
    except (OSError,ValueError):return default


def clean_json(value):
    if isinstance(value,float):return value if math.isfinite(value) else None
    if isinstance(value,dict):return {str(k):clean_json(v) for k,v in value.items()}
    if isinstance(value,list):return [clean_json(x) for x in value]
    return value


def integer(settings,key,default,low,high):
    value=settings.get(key,default)
    if isinstance(value,bool) or not isinstance(value,int) or not low<=value<=high:raise WorkbenchError(422,f'{key} must be an integer from {low} to {high}')
    return value


def valid_classes(classes):
    if not isinstance(classes,list) or not 1<=len(classes)<=128 or any(not isinstance(x,str) or not x.strip() or len(x)>80 for x in classes) or len(set(classes))!=len(classes):raise WorkbenchError(422,'classes must contain 1–128 distinct names')
    return list(classes)


def valid_labels(labels,classes):
    if not isinstance(labels,list) or len(labels)>1000:raise WorkbenchError(422,'labels must be a list of at most 1000 boxes')
    result=[]
    for label in labels:
        if not isinstance(label,dict):raise WorkbenchError(422,'Each label must contain class_id and bbox')
        cid=label.get('class_id');box=label.get('bbox')
        if isinstance(cid,bool) or not isinstance(cid,int) or not 0<=cid<len(classes):raise WorkbenchError(422,'class_id is outside the dataset class schema')
        if not isinstance(box,list) or len(box)!=4 or any(isinstance(x,bool) or not isinstance(x,(float,int)) or not math.isfinite(x) for x in box):raise WorkbenchError(422,'bbox must contain four finite normalized numbers')
        x,y,w,h=box
        if not (w>0 and h>0 and x-w/2>=-1e-6 and y-h/2>=-1e-6 and x+w/2<=1.000001 and y+h/2<=1.000001):raise WorkbenchError(422,'bbox extends beyond the image')
        result.append({'class_id':cid,'bbox':[float(x),float(y),float(w),float(h)]})
    return result


def decode_image(data,filename='frame.jpg'):
    if Path(filename).suffix.lower() not in ('.jpg','.jpeg','.png'):raise WorkbenchError(422,'Only JPEG and PNG images are accepted')
    try:
        raw=base64.b64decode(data,validate=True) if isinstance(data,str) else bytes(data)
        if not raw or len(raw)>12*1024*1024:raise ValueError('image byte limit')
        image=Image.open(io.BytesIO(raw))
        if not 32<=image.width<=4096 or not 32<=image.height<=4096 or image.width*image.height>16_000_000:raise ValueError('image dimensions')
        image.load();width,height=image.size
        if image.format!='JPEG':
            out=io.BytesIO();image.convert('RGB').save(out,'JPEG',quality=95);raw=out.getvalue()
        return raw,width,height
    except Exception as exc:raise WorkbenchError(422,'Image must decode as a complete JPEG/PNG, 32–4096 pixels per side') from exc


class Workbench:
    def __init__(self,root,capture_frame,runtime_status,*,python_path=None,probe=True):
        self.root=Path(root).resolve();self.store=self.root/'data'/'workbench'
        self.code_store=CodeStore(self.root)
        self.capture_frame=capture_frame;self.runtime_status=runtime_status
        self.python=Path(python_path) if python_path else self.root/'training'/'.venv'/'bin'/'python'
        self.lock=threading.RLock();self.stopping=threading.Event();self.pending=queue.Queue()
        self.cancel_events={};self.processes={};self.jobs={};self.controller_process=None;self.controller_directory=None
        self.capabilities={'python':str(self.python),'torch':None,'ultralytics':None,'mps':None,'cpu':self.python.is_file(),'physics':None,'status':'checking' if probe else 'not_probed'}
        for name in ('jobs','datasets','models','deployments','controllers','runtime'):(self.store/name).mkdir(parents=True,exist_ok=True)
        for p in sorted((self.store/'jobs').glob('*/job.json')):
            job=read_json(p)
            if not job:continue
            if job.get('status') not in TERMINAL:
                job.update(status='interrupted',error='Server restarted before completion',finished_at=now());atomic_json(p,job)
            self.jobs[job['id']]=job
        self.deployment=read_json(self.store/'deployment.json')
        previous=read_json(self.store/'controller.json')
        if previous and previous.get('output_dir'):self.controller_directory=Path(previous['output_dir'])
        self.controller_recovery=self._recover_controllers()
        self.worker=threading.Thread(target=self._job_loop,name='workbench-jobs',daemon=True);self.worker.start()
        if probe:threading.Thread(target=self._probe,name='workbench-capabilities',daemon=True).start()

    def _environment(self):
        env={k:v for k,v in os.environ.items() if not any(s in k.upper() for s in ('ADMIN_KEY','VIEWER_KEY','MISSION_CODE'))}
        from fidelity_settings import environment
        env.update(environment(self.root))
        env.update(YOLO_CONFIG_DIR=str(self.store/'runtime'/'ultralytics'),YOLO_AUTOINSTALL='false',PYTHONUNBUFFERED='1',OMP_NUM_THREADS='2',MKL_NUM_THREADS='2')
        return env

    def _probe(self):
        code="import importlib.metadata,json,torch;print(json.dumps(dict(torch=importlib.metadata.version('torch'),ultralytics=importlib.metadata.version('ultralytics'),mps=torch.backends.mps.is_available(),cpu=True)))"
        try:
            result=subprocess.run([str(self.python),'-c',code],capture_output=True,text=True,timeout=25,env=self._environment())
            if result.returncode:raise RuntimeError(result.stderr[-1500:])
            record=json.loads(result.stdout.strip().splitlines()[-1])
            with self.lock:self.capabilities.update(record,status='available')
        except Exception as exc:
            with self.lock:self.capabilities.update(status='unavailable',error=str(exc)[:1000])

    def _runtime(self):
        try:result=self.runtime_status();return result if isinstance(result,dict) else {'physics':result}
        except Exception as exc:return {'physics':{'connected':False,'error':str(exc)}}

    def _id_path(self,category,identifier):
        if not isinstance(identifier,str) or not SAFE_ID.fullmatch(identifier):raise WorkbenchError(404,'Unknown artifact')
        return self.store/category/identifier

    def _dataset(self,identifier):
        path=self._id_path('datasets',identifier);data=read_json(path/'manifest.json')
        if not data:raise WorkbenchError(404,'Dataset not found')
        return path,data

    def _model(self,identifier):
        path=self._id_path('models',identifier);data=read_json(path/'model.json')
        if not data:raise WorkbenchError(404,'Model not found')
        return path,data

    def _registered_training_source(self,identifier,classes=None,expected_hash=None):
        path,model=self._model(identifier);weights=(path/'weights.pt').resolve()
        if not weights.is_relative_to(path.resolve()) or not weights.is_file():raise WorkbenchError(409,'Registered weights must exist inside the model directory')
        actual=digest(weights.read_bytes())
        if actual!=model.get('weights_sha256') or (expected_hash and actual!=expected_hash):raise WorkbenchError(409,'Registered model hash mismatch; training/evaluation refused')
        if classes is not None and model.get('classes')!=classes:raise WorkbenchError(409,'Previous model class names/order differ from this dataset; select a pretrained starting model')
        return weights,dict(kind='registered',model_id=identifier,name=model.get('name',identifier),weights_sha256=actual)

    def _training_source(self,selection,dataset):
        if isinstance(selection,str) and selection in TRAINING_BASES:
            return dict(kind='pretrained',name=selection)
        if isinstance(selection,str) and selection.startswith('model:'):
            return self._registered_training_source(selection[6:],dataset['classes'])[1]
        raise WorkbenchError(422,'Select yolo26n.pt, yolo26s.pt, or an existing registered model')

    def _save_job(self,job):atomic_json(self.store/'jobs'/job['id']/'job.json',job)

    def _redact(self,text):
        text=re.sub(r'(mission_code[= :]+)[^&\s\"\']+',r'\1<redacted>',text,flags=re.I)
        access=read_json(self.root/'data'/'access.json',{})
        for team in access.get('teams',[]):text=text.replace(team.get('mission_code','\0'),'<redacted>')
        return text

    def _logs(self,path,lines=100):
        try:
            with Path(path).open('rb') as f:
                f.seek(0,2);size=f.tell();f.seek(max(0,size-32000));text=f.read().decode('utf-8','replace')
            return [self._redact(x)[:1500] for x in text.splitlines()[-lines:]]
        except OSError:return []

    def _job_snapshot(self,job):
        data=copy.deepcopy(job);directory=self.store/'jobs'/job['id']
        progress=read_json(directory/'progress.json')
        if progress and data['status']!='interrupted':data['progress'].update(clean_json(progress))
        data['logs']=self._logs(directory/'worker.log');return data

    def status(self):
        with self.lock:
            runtime=self._runtime();capabilities=copy.deepcopy(self.capabilities);capabilities['physics']=runtime.get('physics')
            datasets=[]
            for path in sorted((self.store/'datasets').glob('*/manifest.json'),reverse=True):
                manifest=read_json(path)
                if manifest:datasets.append({k:v for k,v in manifest.items() if k!='frames'})
            models=[read_json(p) for p in sorted((self.store/'models').glob('*/model.json'),reverse=True)]
            sources=[dict(id=name,name=label,kind='pretrained',cached=(self.root/'training'/'weights'/name).is_file()) for name,label in TRAINING_BASES.items()]
            sources.extend(dict(id='model:'+m['id'],name=m.get('name',m['id']),kind='registered',model_id=m['id'],classes=m.get('classes'),created_at=m.get('created_at')) for m in models if m)
            return dict(capabilities=capabilities,datasets=datasets,models=[m for m in models if m],training_sources=sources,jobs=[self._job_snapshot(j) for j in list(self.jobs.values())[-30:][::-1]],deployment=copy.deepcopy(self.deployment),controller=self.controller_snapshot(),physics=runtime.get('physics'))

    def _new_dataset(self,name,source):
        identifier=uid('dataset');path=self.store/'datasets'/identifier;(path/'raw').mkdir(parents=True)
        manifest=dict(id=identifier,name=str(name or identifier)[:120],status='collecting',created_at=now(),source=source,frozen=False,revision=1,count=0,classes=[],frames=[],synthetic=None)
        atomic_json(path/'manifest.json',manifest);return path,manifest

    def _frame_record(self,path,manifest,raw,width,height,labels,episode,**extra):
        fid=f'frame-{len(manifest["frames"]):06d}';image_path=path/'raw'/f'{fid}.jpg';image_path.write_bytes(raw)
        record=dict(id=fid,split=None,episode=str(episode)[:160],labels=labels,width=width,height=height,image_sha256=digest(raw),label_sha256=digest(canonical(labels)),image_url=f'/api/workbench/datasets/{manifest["id"]}/frames/{fid}.jpg',**extra)
        manifest['frames'].append(record);manifest['count']=len(manifest['frames']);return record

    def _dataset_counts(self,manifest):
        counts=Counter(label['class_id'] for f in manifest['frames'] for label in f['labels'])
        manifest['class_counts']={name:counts[i] for i,name in enumerate(manifest['classes'])}
        manifest['positive_frames']=sum(bool(f['labels']) for f in manifest['frames'])
        manifest['episodes']=sorted({f['episode'] for f in manifest['frames']})
        manifest['split_counts']=dict(Counter(f.get('split') or 'unsplit' for f in manifest['frames']))

    def _verify_dataset(self,path,manifest):
        if not manifest.get('frozen'):raise WorkbenchError(409,'Dataset is not frozen')
        for f in manifest['frames']:
            image=path/'images'/f['split']/(f['id']+'.jpg')
            label=path/'labels'/f['split']/(f['id']+'.txt')
            if not image.is_file() or digest(image.read_bytes())!=f['image_sha256']:raise WorkbenchError(409,'Frozen image integrity check failed')
            if not label.is_file() or digest(label.read_bytes())!=f['yolo_label_sha256']:raise WorkbenchError(409,'Frozen annotation integrity check failed')
        provenance=dict(classes=manifest['classes'],revision=manifest['revision'],frames=[{**{k:f[k] for k in ('id','episode','split','image_sha256','label_sha256','yolo_label_sha256')},**({'source_group':f.get('source_group')} if manifest.get('provenance_schema')==2 else {})} for f in manifest['frames']])
        if digest(canonical(provenance))!=manifest.get('dataset_sha256'):raise WorkbenchError(409,'Frozen manifest integrity check failed')

    def create_job(self,body):
        kind=body.get('kind');settings=body.get('settings') or {}
        if kind not in ('collect','train','evaluate') or not isinstance(settings,dict):raise WorkbenchError(422,'kind must be collect/train/evaluate and settings an object')
        dataset_id=body.get('dataset_id');model_id=body.get('model_id')
        if kind=='collect':
            normalized=dict(count=integer(settings,'count',240,6,5000),seed=integer(settings,'seed',42,0,2**31-1),slot=settings.get('slot','B-1'),name=str(settings.get('name','Physical camera dataset'))[:120])
            if normalized['slot'] not in ('B-1','B-2','B-3','R-1','R-2','R-3'):raise WorkbenchError(422,'Unknown robot slot')
            if shutil.disk_usage(self.store).free<256*1024*1024:raise WorkbenchError(507,'At least 256 MiB free storage is required')
        else:
            if kind=='evaluate':
                _,model=self._model(model_id);dataset_id=dataset_id or model['dataset_id']
            dataset_path,dataset=self._dataset(dataset_id)
            if not dataset['frozen']:raise WorkbenchError(409,'Freeze the dataset before training or evaluation')
            self._verify_dataset(dataset_path,dataset)
            normalized=dict(epochs=integer(settings,'epochs',20,1,300),imgsz=integer(settings,'imgsz',640,96,1280),batch=integer(settings,'batch',4,1,32),device=settings.get('device','mps' if self.capabilities.get('mps') else 'cpu'))
            if normalized['imgsz']%32:raise WorkbenchError(422,'imgsz must be divisible by 32')
            if normalized['device'] not in ('cpu','mps'):raise WorkbenchError(422,'device must be cpu or mps')
            if normalized['device']=='mps' and self.capabilities.get('mps') is False:raise WorkbenchError(409,'MPS is unavailable in the selected Python runtime')
            model=settings.get('model','yolo26n.pt') if kind=='train' else 'model:'+model_id
            normalized['model']=model;normalized['source']=self._training_source(model,dataset)
        with self.lock:
            if sum(j['status'] not in TERMINAL for j in self.jobs.values())>=8:raise WorkbenchError(429,'At most eight queued/active workbench jobs')
            identifier=uid('job');directory=self.store/'jobs'/identifier;directory.mkdir()
            job=dict(id=identifier,kind=kind,status='queued',created_at=now(),dataset_id=dataset_id,model_id=model_id,settings=normalized,progress=dict(completed=0,total=normalized.get('count',normalized.get('epochs',0)),unit='images' if kind=='collect' else 'epochs'),result=None,error=None)
            self.jobs[identifier]=job;self.cancel_events[identifier]=threading.Event();self._save_job(job);self.pending.put(identifier)
            return self._job_snapshot(job)

    def _job_loop(self):
        while not self.stopping.is_set():
            try:identifier=self.pending.get(timeout=.2)
            except queue.Empty:continue
            with self.lock:
                job=self.jobs[identifier];cancel=self.cancel_events[identifier]
                if cancel.is_set():job.update(status='cancelled',finished_at=now());self._save_job(job);continue
                job.update(status='running',started_at=now());self._save_job(job)
            try:
                result=self._collect(job,cancel) if job['kind']=='collect' else self._training(job,cancel)
                with self.lock:
                    if cancel.is_set():job.update(status='cancelled',finished_at=now(),error='Cancelled by user')
                    else:job.update(status='succeeded',result=result,finished_at=now())
                    self._save_job(job)
            except Exception as exc:
                with self.lock:
                    job.update(status='cancelled' if cancel.is_set() else 'failed',error=self._redact(str(exc))[:3000],finished_at=now());self._save_job(job)
            finally:
                with self.lock:self.processes.pop(identifier,None)
                self.pending.task_done()

    def _collect(self,job,cancel):
        settings=job['settings'];path,manifest=self._new_dataset(settings['name'],'physical_camera_capture')
        manifest['collection_job_id']=job['id'];manifest['seed']=settings['seed'];manifest['slot']=settings['slot'];seen=set();byte_count=0
        with self.lock:job['dataset_id']=manifest['id'];self._save_job(job)
        try:
            for i in range(settings['count']):
                if cancel.is_set() or self.stopping.is_set():raise RuntimeError('Collection cancelled')
                captured=self.capture_frame(settings['seed']+i,slot=settings['slot'])
                if not isinstance(captured,dict):raise RuntimeError('Capture callback did not return a frame')
                provider=str(captured.get('backend') or captured.get('renderer') or '')
                physical=captured.get('physical')
                if physical is False or (physical is not True and not any(x in provider.lower() for x in ('webots','mujoco'))):raise RuntimeError('A native physical camera is required; fallback renderer frames were rejected')
                classes=valid_classes(captured.get('classes'))
                if manifest['classes'] and classes!=manifest['classes']:raise RuntimeError('Capture class schema changed during collection')
                manifest['classes']=classes;manifest['synthetic']=bool(captured.get('synthetic',True))
                raw,width,height=decode_image(captured.get('image_base64',captured.get('image_bytes',b'')))
                if (captured.get('width',width),captured.get('height',height))!=(width,height):raise RuntimeError('Capture dimensions do not match decoded JPEG')
                image_hash=digest(raw)
                if image_hash in seen:raise RuntimeError('Duplicate captured image; change scene/pose before collecting another frame')
                seen.add(image_hash);byte_count+=len(raw)
                if byte_count>2*1024**3 or shutil.disk_usage(self.store).free<128*1024**2:raise RuntimeError('Collection storage limit reached; partial dataset preserved')
                labels=valid_labels(captured.get('annotations',captured.get('labels',[])),classes)
                episode=captured.get('episode')
                if not isinstance(episode,str) or not episode:raise RuntimeError('Capture must provide the physical episode/group ID')
                self._frame_record(path,manifest,raw,width,height,labels,episode,seed=settings['seed']+i,backend=provider,renderer=str(captured.get('renderer',provider)),physical=True,captured_at=captured.get('captured_at',now()),pose=captured.get('pose'),source_group=captured.get('source_group','official-six-example-photos'))
                self._dataset_counts(manifest);atomic_json(path/'manifest.json',manifest)
                with self.lock:job['progress']=dict(completed=i+1,total=settings['count'],unit='images');self._save_job(job)
            manifest['status']='complete';manifest['completed_at']=now();self._dataset_counts(manifest);atomic_json(path/'manifest.json',manifest)
            return dict(dataset_id=manifest['id'],count=manifest['count'],positive_frames=manifest['positive_frames'])
        except Exception:
            manifest['status']='cancelled' if cancel.is_set() else 'failed';atomic_json(path/'manifest.json',manifest);raise

    def import_dataset(self,body):
        files=body.get('files');classes=valid_classes(body.get('classes',DEFAULT_CLASSES))
        if not isinstance(files,list) or not 1<=len(files)<=5000:raise WorkbenchError(422,'files must contain 1–5000 images')
        if len(canonical(body))>32*1024*1024:raise WorkbenchError(413,'Import request exceeds 32 MiB')
        checked=[];seen=set()
        for f in files:
            if not isinstance(f,dict):raise WorkbenchError(422,'Invalid file entry')
            raw,w,h=decode_image(f.get('image_base64',''),f.get('name',''))
            image_hash=digest(raw)
            if image_hash in seen:raise WorkbenchError(422,'Duplicate image in import')
            seen.add(image_hash);labels=valid_labels(f.get('labels',[]),classes)
            episode=f.get('episode','import-session')
            if not isinstance(episode,str) or not episode:raise WorkbenchError(422,'episode must name a real capture session/group')
            source_group=f.get('source_group')
            if source_group is not None and (not isinstance(source_group,str) or not source_group.strip() or len(source_group)>200):raise WorkbenchError(422,'source_group must be a nonempty source image ID of at most 200 characters')
            checked.append((raw,w,h,labels,episode,str(f.get('name',''))[:200],source_group))
        path,manifest=self._new_dataset(body.get('name','Imported images'),'user_import');manifest['classes']=classes;manifest['synthetic']=body.get('synthetic',False)
        for raw,w,h,labels,episode,name,source_group in checked:self._frame_record(path,manifest,raw,w,h,labels,episode,original_name=name,source_group=source_group)
        manifest['status']='complete';self._dataset_counts(manifest);atomic_json(path/'manifest.json',manifest);return manifest

    def labels(self,identifier,body):
        with self.lock:
            path,manifest=self._dataset(identifier)
            if manifest['frozen']:raise WorkbenchError(409,'Frozen datasets are immutable; collect or import a new version')
            if manifest['status']!='complete':raise WorkbenchError(409,'Dataset collection is not complete')
            record=next((f for f in manifest['frames'] if f['id']==body.get('frame_id')),None)
            if not record:raise WorkbenchError(404,'Frame not found')
            record['labels']=valid_labels(body.get('labels'),manifest['classes']);record['label_sha256']=digest(canonical(record['labels']))
            manifest['revision']+=1;manifest['modified_at']=now();self._dataset_counts(manifest);atomic_json(path/'manifest.json',manifest);return manifest

    def clone_dataset(self,identifier,body):
        with self.lock:
            source,original=self._dataset(identifier)
            if original['status']!='complete':raise WorkbenchError(409,'Only complete datasets can be copied for editing')
            if original.get('frozen'):self._verify_dataset(source,original)
            total=sum((source/'raw'/(frame['id']+'.jpg')).stat().st_size for frame in original['frames'])
            if shutil.disk_usage(self.store).free<total+128*1024**2:raise WorkbenchError(507,'Not enough free storage for a separate editable dataset')
            path,manifest=self._new_dataset(body.get('name') or original['name']+' · 수정본',original['source'])
            manifest.update(classes=list(original['classes']),synthetic=original.get('synthetic'),seed=original.get('seed',42),parent_dataset_id=identifier,parent_dataset_sha256=original.get('dataset_sha256'))
            try:
                for frame in original['frames']:
                    raw=(source/'raw'/(frame['id']+'.jpg')).read_bytes()
                    if digest(raw)!=frame['image_sha256']:raise WorkbenchError(409,'Source image hash changed; editable copy refused')
                    extras={k:copy.deepcopy(v) for k,v in frame.items() if k not in ('id','split','episode','labels','width','height','image_sha256','label_sha256','image_url','yolo_label_sha256')}
                    self._frame_record(path,manifest,raw,frame['width'],frame['height'],copy.deepcopy(frame['labels']),frame['episode'],**extras)
                manifest['status']='complete';self._dataset_counts(manifest);atomic_json(path/'manifest.json',manifest)
                return manifest
            except Exception:
                # This newly created copy is not yet published; preserve the source untouched.
                shutil.rmtree(path)
                raise

    def freeze(self,identifier,independent=False):
        with self.lock:
            path,manifest=self._dataset(identifier)
            if manifest['frozen']:
                if independent and not manifest.get('source_audit',{}).get('independent_source_test'):raise WorkbenchError(409,'이 자료는 독립 원본 평가로 고정되지 않았습니다. 수정본을 만든 뒤 원본 그룹으로 다시 분할하세요.')
                return manifest
            if manifest['status']!='complete':raise WorkbenchError(409,'Only complete datasets can be frozen')
            if independent and any(not f.get('source_group') for f in manifest['frames']):raise WorkbenchError(422,'독립 사진 평가에는 모든 사진의 source_group 원본 ID가 필요합니다. provenance.json을 함께 가져오세요.')
            group=lambda f:f['source_group'] if independent else f['episode']
            groups=sorted({group(f) for f in manifest['frames']})
            if len(groups)<3:raise WorkbenchError(422,'At least three independent capture episodes/groups are required for train/validation/test; adjacent frames will not be split across sets')
            # All appearances of one image hash must remain in one group, never leak across splits.
            hashes={}
            for f in manifest['frames']:
                old=hashes.setdefault(f['image_sha256'],group(f))
                if old!=group(f):raise WorkbenchError(422,'Identical image occurs in different episodes')
            grouped=defaultdict(set)
            for f in manifest['frames']:grouped[group(f)].update(x['class_id'] for x in f['labels'])
            ntest=max(1,round(len(groups)*.15));nval=max(1,round(len(groups)*.15))
            nval=min(nval,len(groups)-ntest-1);rng=random.Random(manifest.get('seed',42));best=None;best_missing=10**6
            expected=set(range(len(manifest['classes'])))
            for _ in range(500):
                candidate=list(groups);rng.shuffle(candidate);assignment={g:('test' if i<ntest else 'val' if i<ntest+nval else 'train') for i,g in enumerate(candidate)}
                present={s:set().union(*(grouped[g] for g in groups if assignment[g]==s)) for s in ('train','val','test')}
                missing=sum(len(expected-present[s]) for s in present)
                if missing<best_missing:best=assignment;best_missing=missing
                if not missing:break
            if best_missing:
                present={s:set().union(*(grouped[g] for g in groups if best[g]==s)) for s in ('train','val','test')}
                missing={s:[manifest['classes'][i] for i in sorted(expected-present[s])] for s in present if expected-present[s]}
                raise WorkbenchError(422,'Capture more independent episodes: each split needs every class. Missing '+json.dumps(missing,ensure_ascii=False))
            for split in ('train','val','test'):(path/'images'/split).mkdir(parents=True,exist_ok=True);(path/'labels'/split).mkdir(parents=True,exist_ok=True)
            for f in manifest['frames']:
                f['split']=best[group(f)];source=path/'raw'/(f['id']+'.jpg');raw=source.read_bytes()
                if digest(raw)!=f['image_sha256']:raise WorkbenchError(409,'Source image changed since capture; freeze refused')
                shutil.copyfile(source,path/'images'/f['split']/(f['id']+'.jpg'))
                text=''.join(str(x['class_id'])+' '+' '.join(f'{v:.8f}' for v in x['bbox'])+'\n' for x in f['labels'])
                (path/'labels'/f['split']/(f['id']+'.txt')).write_text(text)
                f['yolo_label_sha256']=digest(text.encode())
            if independent:
                from dataset_identity import source_audit
                if not source_audit(manifest['frames'])['independent_source_test']:raise WorkbenchError(422,'원본 이미지 그룹이 학습/검증/시험에 겹칩니다.')
            manifest['provenance_schema']=2
            provenance=dict(classes=manifest['classes'],revision=manifest['revision'],frames=[{**{k:f[k] for k in ('id','episode','split','image_sha256','label_sha256','yolo_label_sha256')},**({'source_group':f.get('source_group')} if manifest.get('provenance_schema')==2 else {})} for f in manifest['frames']])
            from dataset_identity import source_audit
            manifest['source_audit']=source_audit(manifest['frames'])
            manifest['dataset_sha256']=digest(canonical(provenance));manifest['class_schema_sha256']=digest(canonical(manifest['classes']));manifest['frozen']=True;manifest['frozen_at']=now();manifest['split_policy']='episode-grouped; approximately 70/15/15; no shared episode or image hash'
            yaml='path: '+json.dumps(str(path),ensure_ascii=False)+'\ntrain: images/train\nval: images/val\ntest: images/test\nnames:\n'+''.join(f'  {i}: '+json.dumps(name,ensure_ascii=False)+'\n' for i,name in enumerate(manifest['classes']))
            (path/'data.yaml').write_text(yaml);self._dataset_counts(manifest);atomic_json(path/'manifest.json',manifest);return manifest

    def _stop_process(self,process,grace=3.):
        if process.poll() is not None:return
        try:
            os.killpg(process.pid,signal.SIGTERM) if os.name=='posix' else process.terminate()
            process.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid,signal.SIGKILL) if os.name=='posix' else process.kill();process.wait(timeout=3)
        except ProcessLookupError:process.wait(timeout=3)

    def _sync_progress(self,job,directory):
        progress=read_json(directory/'progress.json')
        if isinstance(progress,dict):
            with self.lock:
                merged={**job['progress'],**clean_json(progress)}
                if merged!=job['progress']:job['progress']=merged;self._save_job(job)

    def _run(self,job,command,cancel):
        directory=self.store/'jobs'/job['id'];log=(directory/'worker.log').open('wb')
        try:
            process=subprocess.Popen(command,cwd=self.root,env=self._environment(),stdout=log,stderr=subprocess.STDOUT,start_new_session=(os.name=='posix'))
            with self.lock:self.processes[job['id']]=process;job['pid']=process.pid;self._save_job(job)
            started=time.monotonic()
            while process.poll() is None:
                self._sync_progress(job,directory)
                if cancel.wait(.15) or self.stopping.is_set():self._sync_progress(job,directory);self._stop_process(process);raise RuntimeError('Job cancelled')
                if time.monotonic()-started>6*3600:self._stop_process(process);raise RuntimeError('Job exceeded six-hour local limit')
                if log.tell()>32*1024*1024:self._stop_process(process);raise RuntimeError('Worker log exceeded 32 MiB')
            self._sync_progress(job,directory)
            if process.returncode:raise RuntimeError(f'Worker exited with code {process.returncode}; see job logs')
        finally:log.close()

    def _training(self,job,cancel):
        path,dataset=self._dataset(job['dataset_id']);self._verify_dataset(path,dataset);directory=self.store/'jobs'/job['id'];settings=job['settings']
        worker=self.root/'training'/'workbench_worker.py'
        if not self.python.is_file() or not worker.is_file():raise RuntimeError('Training Python environment or workbench_worker.py is unavailable')
        model=settings['model'];source=settings.get('source') or self._training_source(model,dataset)
        expected_source_hash=None
        if source['kind']=='registered':
            weights,verified=self._registered_training_source(source['model_id'],dataset['classes'],source.get('weights_sha256'))
            model=str(weights);expected_source_hash=verified['weights_sha256']
        elif job['kind']=='evaluate':
            weights,source=self._registered_training_source(job['model_id'],dataset['classes'])
            model=str(weights);expected_source_hash=source['weights_sha256']
        command=[str(self.python),str(worker),'--kind',job['kind'],'--dataset',str(path/'data.yaml'),'--output',str(directory),'--model',model,'--epochs',str(settings['epochs']),'--imgsz',str(settings['imgsz']),'--batch',str(settings['batch']),'--device',settings['device']]
        if expected_source_hash:command.extend(['--expected-source-sha256',expected_source_hash])
        self._run(job,command,cancel)
        result=clean_json(read_json(directory/'result.json'))
        if not isinstance(result,dict):raise RuntimeError('Worker exited without a valid result.json')
        if result.get('status') in ('failed','cancelled','dry_run'):raise RuntimeError('Worker did not report successful real execution')
        if result.get('dataset_sha256') and result['dataset_sha256']!=dataset['dataset_sha256']:raise RuntimeError('Worker dataset hash does not match the frozen dataset')
        if result.get('classes') and result['classes']!=dataset['classes']:raise RuntimeError('Worker/model class schema does not match the dataset')
        if expected_source_hash and result.get('source_weights_sha256')!=expected_source_hash:raise RuntimeError('Worker starting weights hash does not match the selected model')
        if job['kind']=='evaluate':
            model_path,model_record=self._model(job['model_id'])
            if digest((model_path/'weights.pt').read_bytes())!=model_record['weights_sha256']:raise RuntimeError('Registered weights failed integrity check')
            model_record['evaluation_jobs']=model_record.get('evaluation_jobs',[])+[job['id']]
            model_record.update(metrics=result.get('metrics',{}),per_class=result.get('per_class',{}),quality=self._quality(result,dataset.get('synthetic',True)),last_evaluated_at=now(),evaluation_dataset_id=dataset['id'],evaluation_dataset_sha256=dataset['dataset_sha256'])
            model_record['source_audit']=dataset.get('source_audit',{'independent_source_test':False})
            self._copy_artifacts(result,directory,path,model_path,model_record)
            atomic_json(model_path/'model.json',model_record);return {'model_id':model_record['id'],'metrics':model_record['metrics'],'evaluation_job_id':job['id']}
        weights=Path(result.get('weights_path','')).resolve()
        if not weights.is_file() or not weights.is_relative_to(directory.resolve()) or weights.stat().st_size==0:raise RuntimeError('Training produced no usable weights inside its own job directory')
        weight_hash=digest(weights.read_bytes())
        if result.get('weights_sha256')!=weight_hash:raise RuntimeError('Training weights hash verification failed')
        identifier=uid('model');model_path=self.store/'models'/identifier;model_path.mkdir();shutil.copyfile(weights,model_path/'weights.pt')
        if digest((model_path/'weights.pt').read_bytes())!=weight_hash:raise RuntimeError('Model registry copy hash mismatch')
        title=f'{"추가 학습" if source["kind"]=="registered" else settings["model"]} · {dataset["name"]} · {time.strftime("%m/%d %H:%M")}'
        record=dict(id=identifier,name=title,created_at=now(),dataset_id=dataset['id'],dataset_sha256=dataset['dataset_sha256'],class_schema_sha256=dataset['class_schema_sha256'],classes=dataset['classes'],training_job_id=job['id'],evaluation_jobs=[job['id']],weights_sha256=weight_hash,weights_bytes=weights.stat().st_size,metrics=result.get('metrics',{}),per_class=result.get('per_class',{}),quality=self._quality(result,dataset.get('synthetic',True)),settings=settings,runtime=result.get('runtime'),elapsed_seconds=result.get('elapsed_seconds'),source='real_training_worker',training_source=source,parent_model_id=source.get('model_id'),source_weights_sha256=result.get('source_weights_sha256'))
        record['source_audit']=dataset.get('source_audit',{'independent_source_test':False})
        self._copy_artifacts(result,directory,path,model_path,record);atomic_json(model_path/'model.json',record)
        return {'model_id':identifier,'weights_sha256':weight_hash,'dataset_sha256':dataset['dataset_sha256'],'metrics':record['metrics']}

    def _quality(self,result,synthetic=True):
        metrics=result.get('metrics',{});test=metrics.get('test',metrics) if isinstance(metrics,dict) else {}
        values=[v for k,v in test.items() if isinstance(v,(float,int)) and not isinstance(v,bool) and any(x in str(k).lower() for x in ('map','precision','recall'))]
        map_values=[v for k,v in test.items() if 'map50' in str(k).lower() and isinstance(v,(float,int)) and not isinstance(v,bool)]
        status='unvalidated' if not values else 'measured_zero' if not any(v>0 for v in (map_values or values)) else 'measured'
        return dict(status=status,synthetic_only=bool(synthetic),real_world_validated=False,worker_assessment=result.get('quality'),message='Measured metrics are zero; model usefulness is not demonstrated' if status=='measured_zero' else 'Measured on held-out dataset; real-world transfer is not validated')

    def _copy_artifacts(self,result,job_dir,dataset_dir,model_dir,record):
        artifact_dir=model_dir/'artifacts';artifact_dir.mkdir(exist_ok=True)
        def save(value):
            if not isinstance(value,str):return None
            candidate=Path(value);candidate=(job_dir/candidate).resolve() if not candidate.is_absolute() else candidate.resolve()
            if not candidate.is_file() or not any(candidate.is_relative_to(base.resolve()) for base in (job_dir,dataset_dir)):return None
            if candidate.suffix.lower() not in ('.jpg','.jpeg','.png','.json','.csv','.txt'):return None
            if candidate.stat().st_size>32*1024*1024:return None
            name=digest(str(candidate).encode())[:10]+'-'+candidate.name;shutil.copyfile(candidate,artifact_dir/name)
            return f'/api/workbench/models/{record["id"]}/artifacts/{name}'
        record['plots']=[]
        for item in result.get('plots',[]):
            value=item.get('path',item.get('image')) if isinstance(item,dict) else item;url=save(value)
            if url:record['plots'].append(dict(name=Path(value).name,image_url=url))
        record['samples']=[]
        for sample in result.get('samples',[]):
            if not isinstance(sample,dict):continue
            clean={k:v for k,v in sample.items() if k not in ('image','prediction')};clean['image_url']=save(sample.get('image'));clean['prediction_url']=save(sample.get('prediction'))
            if clean['image_url'] or clean['prediction_url']:record['samples'].append(clean)

    def deploy(self,body):
        with self.lock:
            if self.controller_process and self.controller_process.poll() is None:raise WorkbenchError(409,'Stop the current UGV controller before changing its deployment')
            source,model=self._model(body.get('model_id'));slot=body.get('slot','B-1');mode=body.get('mode','practice');policy=body.get('policy','observe')
            if slot not in ('B-1','B-2','B-3','R-1','R-2','R-3') or mode not in ('practice','match','rehearsal') or policy not in ('observe','mission'):raise WorkbenchError(422,'Invalid deployment slot/mode/policy')
            weights=source/'weights.pt';raw=weights.read_bytes()
            if digest(raw)!=model['weights_sha256']:raise WorkbenchError(409,'Registered model hash mismatch; deployment refused')
            version_id=body.get('code_version_id')
            if not version_id:version_id=self.code_store.snapshot('적용 시 저장한 코드')['id']
            code_source,code=self.code_store.version(version_id)
            identifier=uid('deployment');path=self.store/'deployments'/identifier;path.mkdir();(path/'weights.pt').write_bytes(raw)
            if digest((path/'weights.pt').read_bytes())!=model['weights_sha256']:raise RuntimeError('Deployment copy verification failed')
            deployment=dict(id=identifier,model_id=model['id'],dataset_id=model['dataset_id'],dataset_sha256=model['dataset_sha256'],weights_sha256=model['weights_sha256'],classes=model['classes'],slot=slot,mode=mode,policy=policy,created_at=now(),status='copied',loaded=False)
            code_dir=path/'code';code_dir.mkdir()
            for name,expected in code['files'].items():
                target=code_dir/name;target.write_bytes((code_source/name).read_bytes())
                if digest(target.read_bytes())!=expected:raise WorkbenchError(409,'Code copy verification failed')
                target.chmod(0o444)
            deployment.update(code_version_id=code['id'],code_name=code['name'],code_sha256=code['sha256'],entrypoint_sha256=code['files']['team_node.py'],code_files=code['files'],code_path=str(code_dir/'team_node.py'))
            atomic_json(path/'manifest.json',deployment);atomic_json(self.store/'deployment.json',deployment);self.deployment=deployment;self.controller_directory=None
            return copy.deepcopy(deployment)

    def start_controller(self,body=None):
        with self.lock:
            body=body or {}
            if not self.deployment:raise WorkbenchError(409,'Deploy a registered model first')
            if self.controller_process and self.controller_process.poll() is None:raise WorkbenchError(409,'UGV controller is already running')
            recovery=self._recover_controllers()
            if recovery.get('error') or recovery.get('remaining'):
                raise WorkbenchError(503,'이전 로봇 프로세스의 종료를 확인하지 못했습니다. 실행을 중지했습니다.')
            deployment=self.deployment;weights=self.store/'deployments'/deployment['id']/'weights.pt'
            if 'expected_deployment_id' in body and body['expected_deployment_id']!=deployment.get('id'):
                raise WorkbenchError(409,'복원 이후 적용 구성이 변경됐습니다. 장면을 다시 복원하세요.')
            if 'expected_code_version_id' in body and body['expected_code_version_id']!=deployment.get('code_version_id'):
                raise WorkbenchError(409,'선택한 코드 버전과 현재 적용된 코드가 다릅니다. 장면을 다시 복원하세요.')
            deployment_runtime='mission' if deployment['mode']=='match' else deployment['mode']
            if 'expected_runtime' in body and body['expected_runtime']!=deployment_runtime:
                raise WorkbenchError(409,'선택한 재시험 환경과 현재 적용 환경이 다릅니다. 장면을 다시 복원하세요.')
            if digest(weights.read_bytes())!=deployment['weights_sha256']:raise WorkbenchError(409,'Deployed weights changed; refuse to launch')
            # Existing deployments from before source versioning are upgraded to a
            # new immutable deployment explicitly at the first process start.
            if not deployment.get('code_version_id'):
                deployment=self.deploy({k:deployment[k] for k in ('model_id','slot','mode','policy')})
                weights=self.store/'deployments'/deployment['id']/'weights.pt'
            script=self.store/'deployments'/deployment['id']/'code'/'team_node.py'
            for name,expected in deployment['code_files'].items():
                source=script.parent/name
                if source.is_symlink() or not source.is_file() or digest(source.read_bytes())!=expected:raise WorkbenchError(409,'Deployed code changed; refuse to launch')
            if not self.python.is_file() or not script.is_file():raise WorkbenchError(409,'Learned controller or Python runtime is unavailable')
            access=read_json(self.root/'data'/'access.json',{});team=next((t for t in access.get('teams',[]) if t['slot']==deployment['slot']),None)
            if not team:raise WorkbenchError(409,'No local mission credential for the deployed slot')
            runtime=self._runtime();physics=runtime.get('physics') or {}
            expected_runtime='mission' if deployment['mode']=='match' else deployment['mode']
            if not physics.get('connected'):raise WorkbenchError(409,'Start the native physics backend before launching the UGV controller')
            if physics.get('runtime')!=expected_runtime:raise WorkbenchError(409,'Native physics runtime does not match the deployment mode')
            if 'expected_runtime' in body and body['expected_runtime']!=physics.get('runtime'):
                raise WorkbenchError(409,'선택한 재시험 환경과 물리 시뮬레이터가 다릅니다. 장면을 다시 복원하세요.')
            server=runtime.get('server_url','http://127.0.0.1:8765')
            from urllib.parse import urlparse
            parsed=urlparse(server)
            if parsed.scheme!='http' or parsed.hostname not in ('127.0.0.1','localhost','::1'):raise WorkbenchError(409,'UGV backend must be this local loopback server')
            output=self.store/'controllers'/uid('controller');output.mkdir();self.controller_directory=output
            from fidelity_settings import read as read_settings
            settings=read_settings(self.root);transport=settings['transport'];supervisor=script.with_name('participant_supervisor.py')
            entry=supervisor if supervisor.is_file() else script
            if transport=='ros' and not supervisor.is_file():raise WorkbenchError(409,'ROS 실행에는 새 참가 코드 버전을 저장·선택하세요')
            arguments=[str(entry),'--server',server,'--mission-code',team['mission_code'],'--mode',deployment['mode'],'--weights',str(weights),'--output',str(output),'--policy',deployment['policy']]
            command=['bash',str(self.root/'ros2/run-participant.sh'),*arguments] if transport=='ros' else [str(self.python),*arguments]
            env=self._environment();env.update(MAICON_ROS_SLOT=deployment['slot'],MAICON_ROS_NAMESPACE='participant',MAICON_ROS_SCAN_TOPIC='/participant/scan',MAICON_ROS_INTERFACE_PACKAGE='sim_mil_interfaces')
            log=(output/'worker.log').open('wb')
            try:process=subprocess.Popen(command,cwd=self.root,env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=(os.name=='posix'))
            finally:log.close()
            self.controller_process=process
            launch=dict(output_dir=str(output),pid=process.pid,model_id=deployment['model_id'],deployment_id=deployment['id'],started_at=now(),code_version_id=deployment['code_version_id'],code_sha256=deployment['code_sha256'],entrypoint_sha256=deployment['entrypoint_sha256'],code_path=str(script),code_files=deployment['code_files'])
            launch.update(server_url=server,weights_path=str(weights),transport=transport,process_script=str(entry),container_name=('maicon-rebuild-'+output.name) if transport=='ros' else None,environment_identity=runtime.get('identity'),weights_sha256=deployment['weights_sha256'])
            atomic_json(output/'launch.json',launch)
            atomic_json(self.store/'controller.json',launch)
            return self.controller_snapshot()

    def stop_controller(self):
        with self.lock:
            if self.controller_process:self._stop_process(self.controller_process)
            recovery=self._recover_controllers()
            if self.controller_directory:atomic_json(self.controller_directory/'stopped.json',{'stopped_at':now(),'reason':'user_stop'})
            if recovery.get('error') or recovery.get('remaining'):
                raise WorkbenchError(503,'이전 로봇 프로세스의 종료를 확인하지 못했습니다. 프로세스 기록을 확인하세요.')
            return self.controller_snapshot()

    def _recover_controllers(self):
        """Recover only recorded process identities, including after a server crash."""
        if not any((self.store/'controllers').glob('*/launch.json')):
            self.controller_recovery={'stopped':[],'remaining':[]};return self.controller_recovery
        helper=Path(__file__).with_name('controller_recovery.py')
        server=self.runtime_status().get('server_url','http://127.0.0.1:8765')
        try:
            completed=subprocess.run([str(self.python),str(helper),'--root',str(self.root),'--server',server],
                stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=10)
            result=json.loads(completed.stdout)
            if completed.returncode or not isinstance(result,dict):raise ValueError('recovery failed')
        except (OSError,ValueError,subprocess.SubprocessError):result={'stopped':[],'remaining':[],'error':'ownership verification unavailable'}
        for directory in result.get('stopped',[]):
            path=Path(directory).resolve()
            if path.parent==(self.store/'controllers').resolve():
                atomic_json(path/'stopped.json',{'stopped_at':now(),'reason':'recovered_owned_process'})
        self.controller_recovery=result
        atomic_json(self.store/'controller-recovery.json',result)
        return result

    def _controller_frame(self):
        directory=self.controller_directory
        if not directory:raise WorkbenchError(404,'No UGV inference frame is available')
        # Status-last writer: only accept a byte image matching the announced generation.
        for _ in range(3):
            status=read_json(directory/'status.json',{});predictions=read_json(directory/'predictions.json',{})
            name=status.get('frame_path','frame.jpg')
            if name!='frame.jpg' and not re.fullmatch(r'frame-[0-9]{8}\.jpg',str(name)):raise WorkbenchError(409,'Invalid controller frame reference')
            try:raw=(directory/name).read_bytes()
            except OSError:raise WorkbenchError(404,'UGV has not produced an inference frame')
            image_hash=digest(raw)
            if status.get('frame_seq') is not None and status.get('image_sha256')==image_hash and (not predictions or (predictions.get('frame_seq')==status['frame_seq'] and predictions.get('image_sha256',image_hash)==image_hash)) and read_json(directory/'status.json',{}).get('frame_seq')==status['frame_seq']:
                return raw,status
        raise WorkbenchError(409,'Inference frame generation is changing; retry')

    def controller_snapshot(self):
        with self.lock:
            deployment=self.deployment;process=self.controller_process;directory=self.controller_directory
            if not directory:return dict(status='not_started',pid=None,model_id=deployment['model_id'] if deployment else None,weights_sha256=deployment['weights_sha256'] if deployment else None,loaded=False,frame_seq=0,predictions=[],observations={},logs=[],code_version_id=deployment.get('code_version_id') if deployment else None,code_sha256=deployment.get('code_sha256') if deployment else None)
            state=read_json(directory/'status.json',{});running=process is not None and process.poll() is None;stopped=(directory/'stopped.json').exists()
            actual_hash=state.get('weights_sha256');expected_hash=deployment.get('weights_sha256') if deployment else None
            loaded=bool(running and state.get('loaded') and expected_hash and actual_hash==expected_hash)
            result=dict(state)
            launch=read_json(directory/'launch.json',{})
            code_path=Path(launch['code_path']) if launch.get('code_path') else None
            code_integrity=bool(code_path and all((code_path.parent/name).is_file() and digest((code_path.parent/name).read_bytes())==expected for name,expected in launch.get('code_files',{}).items()))
            result.update({k:launch.get(k) for k in ('code_version_id','code_sha256','entrypoint_sha256','code_path')})
            result['code_integrity']=code_integrity
            result.update(status=('stopped' if stopped else 'failed' if process and process.poll() not in (None,0) else 'exited' if process and process.poll()==0 else 'interrupted' if not process else state.get('status','starting')),pid=process.pid if process else None,model_id=deployment.get('model_id') if deployment else None,weights_sha256=actual_hash,expected_weights_sha256=expected_hash,loaded=loaded,frame_seq=state.get('frame_seq',0),predictions=state.get('predictions',[]),observations=state.get('observations',{}),logs=self._logs(directory/'worker.log'),exit_code=process.poll() if process else None)
            if state.get('loaded') and expected_hash!=actual_hash:result.update(status='failed',loaded=False,error='Loaded model hash does not match deployment')
            if loaded:
                try:
                    _,frame_state=self._controller_frame()
                    for key in ('frame_seq','image_sha256','predictions','observations','captured_at','inference_ms','latency','drone'):
                        if key in frame_state:result[key]=frame_state[key]
                    result['frame_url']='/api/workbench/controller/frame.jpg'
                except WorkbenchError:result['frame_url']=None
            return clean_json(result)

    def handle(self,method,route,body=None,query=None):
        method=method.upper();body=body or {};query=query or {};route=route.rstrip('/') or '/'
        try:
            if not isinstance(body,dict):raise WorkbenchError(422,'JSON body must be an object')
            if method=='GET' and route=='/status':return 200,self.status(),JSON_TYPE
            if method=='POST' and route=='/jobs':return 202,self.create_job(body),JSON_TYPE
            if route.startswith('/jobs/'):
                parts=route.strip('/').split('/');identifier=parts[1]
                with self.lock:
                    job=self.jobs.get(identifier)
                    if not job:raise WorkbenchError(404,'Job not found')
                    if method=='GET' and len(parts)==2:return 200,self._job_snapshot(job),JSON_TYPE
                    if method=='POST' and parts[2:]==['cancel']:
                        if job['status'] in TERMINAL:return 200,self._job_snapshot(job),JSON_TYPE
                        self.cancel_events[identifier].set();job['status']='cancelling';self._save_job(job);return 202,self._job_snapshot(job),JSON_TYPE
            if method=='POST' and route=='/datasets/import':return 201,self.import_dataset(body),JSON_TYPE
            if route.startswith('/datasets/'):
                parts=route.strip('/').split('/');path,manifest=self._dataset(parts[1])
                if method=='GET' and len(parts)==2:return 200,manifest,JSON_TYPE
                if method=='GET' and parts[2:]==['frames']:return 200,{'frames':manifest['frames']},JSON_TYPE
                if method=='GET' and len(parts)==4 and parts[2]=='frames':
                    name=parts[3]
                    if not re.fullmatch(r'frame-[0-9]{6}\.jpg',name):raise WorkbenchError(404,'Frame not found')
                    image=path/'raw'/name
                    if not image.is_file():raise WorkbenchError(404,'Frame not found')
                    return 200,image.read_bytes(),'image/jpeg'
                if method=='POST' and parts[2:]==['labels']:return 200,self.labels(parts[1],body),JSON_TYPE
                if method=='POST' and parts[2:]==['clone']:return 201,self.clone_dataset(parts[1],body),JSON_TYPE
                if method=='POST' and parts[2:]==['freeze']:return 200,self.freeze(parts[1],independent=body.get('independent_sources',False)),JSON_TYPE
            if route.startswith('/models/'):
                parts=route.strip('/').split('/');path,record=self._model(parts[1])
                if method=='GET' and len(parts)==2:return 200,record,JSON_TYPE
                if method=='GET' and len(parts)==4 and parts[2]=='artifacts':
                    name=parts[3]
                    if '/' in name or '\\' in name or name in ('.','..'):raise WorkbenchError(404,'Artifact not found')
                    file=path/'artifacts'/name
                    if not file.is_file() or file.suffix.lower() not in ('.jpg','.jpeg','.png','.json','.csv','.txt'):raise WorkbenchError(404,'Artifact not found')
                    return 200,file.read_bytes(),mimetypes.guess_type(name)[0] or 'application/octet-stream'
            if method=='POST' and route=='/deploy':return 201,self.deploy(body),JSON_TYPE
            if method=='POST' and route=='/controller/start':return 202,self.start_controller(body),JSON_TYPE
            if method=='POST' and route=='/controller/stop':return 200,self.stop_controller(),JSON_TYPE
            if method=='GET' and route=='/controller':return 200,self.controller_snapshot(),JSON_TYPE
            if method=='GET' and route=='/controller/frame.jpg':
                raw,status=self._controller_frame();return 200,raw,'image/jpeg',{'X-Frame-Seq':str(status['frame_seq']),'X-Image-Sha256':status['image_sha256']}
            raise WorkbenchError(404,'Unknown workbench route')
        except (WorkbenchError,DevelopmentError) as exc:return exc.status,{'detail':str(exc)},JSON_TYPE
        except Exception as exc:return 500,{'detail':self._redact(str(exc))[:1500]},JSON_TYPE

    def close(self):
        self.stopping.set()
        with self.lock:
            for event in self.cancel_events.values():event.set()
            processes=list(self.processes.values())
            if self.controller_process:processes.append(self.controller_process)
        for process in processes:
            try:self._stop_process(process)
            except Exception:pass
        self._recover_controllers()
        self.worker.join(timeout=5)
