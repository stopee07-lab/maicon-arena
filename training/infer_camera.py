#!/usr/bin/env python3
"""Run saved weights on OWN simulator camera only; never consume privileged labels."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'examples'))
from robot_client import TeamClient


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--server',default='http://127.0.0.1:8765')
    parser.add_argument('--mission-code',required=True)
    parser.add_argument('--mode',choices=['match','practice','rehearsal'],default='match')
    parser.add_argument('--weights',type=Path,required=True)
    parser.add_argument('--output',type=Path,default=Path(__file__).parent/'inference-smoke')
    parser.add_argument('--imgsz',type=int,default=160)
    parser.add_argument('--device',default='cpu')
    args=parser.parse_args()
    weights=args.weights.resolve()
    if not weights.is_file():parser.error('weights do not exist; train first')
    if args.output.exists():parser.error('output already exists; choose a new folder')
    os.environ.setdefault('YOLO_CONFIG_DIR',str(Path(__file__).resolve().parent/'.ultralytics'))
    os.environ.setdefault('YOLO_AUTOINSTALL','false')
    from PIL import Image
    from ultralytics import YOLO
    client=TeamClient(args.server,args.mission_code,timeout=2.,
        robot_prefix='/sim/robot' if args.mode=='match' else '/sim/'+args.mode+'/robot')
    own_state=client.sim_get('state')
    jpeg,headers=client.sim_get('camera')
    frame=Image.open(io.BytesIO(jpeg)).convert('RGB')
    model=YOLO(str(weights))
    started=time.perf_counter()
    results=model.predict(frame,imgsz=args.imgsz,device=args.device,verbose=False)
    elapsed=time.perf_counter()-started
    predictions=[]
    for result in results:
        for box in result.boxes:
            predictions.append({'class_id':int(box.cls.item()),'class_name':result.names[int(box.cls.item())],
                'confidence':float(box.conf.item()),'xyxy':[float(x) for x in box.xyxy[0].tolist()]})
    args.output.mkdir(parents=True)
    (args.output/'own-camera.jpg').write_bytes(jpeg)
    results[0].save(filename=str(args.output/'predictions.jpg'))
    record={'kind':'own_sensor_inference_smoke','synthetic':True,'mode':args.mode,'slot':own_state['slot'],
        'weights':str(weights),'weights_sha256':hashlib.sha256(weights.read_bytes()).hexdigest(),
        'image_sha256':hashlib.sha256(jpeg).hexdigest(),'image_size':list(frame.size),
        'elapsed_seconds':elapsed,'device':args.device,'imgsz':args.imgsz,'predictions':predictions,
        'source':client.robot_prefix+'/camera','admin_truth_used':False,'real_world_accuracy_validated':False}
    (args.output/'result.json').write_text(json.dumps(record,indent=2,ensure_ascii=False)+'\n')
    print(json.dumps(record,indent=2,ensure_ascii=False))

if __name__=='__main__':main()
