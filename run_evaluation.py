#!/usr/bin/env python3
"""Six-slot physical evaluation. Default writes a plan; --execute runs real-time matches."""
import argparse,json,time,urllib.request,urllib.error,uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parent
SLOTS=['B-1','B-2','B-3','R-1','R-2','R-3']
def plan(seeds,profiles,slots):
 return [dict(seed=seed,sensor_profile=profile,slot=slot) for seed in seeds for profile in profiles for slot in slots]
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--server',default='http://127.0.0.1:8887');p.add_argument('--model-id',required=True);p.add_argument('--version-id');p.add_argument('--seeds',default='101,102,103');p.add_argument('--profiles',default='mild,stress');p.add_argument('--slots',default=','.join(SLOTS));p.add_argument('--execute',action='store_true');a=p.parse_args()
 jobs=plan([int(x) for x in a.seeds.split(',')],a.profiles.split(','),a.slots.split(','))
 if any(j['slot'] not in SLOTS or j['sensor_profile'] not in ('mild','stress','ideal','official_only') for j in jobs):p.error('invalid profile/slot')
 out=ROOT/'data/evaluations'/('evaluation-'+uuid.uuid4().hex[:12]);out.mkdir(parents=True)
 record=dict(jobs=jobs,model_id=a.model_id,version_id=a.version_id,opponent_mode='sensor_limited',status='planned',results=[]);file=out/'evaluation.json'
 def save():file.write_text(json.dumps(record,ensure_ascii=False,indent=2))
 save();print(str(file),flush=True)
 if not a.execute:return
 config=json.load(urllib.request.urlopen(a.server+'/api/config'));key=config['admin_key']
 def request(path,body=None):
  req=urllib.request.Request(a.server+path,data=json.dumps(body).encode() if body is not None else None,headers={'X-Admin-Key':key,'Content-Type':'application/json'})
  with urllib.request.urlopen(req,timeout=90) as r:return json.load(r)
 # Preflight does not own any existing execution; rejection must never stop it.
 state=request('/api/workbench/status')
 if state['physics'].get('running') or (state['controller'].get('pid') and state['controller'].get('status') not in ('stopped','exited','failed','interrupted')):raise RuntimeError('Stop active physics/participant before batch')
 if request('/api/subgame')['status'] in ('armed','running'):raise RuntimeError('Stop active subgame before batch')
 if request('/api/admin/state')['phase'] in ('ready','running','reporting'):raise RuntimeError('Finish/reset current match before batch')
 owned_deployment=None
 record['status']='running';save()
 try:
  for i,j in enumerate(jobs):
   if owned_deployment and (request('/api/workbench/status').get('deployment') or {}).get('id')!=owned_deployment:raise RuntimeError('Another deployment replaced the batch; stopped monitoring')
   request('/api/development/stop',{});request('/api/admin/control',{'action':'reset'});request('/api/admin/rehearsal/control',{'action':'reset'})
   request('/api/fidelity',{**{k:j[k] for k in ('seed','sensor_profile')},'opponent_mode':'sensor_limited'})
   result=request('/api/development/apply-run',dict(model_id=a.model_id,mode='match',policy='mission',slot=j['slot'],**({'version_id':a.version_id} if a.version_id else {})))
   owned_deployment=result['deployment']['id']
   started=time.monotonic();status='running'
   while True:
    state=request('/api/admin/state');workbench=request('/api/workbench/status');controller=workbench['controller']
    if (workbench.get('deployment') or {}).get('id')!=owned_deployment:raise RuntimeError('Another deployment replaced the batch; result not attributed')
    if state['phase']=='finished':status='completed';break
    if state['paused']:raise RuntimeError('Match paused; batch stops without auto-resuming')
    if controller.get('status') in ('failed','exited','stopped','interrupted'):raise RuntimeError('Participant stopped before match ended')
    if time.monotonic()-started>420:raise TimeoutError('Match exceeded420wallseconds')
    time.sleep(1)
   if (request('/api/workbench/status').get('deployment') or {}).get('id')!=owned_deployment:raise RuntimeError('Deployment changed before result export')
   export=request('/api/admin/export');path=out/f'match-{i+1:03}.json';path.write_text(json.dumps(export,ensure_ascii=False))
   record['results'].append(dict(**j,status=status,identity=export['identity'],code_sha256=result['deployment']['code_sha256'],model_sha256=result['deployment']['weights_sha256'],result=str(path),scores={r['slot']:r['score'] for r in export['state']['robots']}));save()
  record['status']='completed'
 except BaseException as e:record.update(status='failed',error=type(e).__name__+': '+str(e));raise
 finally:
  try:
   if owned_deployment and (request('/api/workbench/status').get('deployment') or {}).get('id')==owned_deployment:request('/api/development/stop',{})
  finally:save()
if __name__=='__main__':main()
