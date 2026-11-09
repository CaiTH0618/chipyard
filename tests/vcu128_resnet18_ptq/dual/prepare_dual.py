#!/usr/bin/env python3
"""Bind a board-validated single-core export to disjoint per-task dual arenas."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
from graph_validation import validate_graph

def sha(data):return hashlib.sha256(data).hexdigest()
def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--full-generated',type=Path,required=True)
    p.add_argument('--out',type=Path,default=Path('generated'))
    a=p.parse_args();src=a.full_generated.resolve();out=a.out.resolve()
    mb=(src/'full_manifest.json').read_bytes();m=json.loads(mb);blob=(src/'full_blob.bin').read_bytes()
    if len(blob)!=m['blob_bytes'] or sha(blob)!=m['blob_sha256']:raise ValueError('Full export blob changed')
    validate_graph(m,blob)
    n=m['sample_count']
    if n<2 or n>16 or n%2:raise ValueError('Select an even number of 2..16 samples in the full export; default four images are supported')
    stride=m['arena_bytes'];total=n*stride
    if total+len(blob)>220*1024*1024:raise ValueError('Export exceeds conservative APP memory budget')
    manifest=dict(abi=1,source_full_manifest_sha256=sha(mb),model=m,task_count=n,arena_stride=stride,arena_bytes=total,stage_count=m['count'],buffer_count=len(m['buffers']),assignment='serial: all tasks hart0; dual: owner=(task_index%2)^swap')
    out.mkdir(parents=True,exist_ok=True)
    (out/'dual_blob.bin').write_bytes(blob)
    (out/'dual_manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    digest=bytes.fromhex(sha((out/'dual_manifest.json').read_bytes()))
    lines=['#include "dual.h"','const uint8_t dual_bundle_digest[32] __attribute__((used,section(".rodata.build_info")))={'+','.join(map(str,digest))+'};','const struct qcase dual_stages[]={']
    fields='bytes op I J K in_h in_w in_c out_c out_h out_w stride padding kernel relu pool_size pool_stride pool_padding full_C scale_bits a_scale_bits'.split()
    offsets=[]
    for task_index,sample in enumerate(m['samples']):
        base=task_index*stride
        for c in m['cases'][sample['first_stage']:sample['first_stage']+30]:
            parts=[]
            for k in ('a','b','d'):
                ref=c[k]
                if ref is None:expr='0'
                elif ref['kind']=='blob':expr='dual_blob+'+str(ref['offset'])
                else:expr='dual_arenas+'+str(base+ref['offset'])
                parts.append('.'+k+'='+expr)
            parts.append('.golden=dual_blob+'+str(c['golden']))
            parts.extend('.'+k+'='+str(c.get(k,0))+'u' for k in fields)
            lines.append('{'+','.join(parts)+'},')
            offsets.append(base+c['output_offset'])
    lines += ['};','const uint32_t dual_output_offsets[]={'+','.join(map(str,offsets))+'};','const struct task dual_tasks[]={'+','.join('{'+','.join(map(str,(s['task_id'],s['first_stage'],30,i*stride)))+'}' for i,s in enumerate(m['samples']))+'};','const struct buffer dual_buffers[]={'+','.join('{'+str(b['offset'])+','+str(b['bytes'])+'}' for b in m['buffers'])+'};']
    (out/'dual_graph.c').write_text('\n'.join(lines)+'\n')
    (out/'dual_generated.h').write_text(f'#define DUAL_TASK_COUNT {n}\n#define DUAL_STAGE_COUNT {m["count"]}\n#define DUAL_BUFFER_COUNT {len(m["buffers"])}\n#define DUAL_ARENA_STRIDE {stride}\n#define DUAL_ARENA_BYTES {total}\n')
    (out/'dual_blob.S').write_text('.section .rodata.dual_blob,"a",@progbits\n.balign 64\n.globl dual_blob\ndual_blob:\n.incbin "generated/dual_blob.bin"\n.globl dual_blob_end\ndual_blob_end:\n.section .note.GNU-stack,"",@progbits\n')
    print(f'DUAL GRAPH EXPORT PASS: {n} tasks, {m["count"]} stages, {total:,} private arena bytes; one shared immutable blob')

if __name__=='__main__':main()
