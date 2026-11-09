#!/usr/bin/env python3
"""Export fixed ResNet18 graph: live predecessor pointers, immutable W/input/golden.
No golden intermediate tensor is ever supplied as an operator input.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import numpy as np


def sha(data): return hashlib.sha256(data).hexdigest()
def digest(p): return sha(Path(p).read_bytes())
def bits(f): return struct.unpack('<I', struct.pack('<f', f))[0]
def require(ok, message):
    if not ok: raise ValueError(message)
def product(shape): return int(np.prod(shape))
def align64(n): return (n + 63) // 64 * 64


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-root', type=Path, required=True)
    parser.add_argument('--samples', default='all', help='all or comma-separated task IDs')
    parser.add_argument('--out', type=Path, default=Path('generated'))
    args = parser.parse_args()
    root = args.model_root.resolve(); out = args.out.resolve()
    qdir = root/'artifacts/quant/int8'; gdir = root/'artifacts/golden/resnet18'
    qm = json.loads((qdir/'quant_manifest.json').read_text())
    sm = json.loads((gdir/'samples_manifest.json').read_text())
    require(sm['quant_manifest_sha256'] == digest(qdir/'quant_manifest.json'), 'Quant/golden identity differs')
    for item in qm['files']:
        p = qdir/item['path']; require(p.stat().st_size == item['bytes'] and digest(p) == item['sha256'], 'Changed model artifact: '+item['path'])
    require(digest(root/'scripts/resnet18_integer_ops.py') == qm['provenance']['scripts']['resnet18_integer_ops.py'], 'Integer contract script changed')
    plan = json.loads((qdir/'calibration_plan_snapshot.json').read_text())
    ops = {op['module']: op for op in plan['operators']}
    blocks = {b['block']: b for b in plan['residual_blocks']}
    expected_modules = {'conv1', 'fc'}
    for st in range(1, 5):
        for bi in range(2):
            expected_modules.update(f'layer{st}.{bi}.conv{j}' for j in (1, 2))
            if st > 1 and bi == 0: expected_modules.add(f'layer{st}.{bi}.downsample.0')
    require(set(ops) == expected_modules and len(plan['operators']) == 21 and len(blocks) == 8, 'Expected fixed ResNet18: 20 Conv, 1 FC, 8 blocks')
    selected = list(sm['samples'])
    if args.samples != 'all':
        ids = [int(t) for t in args.samples.split(',')]
        require(len(ids) == len(set(ids)) and ids, 'Duplicate/empty task IDs')
        table = {int(s['task_id']): s for s in selected}
        require(set(ids) <= set(table), 'Unknown task ID')
        selected = [table[i] for i in ids]
    require(selected, 'No selected samples')
    categories = json.loads((qdir/'categories.json').read_text())
    require(len(categories) == 1000, 'Expected 1000 categories')
    weights = np.load(qdir/'quantized_weights.npz', allow_pickle=False)
    blob = bytearray(); arrays = []; shared = {}; tensors = {}; stages = []; samples = []; arena_bytes = 0

    def pack(label, x, role, dedup=None):
        x = np.ascontiguousarray(x)
        if x.dtype.itemsize == 4: x = x.astype('<i4')
        data = x.tobytes()
        if dedup in shared and dedup is not None: return shared[dedup]
        offset = align64(len(blob)); blob.extend(bytes(offset-len(blob))); blob.extend(data)
        arrays.append(dict(name=label, offset=offset, bytes=len(data), shape=list(x.shape), dtype=x.dtype.str, role=role, sha256=sha(data)))
        if dedup is not None: shared[dedup] = offset
        return offset

    def reserve(name, x):
        nonlocal arena_bytes
        if name in tensors:
            t = tensors[name]; require(t['shape'] == list(x.shape) and t['bytes'] == x.nbytes, 'Sample graph shapes differ'); return t
        offset = align64(arena_bytes) + 64
        t = dict(node=name, offset=offset, bytes=x.nbytes, shape=list(x.shape), dtype='int32_le' if x.dtype.itemsize == 4 else 'int8')
        arena_bytes = align64(offset + x.nbytes + 64); tensors[name] = t
        return t

    for sample in selected:
        task = int(sample['task_id']); sd = gdir/sample['golden_directory']
        require(json.loads((sd/'sample_manifest.json').read_text()) == sample, 'Sample manifest differs from aggregate manifest')
        nodes = {n['node']: n for n in sample['nodes']}; values = {}
        for name, n in nodes.items():
            data = (sd/n['bin']).read_bytes()
            require(len(data) == n['bytes'] and sha(data) == n['sha256'], 'Changed golden: '+name)
            dtype = np.int8 if n['dtype'] == 'int8' else np.dtype('<i4')
            x = np.frombuffer(data, dtype=dtype).reshape(n['shape'])
            require(x.nbytes == len(data), 'Golden shape/bytes differ')
            values[name] = x
        require(values['input'].shape == (1,224,224,3), 'Expected batch1 NHWC 224x224 RGB')
        inp = pack(f'task{task}.input', values['input'], 'input')
        available = {'input': {'kind':'blob','offset':inp,'node':'input'}}
        first = len(stages)

        def ref(name):
            require(name in available, 'Graph uses unavailable predecessor: '+name)
            return dict(available[name])

        def emit(name, op, inputs, cfg, module=None):
            y = values[name]; t = reserve(name, y)
            for v in inputs.values():
                if v is not None and v['kind'] == 'arena':
                    require(v['offset'] != t['offset'], 'Input/output overlap')
            c = dict(name=f'task{task}/{name}', node=name, task_id=task, op=op, dtype=t['dtype'], shape=t['shape'], bytes=t['bytes'], output_offset=t['offset'], **cfg)
            if module: c['module'] = module
            c.update({k:inputs.get(k) for k in ('a','b','d')})
            c['golden'] = pack(c['name']+'.golden', y, 'golden')
            stages.append(c); available[name] = dict(kind='arena',offset=t['offset'],node=name)

        def conv(module, input_node, output_node, fused=False):
            op = ops[module]; x = values[input_node]; y = values[output_node]
            w0 = weights[module+'.weight']; bias = weights[module+'.bias']
            require(w0.dtype == np.int8 and bias.dtype == np.int32, 'Expected INT8 W and INT32 bias')
            require(op['op'] == 'conv2d' and op['weight_layout'] == 'OIHW', 'Expected Conv OIHW')
            require(op['kernel'][0] == op['kernel'][1] and op['stride'][0] == op['stride'][1] and op['padding'][0] == op['padding'][1], 'Only symmetric square Conv supported')
            w = w0.transpose(2,3,1,0).copy(); kh,kw,ic,oc = w.shape
            require(tuple(op['kernel']) == (kh,kw) and x.shape[-1] == ic and bias.shape == (oc,), 'Conv parameter dimensions differ')
            stride = int(op['stride'][0]); padding = int(op['padding'][0])
            oh=(x.shape[1]+2*padding-kh)//stride+1; ow=(x.shape[2]+2*padding-kw)//stride+1
            expected_shape=(1,(oh+1)//2,(ow+1)//2,oc) if fused else (1,oh,ow,oc)
            require(y.shape == expected_shape and y.dtype == np.int8, 'Conv output shape differs: '+module)
            inputs=dict(a=ref(input_node), b=dict(kind='blob',offset=pack(module+'.W',w,'weight',module+'.W')), d=dict(kind='blob',offset=pack(module+'.bias',bias,'bias',module+'.bias')))
            cfg=dict(in_h=x.shape[1],in_w=x.shape[2],in_c=ic,out_c=oc,out_h=oh,out_w=ow,stride=stride,padding=padding,kernel=kh,relu=int(op['relu']),scale_bits=bits(op['gemmini_output_scale_fp32']),a_scale_bits=bits(1),pool_size=3 if fused else 1,pool_stride=2 if fused else 0,pool_padding=1 if fused else 0)
            emit(output_node,2,inputs,cfg,module)

        conv('conv1','input','stem.pool',True)
        previous='stem.pool'
        for st in range(1,5):
            for bi in range(2):
                prefix=f'layer{st}.{bi}'; block=blocks[prefix]; saved=previous
                require(block['projection'] == (st>1 and bi==0), 'Unexpected projection')
                require(block['main_requant_scale_fp32'] == 1 and block['add_output_scale_fp32'] == 1 and block['relu'], 'Unsupported residual contract')
                conv(prefix+'.conv1',saved,prefix+'.conv1_relu')
                conv(prefix+'.conv2',prefix+'.conv1_relu',prefix+'.main_preadd')
                if block['projection']:
                    conv(prefix+'.downsample.0',saved,prefix+'.skip_projection'); saved=prefix+'.skip_projection'
                main=prefix+'.main_preadd'; result=prefix+'.add_relu'
                require(values[saved].shape == values[main].shape == values[result].shape, 'Residual branch shape differs')
                emit(result,3,dict(a=ref(saved),b=ref(main)),dict(I=product(values[main].shape[:-1]),J=values[main].shape[-1],relu=1,scale_bits=bits(1),a_scale_bits=bits(block['skip_requant_scale_fp32'])))
                previous=result
        require(values[previous].shape == (1,7,7,512) and values['avgpool'].shape == (1,512), 'Expected GAP7x7x512')
        require(plan['global_average']['count'] == 49, 'Expected 49-element global average')
        emit('avgpool',4,dict(a=ref(previous)),dict(in_h=7,in_c=512,scale_bits=bits(1/49),a_scale_bits=bits(1)))
        fc=ops['fc'];w=weights['fc.weight'].T.copy();b=weights['fc.bias']
        require(w.shape == (512,1000) and w.dtype == np.int8 and b.shape == (1000,) and b.dtype == np.int32 and values['logits'].shape == (1,1000), 'FC dimensions differ')
        require(fc['gemmini_full_C'] and fc['output_dtype'] == 'int32' and not fc['relu'], 'FC contract differs')
        emit('logits',1,dict(a=ref('avgpool'),b=dict(kind='blob',offset=pack('fc.W',w,'weight','fc.W')),d=dict(kind='blob',offset=pack('fc.bias',b,'bias','fc.bias'))),dict(I=1,J=1000,K=512,relu=0,full_C=1,scale_bits=bits(1),a_scale_bits=bits(1)),'fc')
        require(len(stages)-first == 30, 'Expected 30 chained checkpoints')
        logits=values['logits'].reshape(-1);top1=int(logits.argmax())
        require(top1 == sample['int8_top1'], 'Golden Top1 disagrees with manifest')
        samples.append(dict(task_id=task,source_path=sample['source_path'],source_sha256=sample['source_sha256'],sample_manifest_sha256=digest(sd/'sample_manifest.json'),first_stage=first,stage_count=30,input_offset=inp,input_bytes=values['input'].nbytes,fp32_top1=sample['fp32_top1'],int8_top1=top1,int8_top5=sample['int8_top5']))
    weights.close();out.mkdir(parents=True,exist_ok=True)
    manifest=dict(abi=2,model='resnet18',execution='chained-live-tensors; fused stem conv+pool; independent debug buffers',source_quant_manifest_sha256=digest(qdir/'quant_manifest.json'),source_samples_manifest_sha256=digest(gdir/'samples_manifest.json'),count=len(stages),sample_count=len(samples),samples=samples,categories=categories,blob_sha256=sha(blob),blob_bytes=len(blob),arena_bytes=arena_bytes,max_output_bytes=max(t['bytes'] for t in tensors.values()),buffers=list(tensors.values()),arrays=arrays,cases=stages)
    (out/'full_blob.bin').write_bytes(blob);(out/'full_manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    signature=bytes.fromhex(digest(out/'full_manifest.json'))
    lines=['#include "full.h"','const uint8_t full_bundle_digest[32] __attribute__((used,section(".rodata.build_info")))={'+','.join(map(str,signature))+'};','const struct qcase full_cases[]={']
    fields='bytes op I J K in_h in_w in_c out_c out_h out_w stride padding kernel relu pool_size pool_stride pool_padding full_C scale_bits a_scale_bits'.split()
    for c in stages:
        items=[]
        for key in ('a','b','d'):
            v=c[key];expr='0' if v is None else ('full_blob' if v['kind']=='blob' else 'full_output')+'+'+str(v['offset'])
            items.append('.'+key+'='+expr)
        items.append('.golden=full_blob+'+str(c['golden']))
        items += [f'.{k}={c.get(k,0)}u' for k in fields]
        lines.append('{'+','.join(items)+'},')
    lines += ['};','const uint32_t full_output_offsets[]={'+','.join(str(c['output_offset']) for c in stages)+'};','const struct full_buffer full_buffers[]={'+','.join('{'+str(t['offset'])+','+str(t['bytes'])+'}' for t in tensors.values())+'};',f'const uint32_t full_buffer_count={len(tensors)};']
    (out/'full_cases.c').write_text('\n'.join(lines)+'\n')
    (out/'full_generated.h').write_text(f'#define FULL_COUNT {len(stages)}\n#define FULL_ARENA_BYTES {arena_bytes}\n')
    (out/'full_blob.S').write_text('.section .rodata.full_blob,"a",@progbits\n.balign 64\n.globl full_blob\nfull_blob:\n.incbin "generated/full_blob.bin"\n.globl full_blob_end\nfull_blob_end:\n.section .note.GNU-stack,"",@progbits\n')
    print(f'FULL GRAPH EXPORT PASS: {len(samples)} samples; {len(stages)} chained checkpoints; blob={len(blob):,}; arena={arena_bytes:,} bytes')
    print('All predecessor pointers refer to live arena tensors or original input; golden tensors are comparison-only')

if __name__=='__main__': main()
