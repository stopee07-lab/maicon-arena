"""Append-only experiment identity; organizer exports never enter contestant IO."""
from pathlib import Path
import json,time
from workbench import atomic_json,read_json
class ExperimentJournal:
 def __init__(self,server,root):self.server=server;self.root=Path(root);self.last=0.;self.finished=set()
 def observe(self,reason=None):
  if reason is None and time.monotonic()-self.last<1:return
  self.last=time.monotonic();wb=self.server.workbench
  if not wb or not wb.controller_directory:return
  directory=wb.controller_directory;launch=read_json(directory/'launch.json',{})
  if not launch or str(directory) in self.finished:return
  deployment=read_json(wb.store/'deployments'/launch['deployment_id']/'manifest.json',{})
  mode=deployment.get('mode');e={'match':self.server.engine,'practice':self.server.practice,'rehearsal':self.server.rehearsal}.get(mode)
  if not e:return
  out=self.root/'data/experiments'/directory.name;out.mkdir(parents=True,exist_ok=True);meta_path=out/'experiment.json'
  if meta_path.exists() and read_json(meta_path,{}).get('status')!='running':
   self.finished.add(str(directory));return
  if not meta_path.exists():
   record=dict(id=directory.name,started_at=launch['started_at'],identity=launch.get('environment_identity',e.identity()),slot=deployment.get('slot'),runtime=mode,transport=launch.get('transport','http'),code_sha256=launch.get('code_sha256'),model_sha256=launch.get('weights_sha256',deployment.get('weights_sha256')),code_version_id=launch.get('code_version_id'),controller_dir=str(directory),sensor_decision_log=str(directory/'trace.jsonl'),report_event_log=str(directory/'events.jsonl'),trace_rate_hz=2,video=None,status='running',source_note='새 환경 결과; 과거 점수 재채점 안 함')
   atomic_json(meta_path,record)
  state=read_json(directory/'status.json',{});process=wb.controller_process;exited=process is not None and process.poll() is not None
  if process is None:
   record=read_json(meta_path,{});record.update(status='interrupted',reason='server_restarted; no reconstructed match export',finished_at=time.time());atomic_json(meta_path,record);self.finished.add(str(directory));return
  if reason or e.phase=='finished' or exited:
   with e.lock:export=e.export()
   risks={r['slot']:r['hp']/4+r['ammo']/8+r['score']['m3']/30 for r in export['state']['robots']}
   atomic_json(out/'m6-evaluation-context.json',dict(risks=risks,distinct_levels={camp:len({v for k,v in risks.items() if k.startswith(prefix)}) for camp,prefix in [('BLUE','B'),('RED','R')]},note='동점 상대의 M6 만점은 위험도 순서 추론 능력을 입증하지 않음'))
   atomic_json(out/'match.json',export);record=read_json(meta_path,{})
   finished=e.phase=='finished';record.update(status='completed' if finished else 'failed' if state.get('error') or (exited and process.returncode) else 'aborted',reason=state.get('error') or reason or ('controller_exit' if exited else 'match_finished'),finished_at=time.time(),result_path=str(out/'match.json'),scores={r['slot']:r['score'] for r in export['state']['robots']})
   atomic_json(meta_path,record);self.finished.add(str(directory))
