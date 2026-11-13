"""Local experiment settings. Secret values never returned in status."""
from pathlib import Path
import json,os
DEFAULTS=dict(seed=20261113,sensor_profile='mild',opponent_mode='sensor_limited',transport='http',contest_lock=True,llm_mode='practice_fixture',llm_provider='openai',llm_endpoint='',llm_model='')

def read(root):
 p=Path(root)/'data/fidelity-settings.json'
 return {**DEFAULTS,**(json.loads(p.read_text()) if p.exists() else {})}

def public(root):
 s=read(root);s['llm_key_configured']=(Path(root)/'data/llm-key.txt').is_file() or bool(os.environ.get('MAICON_LLM_KEY'))
 s['llm_live_verified']=False
 return s

def save(root,body):
 s=read(root);secret=None
 allowed={'sensor_profile':('mild','ideal','official_only','stress'),'opponent_mode':('sensor_limited','privileged'),'transport':('http','ros'),'llm_mode':('practice_fixture','provided_api'),'llm_provider':('openai','anthropic')}
 for k,v in body.items():
  if k in allowed:
   if v not in allowed[k]:raise ValueError(k+' 값이 올바르지 않습니다')
   s[k]=v
  elif k=='seed':
   if type(v) is not int or not 0<=v<2**31:raise ValueError('seed는 0~2147483647 정수')
   s[k]=v
  elif k=='contest_lock':
   if type(v) is not bool:raise ValueError('contest_lock boolean')
   s[k]=v
  elif k in ('llm_endpoint','llm_model'):
   if not isinstance(v,str) or len(v)>500:raise ValueError(k+' 문자열 필요')
   s[k]=v.strip()
  elif k=='llm_key' and v:
   if not isinstance(v,str) or len(v)>4096:raise ValueError('API Key 형식')
   secret=v.strip()
 if secret is not None:
  p=Path(root)/'data/llm-key.txt';fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
  with os.fdopen(fd,'w') as f:f.write(secret)
  p.chmod(0o600)
 p=Path(root)/'data/fidelity-settings.json';p.write_text(json.dumps(s,ensure_ascii=False,indent=2));p.chmod(0o600)
 return public(root)

def environment(root):
 s=read(root);env={f'MAICON_{k.upper()}':str(v) for k,v in s.items() if k.startswith('llm_') or k=='transport'}
 p=Path(root)/'data/llm-key.txt'
 if p.exists():env['MAICON_LLM_KEY']=p.read_text().strip()
 return env
