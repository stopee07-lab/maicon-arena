#!/usr/bin/env python3
"""Optional real Ultralytics training; never silently install torch or claim a dry-run trained."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data',type=Path,required=True)
    parser.add_argument('--model',default='yolo26n.yaml',help='YAML trains from scratch; .pt may download pretrained weights')
    parser.add_argument('--epochs',type=int,default=30)
    parser.add_argument('--imgsz',type=int,default=640)
    parser.add_argument('--batch',type=int,default=4)
    parser.add_argument('--device',default='cpu',help='cpu, mps (supported Apple environment), or CUDA index 0 on target')
    parser.add_argument('--output',type=Path,default=Path(__file__).parent/'runs')
    parser.add_argument('--seed',type=int,default=42)
    parser.add_argument('--export-onnx',action='store_true')
    parser.add_argument('--dry-run',action='store_true',help='Validate input and print plan without importing ML runtime')
    args=parser.parse_args()
    data=args.data.resolve()
    if not data.is_file():parser.error('data.yaml does not exist; export and inspect a dataset first')
    manifest=data.parent/'manifest.json'
    provenance=json.loads(manifest.read_text()) if manifest.exists() else {'synthetic':'unknown'}
    if args.epochs<1:parser.error('epochs must be positive')
    plan={'status':'not_started','data':str(data),'model':args.model,'epochs':args.epochs,
          'device':args.device,'seed':args.seed,'imgsz':args.imgsz,'batch':args.batch,'synthetic':provenance.get('synthetic'),
          'real_world_accuracy_validated':False}
    if args.dry_run:
        plan['status']='dry_run_no_training';print(json.dumps(plan,indent=2,ensure_ascii=False));return
    os.environ.setdefault('YOLO_CONFIG_DIR',str(Path(__file__).resolve().parent/'.ultralytics'))
    os.environ.setdefault('YOLO_AUTOINSTALL','false')
    try:import ultralytics
    except ImportError:raise SystemExit('Ultralytics/torch are not installed. See docs/robot-training.md; nothing was trained.')
    run=args.output.resolve()/time.strftime('yolo-%Y%m%d-%H%M%S')
    if run.exists():raise SystemExit('Run folder already exists; retry with a new output path.')
    run.mkdir(parents=True)
    metadata=run/'training-status.json'
    plan.update(status='running',ultralytics_version=ultralytics.__version__,started_unix=time.time())
    metadata.write_text(json.dumps(plan,indent=2,ensure_ascii=False))
    try:
        model=ultralytics.YOLO(args.model)
        model.train(data=str(data),epochs=args.epochs,imgsz=args.imgsz,batch=args.batch,device=args.device,
                    workers=0,seed=args.seed,deterministic=True,project=str(run),name='fit',exist_ok=False)
        best=Path(model.trainer.best)
        if not best.is_file() or best.stat().st_size==0:raise RuntimeError('training returned without a usable best.pt')
        trained=ultralytics.YOLO(str(best))
        metrics=trained.val(data=str(data),device=args.device,split='val',workers=0)
        plan.update(status='trained_synthetic_only',best=str(best),weights_sha256=hashlib.sha256(best.read_bytes()).hexdigest(),
                    validation={str(k):float(v) for k,v in metrics.results_dict.items()},finished_unix=time.time())
        if args.export_onnx:plan['onnx']=str(trained.export(format='onnx',imgsz=args.imgsz,device='cpu'))
    except Exception as exc:
        plan.update(status='failed',error=str(exc));metadata.write_text(json.dumps(plan,indent=2,ensure_ascii=False));raise
    metadata.write_text(json.dumps(plan,indent=2,ensure_ascii=False));print(json.dumps(plan,indent=2,ensure_ascii=False))

if __name__=='__main__':main()
