#!/usr/bin/env python3
"""Generate a new HBM-buffer-reusing graph from the frozen full export."""
import argparse,json
from pathlib import Path
from memory_plan import FIELDS,sha,make_layout,validate_bundle
from source_graph_validation import validate_graph

def prepare(src,out):
    src=Path(src).resolve();out=Path(out).resolve()
    raw=(src/'full_manifest.json').read_bytes();m=json.loads(raw)
    blob=(src/'full_blob.bin').read_bytes();validate_graph(m,blob)
    layout=make_layout(m)
    if layout['work_bytes']+layout['output_bytes']+len(blob)>220*1024*1024:raise ValueError('APP budget exceeded')
    result=dict(abi=2,storage='HBM_three_slots_per_hart',source_full_manifest_sha256=sha(raw),
                task_count=m['sample_count'],stage_count=m['count'],model=m,layout=layout,
                backend_sha256=sha((Path(__file__).parent/'model_backend.c').read_bytes()),
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
    (out/'baseline_graph.c').write_text('\n'.join(lines)+'\n')
    macros=dict(BASE_TASK_COUNT=m['sample_count'],BASE_STAGE_COUNT=m['count'],BASE_SLOT_BYTES=layout['slot_bytes'],BASE_SLOT_FRAME_BYTES=layout['slot_frame_bytes'],BASE_HART_WORK_BYTES=layout['hart_work_bytes'],BASE_WORK_BYTES=layout['work_bytes'],BASE_FINAL_BYTES=4000,BASE_FINAL_FRAME_BYTES=layout['final_frame_bytes'],BASE_OUTPUT_BYTES=layout['output_bytes'])
    (out/'baseline_generated.h').write_text(''.join('#define '+k+' '+str(v)+'\n' for k,v in macros.items()))
    (out/'baseline_blob.S').write_text('.section .rodata.baseline_blob,"a",@progbits\n.balign 64\n.globl baseline_blob\nbaseline_blob:\n.incbin "generated/baseline_blob.bin"\n.globl baseline_blob_end\nbaseline_blob_end:\n.section .note.GNU-stack,"",@progbits\n')
    print(f'BASELINE EXPORT PASS: {m["sample_count"]} tasks; {m["count"]} stages; 3 HBM slots/hart; {layout["hart_work_bytes"]:,} bytes/hart; separate final logits')
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--full-generated',type=Path,required=True);p.add_argument('--out',type=Path,default=Path('generated'))
    a=p.parse_args();prepare(a.full_generated,a.out)
