#!/usr/bin/env python3
"""Run one serial or dual batch; verify retained live tensors after both finish."""
import argparse
import hashlib
import json
import mmap
import os
from pathlib import Path
import secrets
import struct
import time
from graph_validation import validate_graph
from protocol import *
from xdma_transport import DMA,compare

def sha(data):return hashlib.sha256(data).hexdigest()
def load_plan(elf_path,manifest_path):
    from elftools.elf.elffile import ELFFile
    ep=Path(elf_path).resolve();mp=Path(manifest_path).resolve()
    m=json.loads(mp.read_text());model=m['model'];blob=(mp.parent/'dual_blob.bin').read_bytes()
    if m['abi']!=1 or m['task_count']!=model['sample_count'] or m['stage_count']!=model['count'] or m['buffer_count']!=30:raise ValueError('Dual export ABI/count mismatch')
    if m['task_count']<2 or m['task_count']>16 or m['task_count']%2:raise ValueError('Expected even task count')
    if m['arena_stride']!=model['arena_bytes'] or m['arena_bytes']!=m['task_count']*m['arena_stride']:raise ValueError('Bad private arena layout')
    if len(blob)!=model['blob_bytes'] or sha(blob)!=model['blob_sha256']:raise ValueError('Model blob changed')
    validate_graph(model,blob)
    with ep.open('rb') as source:
        elf=ELFFile(source)
        if elf.elfclass!=64 or not elf.little_endian or elf['e_machine']!='EM_RISCV' or elf['e_type']!='ET_EXEC' or elf['e_entry']!=APP_BASE or int(elf['e_flags'])&6!=4:raise ValueError('Expected RV64 lp64d executable')
        segments=[]
        for seg in elf.iter_segments():
            if seg['p_type']!='PT_LOAD' or not seg['p_memsz']:continue
            addr,off,fs,ms,flags=map(int,(seg['p_paddr'],seg['p_offset'],seg['p_filesz'],seg['p_memsz'],seg['p_flags']))
            if not APP_BASE<=addr<addr+ms<=APP_END or not 0<=fs<=ms or addr!=seg['p_vaddr'] or off+fs>ep.stat().st_size:raise ValueError('Bad PT_LOAD range')
            if any(addr<a+n and a<addr+ms for a,o,f,n,fl in segments):raise ValueError('Overlapping segments')
            segments.append((addr,off,fs,ms,flags))
        if not any(a<=APP_BASE<a+f and fl&1 for a,o,f,n,fl in segments):raise ValueError('Entry is not executable')
        table=elf.get_section_by_name('.symtab')
        def sym(name):
            values=[s for s in (table.get_symbol_by_name(name) or []) if s['st_shndx']!='SHN_UNDEF']
            if len(values)!=1:raise ValueError('Missing/ambiguous symbol '+name)
            return int(values[0]['st_value'])
        names='_start __bss_start __bss_end __image_end _stack0_bottom _stack0_top _stack1_bottom _stack1_top dual_blob dual_blob_end dual_bundle_digest dual_build_info dual_arenas dual_results dual_stages dual_tasks dual_buffers dual_output_offsets'.split()
        sy={name:sym(name) for name in names}
        def read(addr,n):
            for a,o,f,ms,fl in segments:
                if a<=addr and addr+n<=a+f:source.seek(o+addr-a);return source.read(n)
            raise ValueError('Metadata is not file-backed')
        expected=(BUILD_MAGIC,ABI,APP_BASE,APP_END,STACK0_BOTTOM,STACK0_TOP,STACK1_BOTTOM,STACK1_TOP,SHARED_BASE,SHARED_BYTES,STATUS_OFFSET,256,LOG0_BASE,LOG1_BASE,LOG_SIZE,m['task_count'],m['stage_count'],m['buffer_count'],m['arena_stride'],m['arena_bytes'],120,128,16,128)
        if read(sy['dual_build_info'],192)!=struct.pack('<24Q',*expected):raise ValueError('Build ABI/layout mismatch')
        if read(sy['dual_bundle_digest'],32)!=bytes.fromhex(sha(mp.read_bytes())):raise ValueError('ELF/manifest binding mismatch; rebuild')
        if sy['dual_blob_end']-sy['dual_blob']!=len(blob) or read(sy['dual_blob'],len(blob))!=blob:raise ValueError('ELF/model bytes differ')
        if sy['_start']!=APP_BASE or (sy['_stack0_bottom'],sy['_stack0_top'],sy['_stack1_bottom'],sy['_stack1_top'])!=(STACK0_BOTTOM,STACK0_TOP,STACK1_BOTTOM,STACK1_TOP):raise ValueError('Startup/stack symbols differ')
        if not APP_BASE<=sy['__bss_start']<=sy['__bss_end']==sy['__image_end']<=APP_END:raise ValueError('BSS bounds invalid')
        ranges=[(sy['dual_arenas'],m['arena_bytes']),(sy['dual_results'],m['task_count']*128)]
        for a,n in ranges:
            if a%64 or not sy['__bss_start']<=a<a+n<=sy['__bss_end']:raise ValueError('Mutable workspace outside BSS')
        if ranges[0][0]<ranges[1][0]+ranges[1][1] and ranges[1][0]<ranges[0][0]+ranges[0][1]:raise ValueError('Results overlap arena')
        fields='bytes op I J K in_h in_w in_c out_c out_h out_w stride padding kernel relu pool_size pool_stride pool_padding full_C scale_bits a_scale_bits'.split()
        offsets=[]
        for ti,sample in enumerate(model['samples']):
            base=ti*m['arena_stride']
            task=struct.pack('<4I',sample['task_id'],sample['first_stage'],30,base)
            if read(sy['dual_tasks']+ti*16,16)!=task:raise ValueError('Task table differs')
            for index in range(sample['first_stage'],sample['first_stage']+30):
                c=model['cases'][index];ptr=[]
                for k in ('a','b','d'):
                    r=c[k]
                    ptr.append(0 if r is None else sy['dual_blob']+r['offset'] if r['kind']=='blob' else sy['dual_arenas']+base+r['offset'])
                descriptor=struct.pack('<4Q21I4x',*ptr,sy['dual_blob']+c['golden'],*[c.get(k,0) for k in fields])
                if read(sy['dual_stages']+index*120,120)!=descriptor:raise ValueError('Private graph pointer mismatch at stage '+str(index))
                offsets.append(base+c['output_offset'])
        packed=struct.pack('<'+'I'*len(offsets),*offsets)
        if read(sy['dual_output_offsets'],len(packed))!=packed:raise ValueError('Output offsets differ')
        buffers=b''.join(struct.pack('<II',b['offset'],b['bytes']) for b in model['buffers'])
        if read(sy['dual_buffers'],len(buffers))!=buffers:raise ValueError('Guard descriptors differ')
        if max(a+n for a,o,f,n,fl in segments)>sy['__image_end']:raise ValueError('Segment past image end')
    return dict(path=ep,manifest_path=mp,manifest=m,model=model,blob=blob,symbols=sy,segments=segments)

def verify_snapshot(shared,records,cfg,plan):
    if len(shared)!=SHARED_BYTES or len(records)!=128*plan['manifest']['task_count']:raise ValueError('Snapshot size mismatch')
    if shared[:128]!=cfg:raise ValueError('Configuration changed or stale run')
    _,_,run_id,mode,swap,count,wait,*_=struct.unpack('<16Q',cfg)
    for off in (0x80,0xc0,0x100,0x140,0x180,0x1c0,0x200):
        if struct.unpack_from('<Q',shared,off)[0]!=run_id:raise ValueError('Bootstrap/ready/GO/DONE epoch mismatch')
    makespan=struct.unpack_from('<Q',shared,0x240)[0]
    if not makespan:raise ValueError('Missing coordinated makespan')
    statuses=[parse_status(shared[STATUS_OFFSET+256*h:STATUS_OFFSET+256*(h+1)]) for h in (0,1)]
    for h,s in enumerate(statuses):
        expected=sum(owner(i,mode,swap)==h for i in range(count))
        if (s['state'],s['hart'],s['run_id'],s['mode'],s['error'],s['tasks_done'],s['verified_tasks'],s['mismatches'])!=(DONE,h,run_id,mode,0,expected,expected,0):raise ValueError('Incomplete/wrong-owner/stale/error status: '+str(s))
        if any(s[k] for k in ('mcause','mepc','mtval')):raise ValueError('Unexpected trap metadata')
        for field,symbol in [('bss_start','__bss_start'),('bss_end','__bss_end'),('image_end','__image_end')]:
            if s[field]!=plan['symbols'][symbol]:raise ValueError('Runtime linker symbols differ')
    results=[]
    for i,sample in enumerate(plan['model']['samples']):
        r=parse_task(records[i*128:(i+1)*128]);h=owner(i,mode,swap)
        last=plan['model']['cases'][sample['first_stage']+29]
        address=plan['symbols']['dual_arenas']+i*plan['manifest']['arena_stride']+last['output_offset']
        if (r['task_id'],r['owner'],r['computed'],r['verified'],r['mismatches'],r['first_stage'],r['first_byte'],r['guard_ok'],r['run_id'],r['output_address'],r['output_bytes'])!=(sample['task_id'],h,1,1,0,(1<<64)-1,(1<<64)-1,1,run_id,address,4000):raise ValueError('Task identity/output/check metadata differs: '+str(r))
        expected=plan['blob'][last['golden']:last['golden']+4000]
        if r['checksum']!=checksum(expected) or not 0<r['operator_cycles']<=r['cycles']:raise ValueError('Task checksum/timing differs')
        results.append(r)
    for h,s in enumerate(statuses):
        if s['operator_cycles']!=sum(r['operator_cycles'] for r in results if r['owner']==h):raise ValueError('Per-hart operator cycles differ')
    return statuses,results,makespan

def check_tensor(c,data,expected):
    if data!=expected:
        j=next((i for i,(x,y) in enumerate(zip(data,expected)) if x!=y),min(len(data),len(expected)))
        if c['dtype']=='int32_le' and len(data)==len(expected):
            at=j//4;actual=struct.unpack_from('<i',data,4*at)[0];gold=struct.unpack_from('<i',expected,4*at)[0]
        else:
            at=j;actual=data[j] if j<len(data) else None;gold=expected[j] if j<len(expected) else None
            actual=actual if actual is None or actual<128 else actual-256;gold=gold if gold is None or gold<128 else gold-256
        raise RuntimeError(f"{c['name']}: element {at}, actual={actual}, expected={gold}")

def guard_addresses(plan):
    return (APP_END,STACK0_BOTTOM-64,STACK0_TOP,STACK1_BOTTOM-64,SHARED_BASE+SHARED_BYTES,LOG0_BASE-64,LOG0_BASE+LOG_SIZE,LOG1_BASE+LOG_SIZE)
def outer_guards(dma,plan,install):
    for i,addr in enumerate(guard_addresses(plan)):
        pattern=bytes([0xc0+i])*64
        if install:dma.write(addr,pattern)
        compare(dma.read(addr,64),pattern,'outer guard')
def run_board(args,plan):
    fds=[]
    try:
        for path,flags in ((args.h2c,os.O_RDWR),(args.c2h,os.O_RDWR),(args.user,os.O_RDWR|os.O_SYNC)):fds.append(os.open(path,flags))
        dma=DMA(fds[0],fds[1]);epoch=secrets.randbits(64) or 1
        cfg=config(epoch,MODES[args.mode],int(args.swap),plan['manifest']['task_count'],args.wait_cycles)
        with mmap.mmap(fds[2],4096,flags=mmap.MAP_SHARED,prot=mmap.PROT_READ|mmap.PROT_WRITE) as bar:
            def reg(off):return struct.unpack_from('<I',bar,off)[0]
            if reg(0)!=0x534f4301:raise RuntimeError('Wrong controller ID')
            deadline=time.monotonic()+10
            while not reg(8)&2:
                if time.monotonic()>deadline:raise TimeoutError('Infrastructure not ready')
                time.sleep(.1)
            if reg(8)&1:raise RuntimeError('One-shot START already used; perform the previously verified full-path reset')
            # Same board-validated CPU/FBus path and alias probe, before ELF load.
            addresses=[APP_BASE,0x84000000,0x88000000,0x8c000000,APP_END,STACK0_BOTTOM,STACK1_BOTTOM,SHARED_BASE,HBM_END-64]
            patterns=[bytes((i*37+j*13+(epoch&255))&255 for j in range(64)) for i in range(len(addresses))]
            for addr,data in zip(addresses,patterns):dma.write(addr,data)
            for addr,data in zip(addresses,patterns):compare(dma.read(addr,64),data,'CPU/FBus address probe')
            print('Address probe PASS',flush=True)
            with plan['path'].open('rb') as source:
                for addr,off,fs,ms,flags in plan['segments']:
                    source.seek(off)
                    for position in range(0,fs,65536):
                        data=source.read(min(65536,fs-position));dma.write(addr+position,data);compare(dma.read(addr+position,len(data)),data,'ELF readback')
            initial=initial_shared(cfg);dma.write(SHARED_BASE,initial);compare(dma.read(SHARED_BASE,SHARED_BYTES),initial,'shared preparation')
            dma.write(LOG0_BASE,bytes(LOG_SIZE));dma.write(LOG1_BASE,bytes(LOG_SIZE));outer_guards(dma,plan,True)
            struct.pack_into('<I',bar,0x0c,1)
            if not reg(8)&1:raise RuntimeError('START not accepted')
            begin=time.monotonic();last=[None,None];logpos=[0,0]
            while True:
                snapshot=dma.read(SHARED_BASE,SHARED_BYTES)
                if snapshot[:128]!=cfg:raise RuntimeError('Shared config changed')
                statuses=[parse_status(snapshot[STATUS_OFFSET+256*h:STATUS_OFFSET+256*(h+1)]) for h in (0,1)]
                for h,s in enumerate(statuses):
                    if s['run_id']!=epoch or s['hart']!=h:raise RuntimeError('Stale hart status')
                    if s['log_write']>logpos[h]:
                        total=s['log_write'];logpos[h]=max(logpos[h],total-LOG_SIZE)
                        while logpos[h]<total:
                            at=logpos[h]%LOG_SIZE;n=min(total-logpos[h],LOG_SIZE-at)
                            print(f'[hart{h}] '+dma.read((LOG0_BASE if h==0 else LOG1_BASE)+at,n).decode(errors='replace'),end='',flush=True);logpos[h]+=n
                    if s['state']==ERROR:raise RuntimeError('Target error on hart'+str(h)+': '+repr(s))
                    key=(s['task_index'],s['stage'],s['tasks_done'],s['state'])
                    if key!=last[h]:print(f"hart{h}: task_index={s['task_index']} stage={s['stage']}/30 computed={s['tasks_done']} state={s['state']}",flush=True);last[h]=key
                if all(s['state']==DONE for s in statuses):break
                if time.monotonic()-begin>args.timeout_sec:raise TimeoutError('Dual batch timeout; statuses='+repr(statuses))
                time.sleep(args.poll_sec)
            record_bytes=dma.read(plan['symbols']['dual_results'],128*plan['manifest']['task_count'])
            statuses,results,makespan=verify_snapshot(snapshot,record_bytes,cfg,plan)
            predictions=[]
            for i,sample in enumerate(plan['model']['samples']):
                base=plan['symbols']['dual_arenas']+i*plan['manifest']['arena_stride']
                for c in plan['model']['cases'][sample['first_stage']:sample['first_stage']+30]:
                    addr=base+c['output_offset'];data=dma.read(addr,c['bytes']);gold=plan['blob'][c['golden']:c['golden']+c['bytes']]
                    check_tensor(c,data,gold);compare(dma.read(addr-64,64),bytes([0xc3])*64,c['name']+' prefix');compare(dma.read(addr+c['bytes'],64),bytes([0x3c])*64,c['name']+' suffix')
                    if c['node']=='logits':
                        logits=struct.unpack('<1000i',data);top5=sorted(range(1000),key=lambda j:(-logits[j],j))[:5]
                        prediction=dict(task_id=sample['task_id'],hart=results[i]['owner'],input_sha256=sha(plan['blob'][sample['input_offset']:sample['input_offset']+sample['input_bytes']]),source_path=sample['source_path'],source_sha256=sample['source_sha256'],logits_int32=list(logits),top5=[dict(index=j,category=plan['model']['categories'][j],score_int32=logits[j]) for j in top5])
                        predictions.append(prediction);print(f"TOP1 task={sample['task_id']} hart={results[i]['owner']} class={top5[0]} {plan['model']['categories'][top5[0]]}",flush=True)
                print(f"PASS task{sample['task_id']}: 30 chained tensors, guards and all 1000 INT32 logits exact",flush=True)
            outer_guards(dma,plan,False)
            for off in range(0,len(plan['blob']),65536):compare(dma.read(plan['symbols']['dual_blob']+off,min(65536,len(plan['blob'])-off)),plan['blob'][off:off+65536],'immutable model blob')
            count=plan['manifest']['task_count']
            report=dict(status='PASS',mode=args.mode,swap=bool(args.swap),run_id=epoch,elf_sha256=sha(plan['path'].read_bytes()),manifest_sha256=sha(plan['manifest_path'].read_bytes()),quant_manifest_sha256=plan['model']['source_quant_manifest_sha256'],source_full_manifest_sha256=plan['manifest']['source_full_manifest_sha256'],sample_count=count,checked_tensor_count=30*count,makespan_cycles=makespan,soc_mhz=args.soc_mhz,images_per_million_cycles=count*1e6/makespan,statuses=statuses,task_results=results,predictions=predictions,immutable_blob_readback_checked=True,host_elapsed_seconds=time.monotonic()-begin,timing_note='hart0 interval from before GO publication until both compute-DONE flags; excludes tensor checking and Host readback; includes status/scheduling overhead. Each core records its own mcycle differences.')
            if args.soc_mhz:report.update(makespan_seconds=makespan/(args.soc_mhz*1e6),images_per_second=count*args.soc_mhz*1e6/makespan)
            rp=args.report or Path('reports')/(args.mode+('_swap' if args.swap else '')+'.json');rp.parent.mkdir(parents=True,exist_ok=True);rp.write_text(json.dumps(report,indent=2)+'\n')
            print(f'PASS RESNET18 {args.mode.upper()}: {count} images, {30*count} exact chained tensors; makespan={makespan} cycles',flush=True)
            print('Report:',rp)
    finally:
        for fd in fds:os.close(fd)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--elf',type=Path,required=True);p.add_argument('--manifest',type=Path,default=Path('generated/dual_manifest.json'))
    p.add_argument('--mode',choices=['serial','dual'],default='dual');p.add_argument('--swap',action='store_true');p.add_argument('--report',type=Path)
    p.add_argument('--soc-mhz',type=float,help='Actual unchanged SoC clock, only for cycles-to-seconds conversion')
    p.add_argument('--wait-cycles',type=int,default=90000000000);p.add_argument('--timeout-sec',type=float,default=1800);p.add_argument('--poll-sec',type=float,default=.1)
    p.add_argument('--h2c',default='/dev/xdma0_h2c_0');p.add_argument('--c2h',default='/dev/xdma0_c2h_0');p.add_argument('--user',default='/dev/xdma0_user');p.add_argument('--dry-run',action='store_true')
    a=p.parse_args()
    if a.mode=='serial' and a.swap:p.error('--swap is for dual mode')
    if a.timeout_sec<=0 or a.poll_sec<=0 or not 0<a.wait_cycles<1<<63 or (a.soc_mhz is not None and a.soc_mhz<=0):p.error('Invalid timeout/frequency')
    plan=load_plan(a.elf,a.manifest);config(1,MODES[a.mode],int(a.swap),plan['manifest']['task_count'],a.wait_cycles)
    print(f"ELF dual model: {plan['manifest']['task_count']} tasks, mode={a.mode}, swap={a.swap}, image_end={plan['symbols']['__image_end']:#x}")
    if a.dry_run:print('DRY RUN PASS: RV64/dual stacks/private arenas/graph pointers/blob binding; no device access');return
    run_board(a,plan)
if __name__=='__main__':main()
