#!/usr/bin/env python3
"""Run the HBM-reusing baseline on the separately programmed 2 MiB L2 bitstream."""
import argparse,json,mmap,os,secrets,statistics,struct,time
from pathlib import Path
from bundle import load_plan,verify_snapshot
from memory_plan import sha
from protocol import *
from xdma_transport import DMA,compare

def outer_guards(dma,install):
    addresses=(APP_END,STACK0_BOTTOM-64,STACK0_TOP,STACK1_BOTTOM-64,SHARED_BASE+SHARED_BYTES,LOG0_BASE-64,LOG0_BASE+LOG_SIZE,LOG1_BASE+LOG_SIZE)
    for i,a in enumerate(addresses):
        pattern=bytes([0xc0+i])*64
        if install:dma.write(a,pattern)
        compare(dma.read(a,64),pattern,'outer guard')

def run_board(args,plan):
    fds=[];m=plan['manifest'];layout=plan['layout'];count=m['task_count']
    try:
        for path,flags in [(args.h2c,os.O_RDWR),(args.c2h,os.O_RDWR),(args.user,os.O_RDWR|os.O_SYNC)]:fds.append(os.open(path,flags))
        dma=DMA(fds[0],fds[1]);epoch=secrets.randbits(64) or 1
        cfg=config(epoch,MODES[args.mode],int(args.swap),count,args.wait_cycles,CHECKS[args.check],args.warmup,args.repeats)
        with mmap.mmap(fds[2],4096,flags=mmap.MAP_SHARED,prot=mmap.PROT_READ|mmap.PROT_WRITE) as bar:
            def reg(off):return struct.unpack_from('<I',bar,off)[0]
            if reg(0)!=0x534f4301:raise RuntimeError('Wrong board control ID')
            deadline=time.monotonic()+10
            while not reg(8)&2:
                if time.monotonic()>deadline:raise TimeoutError('Infrastructure not ready')
                time.sleep(.1)
            if reg(8)&1:raise RuntimeError('One-shot START already used: perform the previously verified full-path reset/reprogramming procedure')
            addresses=[APP_BASE,0x84000000,0x88000000,0x8c000000,APP_END,STACK0_BOTTOM,STACK1_BOTTOM,SHARED_BASE,HBM_END-64]
            patterns=[bytes((i*37+j*13+(epoch&255))&255 for j in range(64)) for i in range(len(addresses))]
            for a,data in zip(addresses,patterns):dma.write(a,data)
            for a,data in zip(addresses,patterns):compare(dma.read(a,64),data,'CPU/FBus address probe')
            print('Address probe PASS',flush=True)
            with plan['path'].open('rb') as stream:
                for a,off,fs,ms,flags in plan['segments']:
                    stream.seek(off)
                    for pos in range(0,fs,65536):
                        data=stream.read(min(65536,fs-pos));dma.write(a+pos,data);compare(dma.read(a+pos,len(data)),data,'ELF readback')
            initial=initial_shared(cfg);dma.write(SHARED_BASE,initial);compare(dma.read(SHARED_BASE,SHARED_BYTES),initial,'shared configuration')
            dma.write(LOG0_BASE,bytes(LOG_SIZE));dma.write(LOG1_BASE,bytes(LOG_SIZE));outer_guards(dma,True)
            struct.pack_into('<I',bar,0x0c,1)
            if not reg(8)&1:raise RuntimeError('START not accepted')
            begin=time.monotonic();last=[None,None];logpos=[0,0]
            while True:
                snapshot=dma.read(SHARED_BASE,SHARED_BYTES)
                if snapshot[:128]!=cfg:raise RuntimeError('Shared configuration changed')
                statuses=[parse_status(snapshot[STATUS_OFFSET+256*h:STATUS_OFFSET+256*(h+1)]) for h in [0,1]]
                for h,s in enumerate(statuses):
                    if s['run_id']!=epoch or s['hart']!=h:raise RuntimeError('Stale hart status')
                    if s['log_write']>logpos[h]:
                        total=s['log_write'];logpos[h]=max(logpos[h],total-LOG_SIZE)
                        while logpos[h]<total:
                            at=logpos[h]%LOG_SIZE;n=min(total-logpos[h],LOG_SIZE-at)
                            print('[hart'+str(h)+'] '+dma.read((LOG0_BASE if h==0 else LOG1_BASE)+at,n).decode(errors='replace'),end='',flush=True);logpos[h]+=n
                    if s['state']==ERROR:
                        name='unknown'
                        if s['task_index']<count and 1<=s['stage']<=30:
                            sample=plan['model']['samples'][s['task_index']];name=plan['model']['cases'][sample['first_stage']+s['stage']-1]['name']
                        raise RuntimeError('Target error at '+name+' on hart'+str(h)+': '+repr(s))
                    key=tuple(s[k] for k in ['task_index','stage','iterations_done','warmup_done','state'])
                    if key!=last[h]:
                        print(f"hart{h}: task={s['task_index']} stage={s['stage']}/30 warmup={s['warmup_done']}/{args.warmup} measured={s['iterations_done']}/{args.repeats} state={s['state']}",flush=True);last[h]=key
                if all(s['state']==DONE for s in statuses):break
                if time.monotonic()-begin>args.timeout_sec:raise TimeoutError('Target timeout: '+repr(statuses))
                time.sleep(args.poll_sec)
            records=dma.read(plan['symbols']['baseline_results'],count*128)
            statuses,results,times=verify_snapshot(snapshot,records,cfg,plan)
            predictions=[]
            for ti,sample in enumerate(plan['model']['samples']):
                c=plan['model']['cases'][sample['first_stage']+29];a=results[ti]['output_address']
                data=dma.read(a,4000);gold=plan['blob'][c['golden']:c['golden']+4000]
                compare(data,gold,'task'+str(sample['task_id'])+' full INT32 logits')
                compare(dma.read(a-64,64),bytes([0xc3])*64,'final prefix');compare(dma.read(a+4000,64),bytes([0x3c])*64,'final suffix')
                logits=struct.unpack('<1000i',data);top5=sorted(range(1000),key=lambda j:(-logits[j],j))[:5]
                predictions.append(dict(task_id=sample['task_id'],hart=results[ti]['owner'],source_path=sample['source_path'],source_sha256=sample['source_sha256'],logits_int32=list(logits),top5=[dict(index=j,category=plan['model']['categories'][j],score_int32=logits[j]) for j in top5]))
                print(f"TOP1 task={sample['task_id']} hart={results[ti]['owner']} class={top5[0]} {plan['model']['categories'][top5[0]]}; all 1000 logits exact",flush=True)
            for h in [0,1]:
                for slot in range(3):
                    a=plan['symbols']['baseline_work']+h*layout['hart_work_bytes']+slot*layout['slot_frame_bytes']+64
                    compare(dma.read(a-64,64),bytes([0xc3])*64,'work prefix');compare(dma.read(a+layout['slot_bytes'],64),bytes([0x3c])*64,'work outer suffix')
            outer_guards(dma,False)
            for off in range(0,len(plan['blob']),65536):
                n=min(65536,len(plan['blob'])-off);compare(dma.read(plan['symbols']['baseline_blob']+off,n),plan['blob'][off:off+n],'immutable model blob')
            median=statistics.median(times);makespan=sum(times)
            report=dict(status='PASS',abi=ABI,hardware_label=args.hardware_label,bitstream_sha256=sha(args.bitstream.read_bytes()) if args.bitstream else None,
                mode=args.mode,check=args.check,swap=args.swap,run_id=epoch,warmup=args.warmup,repeats=args.repeats,
                storage=m['storage'],backend_sha256=m['backend_sha256'],resadd_backend=m.get('resadd_backend','loop_with_small_direct'),resadd_direct_sha256=m.get('resadd_direct_sha256'),slot_count_per_hart=3,hart_work_bytes=layout['hart_work_bytes'],work_bytes=layout['work_bytes'],output_bytes=layout['output_bytes'],
                sample_count=count,measured_images=count*args.repeats,iteration_cycles=times,makespan_cycles=makespan,median_batch_cycles=median,
                debug_checked_stage_count=sum(s['checked_stages'] for s in statuses),final_outputs_checked=count,
                final_check_scope='last measured iteration; all 1000 INT32 logits/task; DEBUG checks every stage of every iteration including warmup',
                timings_include_debug_checks=args.check=='debug',eligible_for_performance_comparison=args.check=='perf',
                statuses=statuses,task_results=results,predictions=predictions,elf_sha256=sha(plan['path'].read_bytes()),manifest_sha256=sha(plan['manifest_path'].read_bytes()),
                source_full_manifest_sha256=m['source_full_manifest_sha256'],quant_manifest_sha256=plan['model']['source_quant_manifest_sha256'],
                immutable_blob_readback_checked=True,host_elapsed_seconds=time.monotonic()-begin,soc_mhz=args.soc_mhz,
                timing_note='Each batch: hart0 immediately before GO publication until both compute-DONE flags; warmup excluded. Includes scheduling, pointer resolution, fences and polling interference. Final checks/Host transfers excluded. Debug has in-interval stage checks and must not be used as performance data.',
                cache_condition='post-startup and ELF loading; then specified in-target warmup; not a guaranteed cold-cache measurement')
            if args.soc_mhz:
                hz=args.soc_mhz*1e6;report.update(makespan_seconds=makespan/hz,images_per_second=count*args.repeats*hz/makespan,median_batch_seconds=median/hz)
            rp=args.report or Path('reports')/(args.check+'_'+args.mode+('_swap' if args.swap else '')+'_'+str(epoch)+'.json')
            rp.parent.mkdir(parents=True,exist_ok=True);rp.write_text(json.dumps(report,indent=2)+'\n')
            print(f"PASS CACHE BASELINE {args.check.upper()} {args.mode.upper()}: {count} tasks; checked stages={report['debug_checked_stage_count']}; median batch={median:g} cycles",flush=True)
            print('Report:',rp)
    finally:
        for fd in fds:os.close(fd)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--elf',type=Path,required=True);p.add_argument('--manifest',type=Path,default=Path('generated/baseline_manifest.json'))
    p.add_argument('--mode',choices=MODES,default='serial');p.add_argument('--check',choices=CHECKS,default='debug');p.add_argument('--swap',action='store_true')
    p.add_argument('--warmup',type=int);p.add_argument('--repeats',type=int);p.add_argument('--report',type=Path);p.add_argument('--soc-mhz',type=float)
    p.add_argument('--hardware-label',default='cacheonly_L2_2MiB');p.add_argument('--bitstream',type=Path,help='Optional local .bit for report hash; does not program/detect board image')
    p.add_argument('--wait-cycles',type=int,default=90000000000);p.add_argument('--timeout-sec',type=float,default=1800);p.add_argument('--poll-sec',type=float)
    p.add_argument('--h2c',default='/dev/xdma0_h2c_0');p.add_argument('--c2h',default='/dev/xdma0_c2h_0');p.add_argument('--user',default='/dev/xdma0_user');p.add_argument('--dry-run',action='store_true')
    a=p.parse_args()
    if a.warmup is None:a.warmup=0 if a.check=='debug' else 1
    if a.repeats is None:a.repeats=1 if a.check=='debug' else 5
    if a.poll_sec is None:a.poll_sec=.1 if a.check=='debug' else 1.0
    if a.timeout_sec<=0 or a.poll_sec<=0 or (a.soc_mhz is not None and a.soc_mhz<=0):p.error('Invalid timeout/frequency')
    if a.bitstream and not a.bitstream.is_file():p.error('Bitstream file not found')
    plan=load_plan(a.elf,a.manifest)
    try:config(1,MODES[a.mode],int(a.swap),plan['manifest']['task_count'],a.wait_cycles,CHECKS[a.check],a.warmup,a.repeats)
    except ValueError as e:p.error(str(e))
    print(f"Baseline ELF: {plan['manifest']['task_count']} tasks; {plan['layout']['hart_work_bytes']:,} HBM work bytes/hart; check={a.check}; warmup={a.warmup}; repeats={a.repeats}")
    if a.dry_run:print('DRY RUN PASS: ELF/manifest/blob/lifetime/slot refs/ABI checked; no device access');return
    if plan['model'].get('test_fixture_only'):p.error('Synthetic test fixture cannot run on a board; prepare from the real full export')
    run_board(a,plan)
if __name__=='__main__':main()
