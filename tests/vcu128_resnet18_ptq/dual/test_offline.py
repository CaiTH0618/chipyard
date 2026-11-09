#!/usr/bin/env python3
"""No-device rejection tests for run identity, ownership, output and graph ABI."""
import argparse
import copy
import struct
from host_resnet18_dual import load_plan,verify_snapshot,check_tensor
from protocol import *
from compare_runs import compare_reports
p=argparse.ArgumentParser();p.add_argument('--elf',required=True);p.add_argument('--manifest',required=True)
a=p.parse_args();plan=load_plan(a.elf,a.manifest);n=plan['manifest']['task_count']
def rejects(fn):
    try:fn()
    except (ValueError,RuntimeError):return
    raise AssertionError('Invalid result accepted')
def build(mode,swap):
    run_id=0x123456789abcdef0;cfg=config(run_id,mode,swap,n,90000000000);s=bytearray(initial_shared(cfg));r=bytearray(n*128)
    for off in (0x80,0xc0,0x100,0x140,0x180,0x1c0,0x200):struct.pack_into('<Q',s,off,run_id)
    struct.pack_into('<Q',s,0x240,100000)
    for h in (0,1):
        count=sum(owner(i,mode,swap)==h for i in range(n))
        values=dict(state=DONE,hart=h,run_id=run_id,mode=mode,tasks_done=count,verified_tasks=count,local_cycles=10000,operator_cycles=count*1000,bss_start=plan['symbols']['__bss_start'],bss_end=plan['symbols']['__bss_end'],image_end=plan['symbols']['__image_end'])
        struct.pack_into('<32Q',s,STATUS_OFFSET+256*h,*[values.get(k,0) for k in STATUS_FIELDS],*([0]*13))
    for i,sample in enumerate(plan['model']['samples']):
        c=plan['model']['cases'][sample['first_stage']+29];g=plan['blob'][c['golden']:c['golden']+c['bytes']]
        v=dict(task_id=sample['task_id'],owner=owner(i,mode,swap),computed=1,verified=1,cycles=1200,operator_cycles=1000,checksum=checksum(g),first_stage=(1<<64)-1,first_byte=(1<<64)-1,guard_ok=1,run_id=run_id,output_address=plan['symbols']['dual_arenas']+i*plan['manifest']['arena_stride']+c['output_offset'],output_bytes=4000)
        struct.pack_into('<16Q',r,i*128,*[v.get(k,0) for k in TASK_FIELDS],0,0)
    return s,r,cfg
for mode,swap in ((0,0),(1,0),(1,1)):
    s,r,cfg=build(mode,swap);verify_snapshot(s,r,cfg,plan)
    for off in (0x80,0xc0,0x100,0x140,0x180,0x1c0,0x200,STATUS_OFFSET,STATUS_OFFSET+32):
        bad=bytearray(s);struct.pack_into('<Q',bad,off,struct.unpack_from('<Q',s,off)[0]^1);rejects(lambda:verify_snapshot(bad,r,cfg,plan))
    for word in (0,1,2,3,6,7,8,9,10,11,12,13):
        bad=bytearray(r);struct.pack_into('<Q',bad,8*word,struct.unpack_from('<Q',r,8*word)[0]^1);rejects(lambda:verify_snapshot(s,bad,cfg,plan))
for c in plan['model']['cases']:
    gold=plan['blob'][c['golden']:c['golden']+c['bytes']];check_tensor(c,gold,gold)
    bad=bytearray(gold);bad[len(bad)//2]^=1;rejects(lambda:check_tensor(c,bytes(bad),gold))
# Check fair comparison rejects stale/changed input and mismatched full logits.
base=dict(status='PASS',mode='serial',swap=False,elf_sha256='e',manifest_sha256='m',quant_manifest_sha256='q',source_full_manifest_sha256='f',sample_count=1,soc_mhz=None,makespan_cycles=200,images_per_million_cycles=5000,predictions=[dict(task_id=0,input_sha256='i',source_sha256='s',logits_int32=[0]*1000)])
dual=copy.deepcopy(base);dual.update(mode='dual',makespan_cycles=100)
assert compare_reports(base,dual)['speedup']==2
bad=copy.deepcopy(dual);bad['predictions'][0]['logits_int32'][3]=1;rejects(lambda:compare_reports(base,bad))
bad=copy.deepcopy(dual);bad['elf_sha256']='other';rejects(lambda:compare_reports(base,bad))
print(f'OFFLINE DUAL CHECK PASS: serial/dual/swapped snapshots, {plan["manifest"]["stage_count"]} tensor comparisons; identity/epoch/owner/error/checksum/address/logit mutations rejected')
