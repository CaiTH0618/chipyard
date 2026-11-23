"""Deterministic lifetime plan. Does not execute quantized operators."""
import hashlib
import math
from source_graph_validation import validate_graph

FIELDS='bytes op I J K in_h in_w in_c out_c out_h out_w stride padding kernel relu pool_size pool_stride pool_padding full_C scale_bits a_scale_bits'.split()
def sha(data):return hashlib.sha256(data).hexdigest()
def align64(n):return (n+63)//64*64

def make_layout(model):
    stages=model['cases'];samples=model['samples']
    if not 1<=len(samples)<=16:raise ValueError('Expected 1..16 tasks')
    slot_bytes=max(c['bytes'] for c in stages if c['node']!='logits')
    frame=align64(slot_bytes+128);final_bytes=4000;final_frame=align64(final_bytes+128)
    plans=[];peaks=[]
    for sample in samples:
        first=sample['first_stage'];cs=stages[first:first+30]
        if sample['stage_count']!=30 or cs[-1]['node']!='logits' or cs[-1]['bytes']!=final_bytes:raise ValueError('Expected complete ResNet18 and 1000 INT32 logits')
        last={c['node']:i for i,c in enumerate(cs)};known={};parents=[]
        for i,c in enumerate(cs):
            if c['bytes']!=math.prod(c['shape'])*(4 if c['dtype']=='int32_le' else 1):raise ValueError('Shape/bytes disagree')
            ps=[]
            for key in ('a','b','d'):
                r=c[key]
                if r and r['kind']=='arena':
                    if r['node'] not in known:raise ValueError('Unavailable predecessor')
                    if r['offset']!=known[r['node']][1]:raise ValueError('Wrong predecessor offset')
                    last[r['node']]=i;ps.append(first+known[r['node']][0])
                else:ps.append(-1)
            known[c['node']]=(i,c['output_offset']);parents.append(ps)
        free=[0,1,2];slots={};live={};peak=0
        for i,c in enumerate(cs):
            refs=[]
            for key in ('a','b','d'):
                r=c[key];refs.append(slots[r['node']] if r and r['kind']=='arena' else -1)
            if c['node']=='logits':out=-2
            else:
                if not free:raise ValueError('Three slots insufficient at '+c['node'])
                out=free.pop(0)
                if out in refs:raise ValueError('Input/output overlap')
                slots[c['node']]=out;live[c['node']]=c['bytes']
            peak=max(peak,sum(live.values()))
            plans.append(dict(node=c['node'],producer=i+1,last_use=last[c['node']]+1,refs=refs,out=out,parents=parents[i],bytes=c['bytes']))
            # Inputs and output overlap in time at the producing stage. Release
            # occurs only after the operation's completion, never before allocation.
            for n in list(live):
                if last[n]==i:free.append(slots[n]);del live[n]
        peaks.append(peak)
    return dict(slot_count=3,slot_bytes=slot_bytes,slot_frame_bytes=frame,
                hart_work_bytes=3*frame,work_bytes=2*3*frame,
                final_bytes=final_bytes,final_frame_bytes=final_frame,
                output_bytes=len(samples)*final_frame,peak_live_bytes=peaks,stages=plans)

def validate_bundle(manifest,blob):
    if manifest.get('abi')!=3 or manifest.get('storage')!='HBM_or_ActiveSPM_three_slots_per_hart':raise ValueError('Wrong baseline ABI/storage')
    m=manifest['model'];validate_graph(m,blob)
    if manifest.get('spm')!={'bases':[0x70000000,0x70100000],'bytes':1048576,'controls':[0x10050000,0x10051000]}:raise ValueError('Unexpected SPM map')
    if manifest['layout']!=make_layout(m):raise ValueError('Lifetime/slot plan differs')
    if manifest['layout']['hart_work_bytes']>1048576:raise ValueError('SPM capacity exceeded')
    if manifest.get('layout_sha256')!=sha(__import__('json').dumps(manifest['layout'],sort_keys=True).encode()):raise ValueError('Layout hash differs')
    if manifest['task_count']!=m['sample_count'] or manifest['stage_count']!=m['count']:raise ValueError('Count mismatch')
    if len(blob)!=m['blob_bytes'] or sha(blob)!=m['blob_sha256']:raise ValueError('Blob changed')

def build_info(m):
    from protocol import BUILD_MAGIC,ABI,APP_BASE,APP_END,STACK0_BOTTOM,STACK0_TOP,STACK1_BOTTOM,STACK1_TOP,SHARED_BASE,SHARED_BYTES,STATUS_OFFSET,LOG0_BASE,LOG1_BASE,LOG_SIZE,MAX_REPEATS
    l=m['layout']
    return (BUILD_MAGIC,ABI,APP_BASE,APP_END,STACK0_BOTTOM,STACK0_TOP,STACK1_BOTTOM,STACK1_TOP,
            SHARED_BASE,SHARED_BYTES,STATUS_OFFSET,256,LOG0_BASE,LOG1_BASE,LOG_SIZE,
            m['task_count'],m['stage_count'],3,l['hart_work_bytes'],l['work_bytes'],120,128,16,128,
            l['slot_bytes'],l['slot_frame_bytes'],l['final_frame_bytes'],l['output_bytes'],16,MAX_REPEATS,0x248,l['final_bytes'])
