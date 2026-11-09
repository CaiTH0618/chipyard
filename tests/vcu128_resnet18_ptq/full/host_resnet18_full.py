#!/usr/bin/env python3
"""Board full loader: the existing CPU/FBus XDMA path and one-shot START protocol."""
import argparse,hashlib,json,mmap,os,secrets,struct,time
from pathlib import Path
from protocol import *
from xdma_transport import DMA,compare

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

def load_plan(elf_path,manifest_path):
 from elftools.elf.elffile import ELFFile
 path=Path(elf_path).resolve();mp=Path(manifest_path).resolve()
 manifest=json.loads(mp.read_text());blob=(mp.parent/'full_blob.bin').read_bytes()
 if sha(blob).hex()!=manifest['blob_sha256'] or len(blob)!=manifest['blob_bytes']:raise ValueError('fixture blob changed')
 validate_graph(manifest, blob)
 for a in manifest['arrays']:
  data=blob[a['offset']:a['offset']+a['bytes']]
  if len(data)!=a['bytes'] or sha(data).hex()!=a['sha256'] or a['offset']%64:raise ValueError('fixture array mismatch')
 with path.open('rb') as source:
  elf=ELFFile(source)
  if elf.elfclass!=64 or not elf.little_endian or elf['e_machine']!='EM_RISCV' or elf['e_type']!='ET_EXEC':raise ValueError('expected RV64 executable')
  if int(elf['e_entry'])!=APP_BASE or int(elf['e_flags'])&6!=4:raise ValueError('entry/lp64d ABI mismatch')
  segments=[]
  for seg in elf.iter_segments():
   if seg['p_type']!='PT_LOAD' or not seg['p_memsz']:continue
   a,o,f,m,flags=map(int,(seg['p_paddr'],seg['p_offset'],seg['p_filesz'],seg['p_memsz'],seg['p_flags']))
   if not APP_BASE<=a<a+m<=APP_END or not 0<=f<=m or a!=int(seg['p_vaddr']) or o+f>path.stat().st_size:raise ValueError('bad PT_LOAD range')
   if any(a<s[0]+s[3] and s[0]<a+m for s in segments):raise ValueError('overlapping PT_LOAD')
   segments.append((a,o,f,m,flags))
  if not any(a<=APP_BASE<a+f and flags&1 for a,o,f,m,flags in segments):raise ValueError('entry not executable')
  table=elf.get_section_by_name('.symtab')
  def sym(name):
   matches=[s for s in (table.get_symbol_by_name(name) or []) if s['st_shndx']!='SHN_UNDEF']
   if len(matches)!=1:raise ValueError('missing/ambiguous symbol: '+name)
   return int(matches[0]['st_value'])
  symbols={n:sym(n) for n in ('_start','__image_end','__bss_start','__bss_end','_stack_top','_stack_bottom','full_blob','full_blob_end','full_bundle_digest','full_output','full_build_info','full_cases','full_output_offsets','full_buffers','full_buffer_count')}
  def read_address(a,n):
   for addr,off,fs,ms,flags in segments:
    if addr<=a and a+n<=addr+fs:source.seek(off+a-addr);return source.read(n)
   raise ValueError('metadata not file-backed')
  info=struct.unpack('<16Q',read_address(symbols['full_build_info'],128));hart=info[2]
  expected=(BUILD_MAGIC,2,hart,manifest['count'],manifest['arena_bytes'],APP_BASE,APP_END,
            STACK_BOTTOM,STACK_TOP,RESULT_BASE,LOG_BASE,LOG_SIZE,256,8,8,32)
  if hart not in (0,1) or info!=expected:raise ValueError('full build ABI mismatch')
  if read_address(symbols['full_bundle_digest'],32)!=sha(mp.read_bytes()):raise ValueError('ELF/manifest signature differs; rebuild')
  if symbols['full_blob_end']-symbols['full_blob']!=len(blob) or read_address(symbols['full_blob'],len(blob))!=blob:raise ValueError('ELF/fixture bytes differ')
  if symbols['_start']!=APP_BASE or (symbols['_stack_bottom'],symbols['_stack_top'])!=(STACK_BOTTOM,STACK_TOP):raise ValueError('startup/stack mismatch')
  if not APP_BASE<=symbols['__bss_start']<=symbols['__bss_end']==symbols['__image_end']<=APP_END:raise ValueError('BSS symbols mismatch')
  if not symbols['__bss_start']<=symbols['full_output']<symbols['full_output']+manifest['arena_bytes']<=symbols['__bss_end']:raise ValueError('output workspace mismatch')
  fields='bytes op I J K in_h in_w in_c out_c out_h out_w stride padding kernel relu pool_size pool_stride pool_padding full_C scale_bits a_scale_bits'.split()
  for i,c in enumerate(manifest['cases']):
   refs=[0 if c[k] is None else symbols['full_blob' if c[k]['kind']=='blob' else 'full_output']+c[k]['offset'] for k in ('a','b','d')]
   expected_case=struct.pack('<4Q21I4x',*refs,symbols['full_blob']+c['golden'],*[c.get(k,0) for k in fields])
   if read_address(symbols['full_cases']+i*120,120)!=expected_case:raise ValueError('ELF graph descriptor mismatch at stage '+str(i))
  offsets=struct.pack('<'+'I'*manifest['count'],*[c['output_offset'] for c in manifest['cases']])
  if read_address(symbols['full_output_offsets'],len(offsets))!=offsets:raise ValueError('ELF output offsets mismatch')
  buffers=b''.join(struct.pack('<II',b['offset'],b['bytes']) for b in manifest['buffers'])
  if read_address(symbols['full_buffers'],len(buffers))!=buffers or read_address(symbols['full_buffer_count'],4)!=struct.pack('<I',len(manifest['buffers'])):raise ValueError('ELF buffer descriptors mismatch')
  if max(a+m for a,o,f,m,flags in segments)>symbols['__image_end']:raise ValueError('segment past image end')
 return {'path':path,'manifest':manifest,'blob':blob,'hart':hart,'segments':segments,'symbols':symbols}

def verify_case(plan,index,header,data,prefix,suffix):
 c=plan['manifest']['cases'][index];expected=plan['blob'][c['golden']:c['golden']+c['bytes']]
 if header['hart']!=plan['hart'] or header['stage']!=index+1 or header['sequence']!=index+1:raise ValueError('wrong hart/case sequence')
 if header['cases_done']!=index or header['output_address']!=plan['symbols']['full_output']+c['output_offset'] or header['output_bytes']!=c['bytes']:raise ValueError('output descriptor mismatch')
 compare(prefix,bytes([0xc3])*64,'output prefix guard');compare(suffix,bytes([0x3c])*64,'output suffix guard')
 if data!=expected:
  first=next((i for i,(a,b) in enumerate(zip(data,expected)) if a!=b),min(len(data),len(expected)))
  signed=lambda v:v if v<128 else v-256
  if c['dtype']=='int32_le' and len(data)==len(expected):
   j=first//4;actual_value=struct.unpack_from('<i',data,j*4)[0];expected_value=struct.unpack_from('<i',expected,j*4)[0]
  else:
   j=first;actual_value=signed(data[first]) if first<len(data) else None;expected_value=signed(expected[first]) if first<len(expected) else None
  raise RuntimeError(f"{c['name']}: element {j}, actual={actual_value}, expected={expected_value}; first byte={first}; output={header['output_address']:#x}")
 if header['mismatches'] or header['first_bad']!=0xffffffffffffffff:raise ValueError('target comparison disagrees with Host')
 if header['checksum']!=checksum(data):raise ValueError('target checksum disagrees with Host')
 return c

def probe(dma,run_id):
 addresses=[APP_BASE,0x84000000,0x88000000,0x8c000000,APP_END,STACK_BOTTOM,RESULT_BASE,HBM_END-64]
 patterns=[bytes((i*37+j*13+(run_id&255))&255 for j in range(64)) for i in range(len(addresses))]
 for a,p in zip(addresses,patterns):dma.write(a,p)
 for a,p in zip(addresses,patterns):compare(dma.read(a,64),p,f'address {a:#x}')
 print('Address probe PASS: low/high addresses and alias checks',flush=True)

def guards(dma,install):
 for i,a in enumerate((APP_END,STACK_BOTTOM,RESULT_BASE+RESULT_REGION,LOG_BASE-64,LOG_BASE+LOG_SIZE)):
  p=bytes([0xc1+i])*64
  if install:dma.write(a,p)
  compare(dma.read(a,64),p,'HBM guard')

def run_board(args,plan):
 fds=[]
 try:
  for p,flags in ((args.h2c,os.O_RDWR),(args.c2h,os.O_RDWR),(args.user,os.O_RDWR|os.O_SYNC)):fds.append(os.open(p,flags))
  dma=DMA(fds[0],fds[1]);run_id=secrets.randbits(64) or 1
  with mmap.mmap(fds[2],4096,flags=mmap.MAP_SHARED,prot=mmap.PROT_READ|mmap.PROT_WRITE) as bar:
   def reg(o):return struct.unpack_from('<I',bar,o)[0]
   if reg(0)!=0x534f4301:raise RuntimeError('wrong BAR controller ID')
   deadline=time.monotonic()+10
   while not reg(8)&2:
    if time.monotonic()>deadline:raise TimeoutError('infrastructure not ready')
    time.sleep(.1)
   if reg(8)&1:raise RuntimeError('one-shot START already used; perform the previously verified full-path reset first')
   probe(dma,run_id)
   if args.probe_only:print('PROBE ONLY PASS: START not issued');return
   with plan['path'].open('rb') as source:
    for address,offset,fs,ms,flags in plan['segments']:
     source.seek(offset)
     for position in range(0,fs,65536):
      data=source.read(min(65536,fs-position));dma.write(address+position,data)
      compare(dma.read(address+position,len(data)),data,'ELF readback')
   initial=initial_result(run_id,plan['hart'],plan['manifest']['count'])
   dma.write(RESULT_BASE,initial);compare(dma.read(RESULT_BASE,RESULT_REGION),initial,'result preparation')
   dma.write(LOG_BASE,bytes(LOG_SIZE));guards(dma,True)
   struct.pack_into('<I',bar,0x0c,1)
   if not reg(8)&1:raise RuntimeError('START not accepted')
   start=time.monotonic();checked=0;log_pos=0;records=[];predictions=[]
   while True:
    h=parse_header(dma.read(RESULT_BASE,256))
    if h['magic']!=MAGIC or h['run_id']!=run_id:raise RuntimeError('stale/corrupt result identity')
    if h['log_write']>log_pos:
     total=h['log_write'];log_pos=max(log_pos,total-LOG_SIZE)
     while log_pos<total:
      at=log_pos%LOG_SIZE;count=min(total-log_pos,LOG_SIZE-at)
      print(dma.read(LOG_BASE+at,count).decode(errors='replace'),end='',flush=True);log_pos+=count
    if h['state']==ERROR:raise RuntimeError(f"target ERROR={h['error']} stage={h['stage']} mcause={h['mcause']:#x} mepc={h['mepc']:#x} mtval={h['mtval']:#x}")
    if h['state']==WAIT_HOST and h['sequence']==checked+1:
     if checked>=plan['manifest']['count']:raise RuntimeError('too many case results')
     n=plan['manifest']['cases'][checked]['bytes'];out=plan['symbols']['full_output']+plan['manifest']['cases'][checked]['output_offset']
     data=dma.read(out,n)
     c=verify_case(plan,checked,h,data,dma.read(out-64,64),dma.read(out+n,64))
     print(f"PASS hart{plan['hart']} {checked+1}/{plan['manifest']['count']} {c['name']}: bytes={n:,} cycles={h['cycles']}",flush=True)
     records.append({'task_id':c['task_id'],'node':c['node'],'bytes':n,'cycles':h['cycles'],'exact_match':True})
     if c['node']=='logits':
      logits=struct.unpack('<1000i',data)
      top5=sorted(range(1000),key=lambda j:(-logits[j],j))[:5]
      prediction={'task_id':c['task_id'],'top5':[{'index':j,'category':plan['manifest']['categories'][j],'score_int32':logits[j]} for j in top5], 'logits_int32':list(logits)}
      predictions.append(prediction)
      print('TOP1 task='+str(c['task_id'])+' class='+str(top5[0])+' '+plan['manifest']['categories'][top5[0]],flush=True)
      # Every tensor for this sample remains live in a disjoint arena slice.
      for prior in [x for x in plan['manifest']['cases'] if x['task_id']==c['task_id']]:
       addr=plan['symbols']['full_output']+prior['output_offset']
       compare(dma.read(addr,prior['bytes']),plan['blob'][prior['golden']:prior['golden']+prior['bytes']],prior['name']+' retained tensor')
       compare(dma.read(addr-64,64),bytes([0xc3])*64,'retained prefix guard')
       compare(dma.read(addr+prior['bytes'],64),bytes([0x3c])*64,'retained suffix guard')
     checked+=1;dma.write(RESULT_BASE+136,struct.pack('<Q',checked))
    if h['state']==DONE:
     if checked!=plan['manifest']['count'] or h['cases_done']!=checked or h['hart']!=plan['hart']:raise RuntimeError('incomplete terminal result')
     for field,symbol in [('image_end','__image_end'),('bss_start','__bss_start'),('bss_end','__bss_end')]:
      if h[field]!=plan['symbols'][symbol]:raise RuntimeError('runtime linker symbols differ')
     guards(dma,False)
     # Detect accidental writes into any input, weight, bias or golden bytes.
     for offset in range(0,len(plan['blob']),65536):
      compare(dma.read(plan['symbols']['full_blob']+offset,min(65536,len(plan['blob'])-offset)),plan['blob'][offset:offset+65536],'immutable blob final readback')
     report={'status':'PASS','hart':plan['hart'],'run_id':run_id,'elf_sha256':sha(plan['path'].read_bytes()).hex(),'manifest_sha256':sha(args.manifest.read_bytes()).hex(),'quant_manifest_sha256':plan['manifest']['source_quant_manifest_sha256'],'sample_count':plan['manifest']['sample_count'],'samples':[{**s,'input_sha256':sha(plan['blob'][s['input_offset']:s['input_offset']+s['input_bytes']]).hex()} for s in plan['manifest']['samples']],'checkpoints':records,'predictions':predictions,'immutable_blob_readback_checked':True,'sum_operator_cycles':sum(r['cycles'] for r in records),'host_elapsed_seconds':time.monotonic()-start,'timing_note':'Debug validation includes per-stage Host handshakes; sum_operator_cycles is not continuous inference makespan.'}
     rp=args.report or Path('full_results_hart'+str(plan['hart'])+'.json')
     rp.parent.mkdir(parents=True,exist_ok=True);rp.write_text(json.dumps(report,indent=2)+'\n')
     print('Report saved:',rp)
     print(f"PASS RESNET18 FULL HART{plan['hart']}: {plan['manifest']['sample_count']} samples, {checked} chained checkpoints; all INT32 logits exact",flush=True)
     return
    if time.monotonic()-start>args.timeout_sec:raise TimeoutError('full timeout; last header='+repr(h))
    time.sleep(args.poll_sec)
 finally:
  for fd in fds:os.close(fd)

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--elf',type=Path)
 p.add_argument('--manifest',type=Path,default=Path('generated/full_manifest.json'))
 p.add_argument('--hart',type=int,choices=[0,1]);p.add_argument('--dry-run',action='store_true');p.add_argument('--probe-only',action='store_true')
 p.add_argument('--h2c',default='/dev/xdma0_h2c_0');p.add_argument('--c2h',default='/dev/xdma0_c2h_0');p.add_argument('--user',default='/dev/xdma0_user')
 p.add_argument('--report',type=Path,help='Successful run JSON with per-stage cycles and complete INT32 logits')
 p.add_argument('--timeout-sec',type=float,default=1800);p.add_argument('--poll-sec',type=float,default=.1)
 a=p.parse_args()
 if a.timeout_sec<=0 or a.poll_sec<=0:p.error('timeouts must be positive')
 if a.probe_only:
  if a.dry_run:p.error('--probe-only and --dry-run are exclusive')
  run_board(a,None);return
 if not a.elf:p.error('--elf is required')
 plan=load_plan(a.elf,a.manifest)
 if a.hart is not None and a.hart!=plan['hart']:p.error('--hart differs from ELF')
 print(f"ELF hart={plan['hart']} cases={plan['manifest']['count']} image_end={plan['symbols']['__image_end']:#x}")
 if a.dry_run:print('DRY RUN PASS: RV64 ABI, layout, metadata and fixture bytes; no device access');return
 run_board(a,plan)

if __name__=='__main__':main()
