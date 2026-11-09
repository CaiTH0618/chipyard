#!/usr/bin/env python3
"""Board qops loader: the existing CPU/FBus XDMA path and one-shot START protocol."""
import argparse,hashlib,json,mmap,os,secrets,struct,time
from pathlib import Path
from protocol import *
from xdma_transport import DMA,compare

def sha(data):return hashlib.sha256(data).digest()
def load_plan(elf_path,manifest_path):
 from elftools.elf.elffile import ELFFile
 path=Path(elf_path).resolve();mp=Path(manifest_path).resolve()
 manifest=json.loads(mp.read_text());blob=(mp.parent/'qops_blob.bin').read_bytes()
 if sha(blob).hex()!=manifest['blob_sha256'] or len(blob)!=manifest['blob_bytes']:raise ValueError('fixture blob changed')
 if manifest['abi']!=1 or manifest['count']!=len(manifest['cases']):raise ValueError('bad fixture ABI/count')
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
  symbols={n:sym(n) for n in ('_start','__image_end','__bss_start','__bss_end','_stack_top','_stack_bottom','qops_blob','qops_blob_end','qops_bundle_digest','qops_output','qops_build_info')}
  def read_address(a,n):
   for addr,off,fs,ms,flags in segments:
    if addr<=a and a+n<=addr+fs:source.seek(off+a-addr);return source.read(n)
   raise ValueError('metadata not file-backed')
  info=struct.unpack('<16Q',read_address(symbols['qops_build_info'],128));hart=info[2]
  expected=(BUILD_MAGIC,1,hart,manifest['count'],manifest['max_output_bytes'],APP_BASE,APP_END,
            STACK_BOTTOM,STACK_TOP,RESULT_BASE,LOG_BASE,LOG_SIZE,256,8,8,32)
  if hart not in (0,1) or info!=expected:raise ValueError('qops build ABI mismatch')
  if read_address(symbols['qops_bundle_digest'],32)!=sha(mp.read_bytes()):raise ValueError('ELF/manifest signature differs; rebuild')
  if symbols['qops_blob_end']-symbols['qops_blob']!=len(blob) or read_address(symbols['qops_blob'],len(blob))!=blob:raise ValueError('ELF/fixture bytes differ')
  if symbols['_start']!=APP_BASE or (symbols['_stack_bottom'],symbols['_stack_top'])!=(STACK_BOTTOM,STACK_TOP):raise ValueError('startup/stack mismatch')
  if not APP_BASE<=symbols['__bss_start']<=symbols['__bss_end']==symbols['__image_end']<=APP_END:raise ValueError('BSS symbols mismatch')
  if not symbols['__bss_start']<=symbols['qops_output']<symbols['qops_output']+manifest['max_output_bytes']+128<=symbols['__bss_end']:raise ValueError('output workspace mismatch')
  if max(a+m for a,o,f,m,flags in segments)>symbols['__image_end']:raise ValueError('segment past image end')
 return {'path':path,'manifest':manifest,'blob':blob,'hart':hart,'segments':segments,'symbols':symbols}

def verify_case(plan,index,header,data,prefix,suffix):
 c=plan['manifest']['cases'][index];expected=plan['blob'][c['golden']:c['golden']+c['bytes']]
 if header['hart']!=plan['hart'] or header['stage']!=index+1 or header['sequence']!=index+1:raise ValueError('wrong hart/case sequence')
 if header['cases_done']!=index or header['output_address']!=plan['symbols']['qops_output']+64 or header['output_bytes']!=c['bytes']:raise ValueError('output descriptor mismatch')
 compare(prefix,bytes([0xc3])*64,'output prefix guard');compare(suffix,bytes([0x3c])*64,'output suffix guard')
 compare(data,expected,c['name'])
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
   start=time.monotonic();checked=0;log_pos=0
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
     n=plan['manifest']['cases'][checked]['bytes'];out=plan['symbols']['qops_output']+64
     data=dma.read(out,n)
     c=verify_case(plan,checked,h,data,dma.read(out-64,64),dma.read(out+n,64))
     print(f"PASS hart{plan['hart']} {checked+1}/{plan['manifest']['count']} {c['name']}: bytes={n:,} cycles={h['cycles']}",flush=True)
     checked+=1;dma.write(RESULT_BASE+136,struct.pack('<Q',checked))
    if h['state']==DONE:
     if checked!=plan['manifest']['count'] or h['cases_done']!=checked or h['hart']!=plan['hart']:raise RuntimeError('incomplete terminal result')
     for field,symbol in [('image_end','__image_end'),('bss_start','__bss_start'),('bss_end','__bss_end')]:
      if h[field]!=plan['symbols'][symbol]:raise RuntimeError('runtime linker symbols differ')
     guards(dma,False)
     print(f"PASS RESNET18 QOPS HART{plan['hart']}: {checked} cases; Host compared every output byte and guards",flush=True)
     return
    if time.monotonic()-start>args.timeout_sec:raise TimeoutError('qops timeout; last header='+repr(h))
    time.sleep(args.poll_sec)
 finally:
  for fd in fds:os.close(fd)

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--elf',type=Path)
 p.add_argument('--manifest',type=Path,default=Path('generated/qops_manifest.json'))
 p.add_argument('--hart',type=int,choices=[0,1]);p.add_argument('--dry-run',action='store_true');p.add_argument('--probe-only',action='store_true')
 p.add_argument('--h2c',default='/dev/xdma0_h2c_0');p.add_argument('--c2h',default='/dev/xdma0_c2h_0');p.add_argument('--user',default='/dev/xdma0_user')
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
