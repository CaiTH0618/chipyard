import hashlib
def sha(data):return hashlib.sha256(data).digest()
def validate_graph(m,blob):
 if m['abi']!=2 or m['count']!=len(m['cases']) or m['count']!=30*m['sample_count']:raise ValueError('bad full graph ABI/count')
 if len(m['samples'])!=m['sample_count'] or len({s['task_id'] for s in m['samples']})!=m['sample_count']:raise ValueError('bad sample IDs')
 if len(m['categories'])!=1000 or m['arena_bytes']<=0 or m['arena_bytes']%64:raise ValueError('bad categories/arena')
 buffers={b['node']:b for b in m['buffers']};ranges=[]
 if len(buffers)!=30 or len(m['buffers'])!=30:raise ValueError('expected 30 independent tensor buffers')
 for b in m['buffers']:
  lo,hi=b['offset']-64,b['offset']+b['bytes']+64
  if b['offset']%64 or not 0<=lo<hi<=m['arena_bytes']:raise ValueError('bad guarded arena slice')
  if any(lo<y and x<hi for x,y in ranges):raise ValueError('overlapping guarded arena slices')
  ranges.append((lo,hi))
 arrays={a['offset']:a for a in m['arrays']}
 if len(arrays)!=len(m['arrays']):raise ValueError('duplicate blob offsets')
 ranges=[]
 for a in m['arrays']:
  lo,hi=a['offset'],a['offset']+a['bytes']
  if lo%64 or not 0<=lo<hi<=len(blob) or any(lo<y and x<hi for x,y in ranges):raise ValueError('bad blob array range')
  if sha(blob[lo:hi]).hex()!=a['sha256']:raise ValueError('changed blob array')
  ranges.append((lo,hi))
 topology=[('stem.pool',2,'input',None)]
 previous='stem.pool'
 for st in range(1,5):
  for bi in range(2):
   prefix=f'layer{st}.{bi}';saved=previous
   topology.extend([(prefix+'.conv1_relu',2,saved,None),(prefix+'.main_preadd',2,prefix+'.conv1_relu',None)])
   if st>1 and bi==0:
    topology.append((prefix+'.skip_projection',2,saved,None));saved=prefix+'.skip_projection'
   topology.append((prefix+'.add_relu',3,saved,prefix+'.main_preadd'));previous=prefix+'.add_relu'
 topology.extend([('avgpool',4,previous,None),('logits',1,'avgpool',None)])
 for sample_index,s in enumerate(m['samples']):
  if s['first_stage']!=30*sample_index or s['stage_count']!=30:raise ValueError('sample stage boundaries differ')
  cs=m['cases'][s['first_stage']:s['first_stage']+30]
  if len(cs)!=30 or cs[0]['node']!='stem.pool' or cs[-1]['node']!='logits':raise ValueError('incomplete sample graph')
  counts={op:sum(c['op']==op for c in cs) for op in (1,2,3,4)}
  if counts!={1:1,2:20,3:8,4:1}:raise ValueError('operator counts differ')
  available={'input':{'kind':'blob','offset':s['input_offset'],'node':'input'}}
  if arrays[s['input_offset']]['role']!='input' or arrays[s['input_offset']]['bytes']!=s['input_bytes']:raise ValueError('bad model input')
  for c,(node,op,a_node,b_node) in zip(cs,topology):
   if c['node']!=node or c['op']!=op or c['a'].get('node')!=a_node or (b_node is not None and c['b'].get('node')!=b_node):raise ValueError('fixed ResNet18 topology differs')
   if c['task_id']!=s['task_id'] or c['node'] in available:raise ValueError('wrong task/duplicate graph output')
   b=buffers[c['node']]
   if (c['output_offset'],c['bytes'],c['shape'],c['dtype'])!=(b['offset'],b['bytes'],b['shape'],b['dtype']):raise ValueError('stage buffer differs')
   g=arrays[c['golden']]
   if g['role']!='golden' or g['bytes']!=c['bytes']:raise ValueError('bad golden reference')
   for key in ('a','b','d'):
    v=c[key]
    if v is None:continue
    if v['kind']=='arena':
     if v!=available.get(v['node']) or v['offset']==c['output_offset']:raise ValueError('not a live predecessor or overlapping output')
    elif v['kind']=='blob':
     if key=='a':
      if v!=available['input'] or c['node']!='stem.pool':raise ValueError('intermediate operator input must be a live tensor')
     elif arrays[v['offset']]['role']!=('weight' if key=='b' else 'bias') or arrays[v['offset']]['name']!=c['module']+('.W' if key=='b' else '.bias'):raise ValueError('operator parameter is not the correct weight/bias')
    else:raise ValueError('unknown reference kind')
   if c['op']==3 and (c['a']['kind']!='arena' or c['b']['kind']!='arena'):raise ValueError('residual inputs must both be live')
   available[c['node']]={'kind':'arena','offset':c['output_offset'],'node':c['node']}

