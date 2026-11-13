#!/usr/bin/env python3
"""Export privileged synthetic training frames. This is NOT a contestant sensor client."""
import argparse
import base64
import hashlib
import json
import math
import os
from pathlib import Path
import random
import tempfile
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen

CLASSES=['military_truck','mortar','tank','missile','ammo_box','drum']


def yolo_line(annotation,nclasses):
    class_id=annotation['class_id'];box=annotation['bbox']
    if isinstance(class_id,bool) or not isinstance(class_id,int) or not 0<=class_id<nclasses:
        raise ValueError('invalid class_id')
    if len(box)!=4 or not all(isinstance(x,(float,int)) and math.isfinite(x) for x in box):
        raise ValueError('bbox must be four finite normalized numbers')
    x,y,w,h=box
    if not (0<w<=1 and 0<h<=1 and 0<=x-w/2 and x+w/2<=1.000001
            and 0<=y-h/2 and y+h/2<=1.000001):
        raise ValueError('bbox outside normalized image bounds')
    return str(class_id)+' '+ ' '.join(f'{v:.6f}' for v in box)


def split_indices(count,seed):
    if count<2:raise ValueError('need at least two frames')
    indices=list(range(count));random.Random(seed).shuffle(indices)
    nval=max(1,round(count*.2))
    return {'train':sorted(indices[nval:]),'val':sorted(indices[:nval])}


def jpeg_dimensions(data):
    """Parse JPEG SOF dimensions with stdlib. Full pixel decoding belongs in visual QA."""
    if not data.startswith(b'\xff\xd8') or not data.endswith(b'\xff\xd9'):
        raise ValueError('not a complete JPEG')
    i=2
    while i<len(data):
        if data[i]!=255:raise ValueError('invalid JPEG marker')
        while i<len(data) and data[i]==255:i+=1
        marker=data[i];i+=1
        if marker in (0xd8,0xd9):continue
        if marker==0xda:break
        size=int.from_bytes(data[i:i+2],'big')
        if size<2 or i+size>len(data):raise ValueError('invalid JPEG segment')
        if marker in (0xc0,0xc1,0xc2,0xc3,0xc5,0xc6,0xc7,0xc9,0xca,0xcb,0xcd,0xce,0xcf):
            return int.from_bytes(data[i+5:i+7],'big'),int.from_bytes(data[i+3:i+5],'big')
        i+=size
    raise ValueError('no supported JPEG dimensions')


def export_dataset(server,key,output,count=120,seed=42,slot='B-1'):
    output=Path(output).resolve()
    if output.exists():raise FileExistsError(f'Refusing to overwrite {output}; choose a new dataset version')
    if not key:raise ValueError('SIM_ADMIN_KEY is required for the separate training route')
    split=split_indices(count,seed)
    split_for={i:name for name,indices in split.items() for i in indices}
    output.parent.mkdir(parents=True,exist_ok=True)
    manifest={'synthetic':True,'purpose':'pipeline validation only; not evidence of real-camera accuracy',
        'seed':seed,'slot':slot,'count':count,'classes':CLASSES,'created_unix':time.time(),
        'source':'/api/admin/training-frame','split':'80/20 randomized frame split; same synthetic scene domain',
        'weights_trained':False,'frames':[],'class_counts':{name:[0]*6 for name in split}}
    hashes=set()
    with tempfile.TemporaryDirectory(prefix='.dataset-',dir=output.parent) as temp:
        root=Path(temp)/'dataset';root.mkdir()
        for name in split:
            (root/'images'/name).mkdir(parents=True);(root/'labels'/name).mkdir(parents=True)
        for i in range(count):
            frame_seed=seed+i
            url=server.rstrip('/')+'/api/admin/training-frame?'+urlencode({'slot':slot,'seed':frame_seed})
            request=Request(url,headers={'X-Admin-Key':key,'Accept':'application/json'})
            with urlopen(request,timeout=10) as response:
                frame=json.loads(response.read(8*1024*1024))
            if frame.get('synthetic') is not True or frame.get('classes')!=CLASSES:
                raise ValueError('unexpected frame class map or provenance')
            image=base64.b64decode(frame['image_base64'],validate=True)
            width,height=jpeg_dimensions(image)
            if (width,height)!=(frame['width'],frame['height']):raise ValueError('JPEG dimensions do not match metadata')
            if (width,height)!=(640,480):raise ValueError('expected 640x480 training frames')
            digest=hashlib.sha256(image).hexdigest()
            if digest in hashes:raise ValueError('duplicate image risks train/val leakage; choose distinct seeds')
            hashes.add(digest)
            rows=[yolo_line(a,len(CLASSES)) for a in frame['annotations']]
            subset=split_for[i];name=f'frame_{frame_seed:08d}'
            (root/'images'/subset/(name+'.jpg')).write_bytes(image)
            labels='\n'.join(rows)+ ('\n' if rows else '')
            (root/'labels'/subset/(name+'.txt')).write_text(labels)
            for annotation in frame['annotations']:manifest['class_counts'][subset][annotation['class_id']]+=1
            manifest['frames'].append({'stem':name,'seed':frame_seed,'split':subset,'image_sha256':digest,
                                       'label_sha256':hashlib.sha256(labels.encode()).hexdigest(),'objects':len(rows)})
        manifest['positive_frames']=sum(bool(f['objects']) for f in manifest['frames'])
        if manifest['positive_frames']==0:raise ValueError('no positive annotations; dataset unsuitable even for smoke training')
        # JSON-quoted absolute path is valid YAML, including Korean characters/spaces.
        yaml='path: '+json.dumps(str(output),ensure_ascii=False)+'\ntrain: images/train\nval: images/val\nnames:\n'
        yaml+=''.join(f'  {i}: {name}\n' for i,name in enumerate(CLASSES))
        (root/'data.yaml').write_text(yaml)
        (root/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
        (root/'README.txt').write_text('Synthetic icon camera frames. Labels come from privileged renderer geometry.\n'
            'Train/val share one synthetic scene domain. Real-camera and held-out-location validation are still required.\n'
            'No model weights were trained by this export. Do not expose manifest/labels to contestant code.\n')
        root.rename(output)
    return manifest


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--server',default='http://127.0.0.1:8765')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--count',type=int,default=120);parser.add_argument('--seed',type=int,default=42)
    parser.add_argument('--slot',choices=['B-1','B-2','B-3','R-1','R-2','R-3'],default='B-1')
    args=parser.parse_args()
    manifest=export_dataset(args.server,os.environ.get('SIM_ADMIN_KEY',''),args.output,args.count,args.seed,args.slot)
    print(json.dumps({'output':str(args.output.resolve()),'frames':manifest['count'],
                      'positive_frames':manifest['positive_frames'],'class_counts':manifest['class_counts'],
                      'weights_trained':False},ensure_ascii=False,indent=2))

if __name__=='__main__':main()
