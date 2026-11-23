#!/usr/bin/env python3
"""Generate a new HBM-buffer-reusing graph from the frozen full export."""
import argparse,json
from pathlib import Path
from memory_plan import FIELDS,sha,make_layout,validate_bundle
from source_graph_validation import validate_graph
from weight_plan import make_weight_plan,plan_hash,PROFILES

def prepare(src,out,weight_profile='early',weight_nodes=None):
    src=Path(src).resolve();out=Path(out).resolve()
    raw=(src/'full_manifest.json').read_bytes();m=json.loads(raw)
    blob=(src/'full_blob.bin').read_bytes();validate_graph(m,blob)
    layout=make_layout(m)
    if layout['hart_work_bytes']>1048576:raise ValueError('Three slots exceed each ActiveSPM')
    if layout['work_bytes']+layout['output_bytes']+len(blob)>220*1024*1024:raise ValueError('APP budget exceeded')
    weights=make_weight_plan(m,layout,weight_profile,weight_nodes)
    result=dict(abi=4,storage='HBM_or_ActiveSPM_three_slots_per_hart',source_full_manifest_sha256=sha(raw),
                task_count=m['sample_count'],stage_count=m['count'],model=m,layout=layout,
                backend_sha256=sha((Path(__file__).parent/'model_backend.c').read_bytes()),
                runtime_sha256=sha((Path(__file__).parent/'runtime.c').read_bytes()),
                resident_weights_sha256=sha((Path(__file__).parent/'resident_weights.c').read_bytes()),
                weights=weights,weight_plan_sha256=plan_hash(weights),
                layout_sha256=sha(json.dumps(layout,sort_keys=True).encode()),
                spm=dict(bases=[0x70000000,0x70100000],bytes=1048576,controls=[0x10050000,0x10051000]),
                resadd_backend='direct_accumulator_8x8_v1',
                resadd_direct_sha256=sha((Path(__file__).parent/'resadd_direct.h').read_bytes()))
    validate_bundle(result,blob);out.mkdir(parents=True,exist_ok=True)
    (out/'baseline_blob.bin').write_bytes(blob)
    manifest=json.dumps(result,indent=2)+'\n';(out/'baseline_manifest.json').write_text(manifest)
    lines=['#include "baseline.h"',
           'const uint8_t baseline_bundle_digest[32] __attribute__((used,section(".rodata.build_info")))={'+','.join(map(str,bytes.fromhex(sha(manifest.encode()))))+'};',
           'const struct qcase baseline_stages[]={']
    for c in m['cases']:
        parts=[]
        for key in ('a','b','d'):
            r=c[key];expr='baseline_blob+'+str(r['offset']) if r and r['kind']=='blob' else '0'
            parts.append('.'+key+'='+expr)
        parts.append('.golden=baseline_blob+'+str(c['golden']))
        parts.extend('.'+k+'='+str(c.get(k,0))+'u' for k in FIELDS)
        lines.append('{'+','.join(parts)+'},')
    lines.extend(['};','const struct refs baseline_refs[]={'+','.join('{'+','.join(map(str,p['refs']+[p['out']]))+'}' for p in layout['stages'])+'};',
                  'const int32_t baseline_parents[][3] __attribute__((used,section(".rodata.build_info")))={'+','.join('{'+','.join(map(str,p['parents']))+'}' for p in layout['stages'])+'};',
                  'const struct task baseline_tasks[]={'+','.join('{'+','.join(map(str,(s['task_id'],s['first_stage'],30,i*layout['final_frame_bytes']+64)))+'}' for i,s in enumerate(m['samples']))+'};'])
    lines.append('const struct resident_weight baseline_resident_weights[]={'+','.join('{baseline_blob+'+str(e['source_offset'])+','+str(e['data_offset'])+','+str(e['bytes'])+'}' for e in weights['entries'])+'};')
    lines.append('const int32_t baseline_weight_refs[]={'+','.join(map(str,weights['stage_refs']))+'};')
    (out/'baseline_graph.c').write_text('\n'.join(lines)+'\n')
    macros=dict(BASE_TASK_COUNT=m['sample_count'],BASE_STAGE_COUNT=m['count'],BASE_SLOT_BYTES=layout['slot_bytes'],BASE_SLOT_FRAME_BYTES=layout['slot_frame_bytes'],BASE_HART_WORK_BYTES=layout['hart_work_bytes'],BASE_WORK_BYTES=layout['work_bytes'],BASE_FINAL_BYTES=4000,BASE_FINAL_FRAME_BYTES=layout['final_frame_bytes'],BASE_OUTPUT_BYTES=layout['output_bytes'])
    macros.update(BASE_WEIGHT_COUNT=len(weights['entries']),BASE_WEIGHT_BYTES=weights['payload_bytes'],BASE_WEIGHT_REGION_OFFSET=weights['region_offset'],BASE_WEIGHT_REGION_BYTES=weights['region_bytes'])
    (out/'baseline_generated.h').write_text(''.join('#define '+k+' '+str(v)+'\n' for k,v in macros.items()))
    (out/'weight_plan.json').write_text(json.dumps(weights,indent=2)+'\n')
    (out/'baseline_blob.S').write_text('.section .rodata.baseline_blob,"a",@progbits\n.balign 64\n.globl baseline_blob\nbaseline_blob:\n.incbin "generated/baseline_blob.bin"\n.globl baseline_blob_end\nbaseline_blob_end:\n.section .note.GNU-stack,"",@progbits\n')
    print(f'FULL SPM EXPORT PASS: {m["sample_count"]} tasks; {m["count"]} stages; 3 selectable HBM/SPM slots/hart; {layout["hart_work_bytes"]:,} bytes/hart; separate final logits')
    print(f'WEIGHT PLAN: {weight_profile}; {len(weights["entries"])} arrays; {weights["payload_bytes"]:,} payload bytes; total SPM occupancy with guards={weights["occupied_bytes"]:,}/{1048576:,}; free={weights["unused_bytes"]:,}')
    for e in weights['entries']:print(f'  {e["node"]}: {e["bytes"]:,} B -> SPM+0x{e["data_offset"]:x}')
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--full-generated',type=Path,required=True);p.add_argument('--out',type=Path,default=Path('generated'))
    p.add_argument('--weight-profile',choices=PROFILES,default='early')
    p.add_argument('--weight-nodes',help='Comma-separated node names; requires manual profile')
    a=p.parse_args();prepare(a.full_generated,a.out,a.weight_profile,a.weight_nodes.split(',') if a.weight_nodes else None)
