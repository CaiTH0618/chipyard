#!/usr/bin/env python3
"""ActiveSPM functional test: Host accesses HBM only; Rocket checks SPM."""
import argparse,json,mmap,os,secrets,struct,time
from pathlib import Path
from bundle import load_plan,verify_snapshot
from protocol import *
from test_plan import sha,expected,seed,output_bytes,BUFFER_BYTES,WORK_BYTES
from xdma_transport import DMA,compare

def outer_guards(dma,install):
    addresses=(APP_END,STACK0_BOTTOM-64,STACK0_TOP,STACK1_BOTTOM-64,SHARED_BASE+SHARED_BYTES,LOG0_BASE-64,LOG0_BASE+LOG_SIZE,LOG1_BASE+LOG_SIZE)
    for i,a in enumerate(addresses):
        b=bytes([0xc0+i])*64
        if install:dma.write(a,b)
        compare(dma.read(a,64),b,'outer HBM guard')

def run_board(a,plan):
    m=plan['manifest'];fds=[];mask=MODES[a.mode];suite=SUITES[a.suite];epoch=secrets.randbits(64) or 1
    cfg=config(epoch,mask,suite,a.iterations,a.wait_cycles,m['max_iterations'])
    try:
        for path,flags in [(a.h2c,os.O_RDWR),(a.c2h,os.O_RDWR),(a.user,os.O_RDWR|os.O_SYNC)]:fds.append(os.open(path,flags))
        dma=DMA(fds[0],fds[1])
        with mmap.mmap(fds[2],4096,flags=mmap.MAP_SHARED,prot=mmap.PROT_READ|mmap.PROT_WRITE) as bar:
            def reg(off):return struct.unpack_from('<I',bar,off)[0]
            if reg(0)!=0x534f4301:raise RuntimeError('Wrong board control ID')
            deadline=time.monotonic()+10
            while not reg(8)&2:
                if time.monotonic()>deadline:raise TimeoutError('Infrastructure not ready')
                time.sleep(.1)
            if reg(8)&1:raise RuntimeError('START already used; restore the board through the previously verified full-path reset/reprogramming procedure')
            addresses=[APP_BASE,0x84000000,0x88000000,0x8c000000,APP_END,STACK0_BOTTOM,STACK1_BOTTOM,SHARED_BASE,HBM_END-64]
            patterns=[bytes((i*37+j*13+(epoch&255))&255 for j in range(64)) for i in range(len(addresses))]
            for address,b in zip(addresses,patterns):dma.write(address,b)
            for address,b in zip(addresses,patterns):compare(dma.read(address,64),b,'HBM address probe')
            print('HBM address probe PASS',flush=True)
            with plan['path'].open('rb') as stream:
                for address,off,fs,ms,flags in plan['segments']:
                    stream.seek(off)
                    for pos in range(0,fs,65536):
                        b=stream.read(min(65536,fs-pos));dma.write(address+pos,b);compare(dma.read(address+pos,len(b)),b,'ELF readback')
            initial=initial_shared(cfg);dma.write(SHARED_BASE,initial);compare(dma.read(SHARED_BASE,SHARED_BYTES),initial,'shared configuration')
            dma.write(LOG0_BASE,bytes(LOG_SIZE));dma.write(LOG1_BASE,bytes(LOG_SIZE));outer_guards(dma,True)
            struct.pack_into('<I',bar,0x0c,1)
            if not reg(8)&1:raise RuntimeError('START not accepted')
            begin=time.monotonic();last=[None,None];logpos=[0,0]
            while True:
                raw=dma.read(SHARED_BASE,SHARED_BYTES)
                if raw[:128]!=cfg:raise RuntimeError('Configuration changed')
                statuses=[parse_status(raw[STATUS_OFFSET+256*h:STATUS_OFFSET+256*(h+1)]) for h in (0,1)]
                for h,s in enumerate(statuses):
                    if s['run_id']!=epoch or s['hart']!=h:raise RuntimeError('Stale hart status')
                    total=s['log_write'];logpos[h]=max(logpos[h],total-LOG_SIZE)
                    while logpos[h]<total:
                        at=logpos[h]%LOG_SIZE;n=min(total-logpos[h],LOG_SIZE-at)
                        print('[hart'+str(h)+'] '+dma.read((LOG0_BASE if h==0 else LOG1_BASE)+at,n).decode(errors='replace'),end='',flush=True);logpos[h]+=n
                    if s['state']==ERROR:
                        name=m['cases'][s['stage']-1]['name'] if 1<=s['stage']<=m['case_count'] else 'startup/control/instance-alias probe'
                        failure=dict(status='ERROR',run_id=epoch,mode=a.mode,suite=a.suite,iterations=a.iterations,failed_hart=h,case_name=name,statuses=statuses,
                            elf_sha256=sha(plan['path'].read_bytes()),manifest_sha256=sha(plan['manifest_path'].read_bytes()),hardware=m['hardware'],eligible_for_performance_comparison=False)
                        out=a.report or Path('reports')/('activespm_failed_'+str(epoch)+'.json');out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(failure,indent=2)+'\n');print('Failure report:',out,flush=True)
                        raise RuntimeError('ActiveSPM target error on hart'+str(h)+' at '+name+': '+repr(s))
                    key=tuple(s[k] for k in ('state','task_index','stage','tasks_done','iterations_done'))
                    if key!=last[h]:
                        print(f"hart{h}: iteration={s['task_index']+1}/{a.iterations} case={s['stage']}/{m['case_count']} passed={s['tasks_done']} rounds_done={s['iterations_done']} state={s['state']}",flush=True);last[h]=key
                if all(s['state']==DONE for s in statuses):break
                if time.monotonic()-begin>a.timeout_sec:raise TimeoutError('Target timeout: '+repr(statuses))
                time.sleep(a.poll_sec)
            records=dma.read(plan['symbols']['test_records'],2*m['max_iterations']*m['case_count']*128)
            statuses,passed,controls=verify_snapshot(raw,records,cfg,plan)
            snapshots=[]
            if suite:
                index=m['case_count']-1;c=m['cases'][index];n=output_bytes(c)
                for h in (0,1):
                    if not mask&(1<<h):continue
                    address=plan['symbols']['test_work']+h*WORK_BYTES+BUFFER_BYTES+64
                    data=dma.read(address,n);gold=expected(c,seed(h,a.iterations-1,index));compare(data,gold,'independent Host final full snapshot')
                    compare(dma.read(address-64,64),b'\xc3'*64,'HBM snapshot prefix');compare(dma.read(address+n,64),b'\x3c'*64,'HBM snapshot suffix')
                    snapshots.append(dict(hart=h,name=c['name'],bytes=n,sha256=sha(data),mismatched_bytes=0))
            outer_guards(dma,False)
            # All file-backed immutable/read-only segments must remain unchanged.
            with plan['path'].open('rb') as stream:
                for address,off,fs,ms,flags in plan['segments']:
                    if flags&2:continue
                    stream.seek(off)
                    for pos in range(0,fs,65536):
                        b=stream.read(min(65536,fs-pos));compare(dma.read(address+pos,len(b)),b,'immutable ELF segment')
            report=dict(status='PASS',purpose=m['purpose'],mode=a.mode,suite=a.suite,iterations=a.iterations,
                run_id=epoch,case_count_per_iteration=sum(a.suite=='all' or c['kind']<=2 for c in m['cases']),passed_case_count=len(passed),
                hardware=m['hardware'],hardware_label=a.hardware_label,bitstream_sha256=sha(a.bitstream.read_bytes()) if a.bitstream else None,
                instance_alias_probe_passed=True,control_snapshots_before=list(controls[:14]),control_snapshots_after=list(controls[14:]),
                activespm_dma_started=False,eligible_for_performance_comparison=False,
                statuses=statuses,case_results=passed,final_hbm_snapshots=snapshots,
                host_elapsed_seconds=time.monotonic()-begin,elf_sha256=sha(plan['path'].read_bytes()),manifest_sha256=sha(plan['manifest_path'].read_bytes()),
                source_sha256=m['source_sha256'],immutable_elf_readback_checked=True,
                validation_scope='Each selected case of every iteration checked bytewise by Rocket, and actual/reference FNV hashes independently checked by Host. All-suite final snapshot also compared bytewise by Host. CPU-only runs issue no Gemmini commands. Hart0 performs small address/instance alias probes on both SPMs even in a single-hart mode.',
                timing_note='Functional diagnostics include input preparation, CPU reference/checks, guards and HBM snapshots; cycles are not inference performance.')
            out=a.report or Path('reports')/('activespm_'+a.mode+'_'+a.suite+'_'+str(epoch)+'.json');out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(report,indent=2)+'\n')
            print(f'PASS ACTIVESPM {a.mode.upper()} {a.suite.upper()}: {len(passed)} checked cases; ActiveSPM DMA untouched',flush=True)
            print('Report:',out)
    finally:
        for fd in fds:os.close(fd)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--elf',type=Path,default=Path('build/activespm_test.elf'));p.add_argument('--manifest',type=Path,default=Path('generated/test_manifest.json'))
    p.add_argument('--mode',choices=MODES,default='hart0');p.add_argument('--suite',choices=SUITES,default='cpu');p.add_argument('--iterations',type=int,default=4)
    p.add_argument('--wait-cycles',type=int,default=90000000000);p.add_argument('--timeout-sec',type=float,default=1800);p.add_argument('--poll-sec',type=float,default=.2)
    p.add_argument('--report',type=Path);p.add_argument('--hardware-label',default='L2_1MiB_plus_2x1MiB_ActiveSPM');p.add_argument('--bitstream',type=Path)
    p.add_argument('--h2c',default='/dev/xdma0_h2c_0');p.add_argument('--c2h',default='/dev/xdma0_c2h_0');p.add_argument('--user',default='/dev/xdma0_user');p.add_argument('--dry-run',action='store_true')
    a=p.parse_args();plan=load_plan(a.elf,a.manifest)
    if a.timeout_sec<=0 or a.poll_sec<=0 or a.bitstream and not a.bitstream.is_file():p.error('Invalid timeout, polling interval or bitstream path')
    try:config(1,MODES[a.mode],SUITES[a.suite],a.iterations,a.wait_cycles,plan['manifest']['max_iterations'])
    except ValueError as e:p.error(str(e))
    print(f"ActiveSPM test ELF: {plan['manifest']['case_count']} cases; mode={a.mode}; suite={a.suite}; iterations={a.iterations}")
    print('SPM0=0x70000000 SPM1=0x70100000, 1 MiB each; control MMIO read-only; Host DMA restricted to HBM')
    if a.dry_run:print('DRY RUN PASS: ELF, manifest, test table, memory bounds and ABI matched; no device access');return
    run_board(a,plan)
if __name__=='__main__':main()
