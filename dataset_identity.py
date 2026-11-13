"""Track source-picture identity separately from rendered/viewpoint identity."""
from collections import defaultdict

def source_audit(frames):
 groups=defaultdict(set);missing=0;splits=set()
 for f in frames:
  split=f.get('split');splits.add(split)
  group=f.get('source_group')
  if not group:missing+=1
  else:groups[group].add(split)
 shared=sorted(k for k,v in groups.items() if len(v)>1)
 independent=not missing and not shared and {'train','val','test'}<=splits
 return {'independent_source_test':independent,'shared_source_groups':shared,'missing_source_identity':missing,
         'source_group_count':len(groups),'evaluation_domain':'source-group-disjoint; real-camera status separate' if independent else 'shared or unverified source images; not novel-image validation'}
