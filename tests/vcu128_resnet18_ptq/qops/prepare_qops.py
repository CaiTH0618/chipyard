#!/usr/bin/env python3
"""Build fixed qops fixtures from the user's integer model and saved golden tensors."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import sys
import numpy as np

def digest(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def bits(f): return struct.unpack('<I',struct.pack('<f',f))[0]
def require(ok,msg):
    if not ok: raise RuntimeError(msg)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--model-root',type=Path,required=True)
    p.add_argument('--sample',type=int,default=0)
    p.add_argument('--out',type=Path,default=Path('generated'))
    a=p.parse_args();root=a.model_root.resolve();out=a.out.resolve();out.mkdir(parents=True,exist_ok=True)
    qdir=root/'artifacts/quant/int8';gdir=root/'artifacts/golden/resnet18'
    qm=json.loads((qdir/'quant_manifest.json').read_text())
    sm=json.loads((gdir/'samples_manifest.json').read_text())
    require(sm['quant_manifest_sha256']==digest(qdir/'quant_manifest.json'),'quant/golden manifests differ')
    for f in qm['files']:require(digest(qdir/f['path'])==f['sha256'],'parameter artifact changed: '+f['path'])
    require(digest(root/'scripts/resnet18_integer_ops.py')==qm['provenance']['scripts']['resnet18_integer_ops.py'],
            'Integer reference implementation changed')
    sys.path.insert(0,str(root/'scripts'))
    from resnet18_integer_ops import conv_acc64,requant8,residual_add,global_average7,linear_acc64,maxpool3_stride2
    samples=[s for s in sm['samples'] if s['task_id']==a.sample]
    require(len(samples)==1,'Unknown sample')
    sample=samples[0];sd=gdir/sample['golden_directory'];nodes={n['node']:n for n in sample['nodes']}
    def node(name):
        n=nodes[name];path=sd/n['bin'];require(digest(path)==n['sha256'],'golden changed: '+name)
        dtype=np.int8 if n['dtype']=='int8' else np.dtype('<i4')
        return np.frombuffer(path.read_bytes(),dtype=dtype).reshape(n['shape']).copy()
    plan=json.loads((qdir/'calibration_plan_snapshot.json').read_text())
    ops={x['module']:x for x in plan['operators']};blocks={x['block']:x for x in plan['residual_blocks']}
    z=np.load(qdir/'quantized_weights.npz',allow_pickle=False)
    blob=bytearray();arrays=[];cache={};cases=[]
    def array(label,x):
        x=np.ascontiguousarray(x)
        if x.dtype.itemsize==4:x=x.astype('<i4')
        data=x.tobytes();key=(data,x.dtype.str,tuple(x.shape))
        if key in cache:return cache[key]
        blob.extend(b'\0'*((-len(blob))%64));off=len(blob);blob.extend(data);cache[key]=off
        arrays.append({'name':label,'offset':off,'bytes':len(data),'shape':list(x.shape),'dtype':x.dtype.str,
                       'sha256':hashlib.sha256(data).hexdigest()})
        return off
    def add(name,kind,inputs,golden,scale=1.0,a_scale=1.0,**cfg):
        c={'name':name,'op':kind,'scale_bits':bits(scale),'a_scale_bits':bits(a_scale),
           'bytes':golden.nbytes,'dtype':'int32_le' if golden.dtype.itemsize==4 else 'int8',
           'shape':list(golden.shape),'source_sample':a.sample,**cfg}
        for key,x in inputs.items():c[key]=array(name+'.'+key,x)
        for key in ('a','b','d'):c.setdefault(key,None)
        c['golden']=array(name+'.expected',golden);cases.append(c)
    # Bias-only matmuls expose exact accumulator output scaling, including ties/tails.
    scales={bits(.5),bits(1),bits(1/49)}
    scales.update(bits(x['gemmini_output_scale_fp32']) for x in plan['operators'] if x.get('gemmini_output_scale_fp32') is not None)
    scales.update(bits(x['skip_requant_scale_fp32']) for x in plan['residual_blocks'])
    vals=np.array([-2147483648,-16777219,-16777217,-65537,-1025,-257,-255,-129,-128,-127,
                   -5,-3,-1,0,1,3,5,63,64,65,127,128,129,255,257,1025,65537,16777217,16777219,2147483647,0],dtype='<i4')
    for n,s in enumerate(sorted(scales)):
        scale=struct.unpack('<f',struct.pack('<I',s))[0]
        expected=requant8(vals,scale)[0].reshape(1,-1)
        add(f'requant_{n:02d}_0x{s:08x}',1,{'a':np.zeros((1,1),np.int8),'b':np.zeros((1,len(vals)),np.int8),'d':vals},
            expected,scale,I=1,J=len(vals),K=1,relu=0,full_C=0)
    # A full-width read must preserve nonrepresentable-in-FP32 integer bias values.
    add('full_width_bias_int32',1,{'a':np.zeros((1,1),np.int8),'b':np.zeros((1,len(vals)),np.int8),'d':vals},
        vals.reshape(1,-1),I=1,J=len(vals),K=1,relu=0,full_C=1)
    add('requant_half_relu',1,{'a':np.zeros((1,1),np.int8),'b':np.zeros((1,len(vals)),np.int8),'d':vals},
        requant8(vals,.5,True)[0].reshape(1,-1),.5,I=1,J=len(vals),K=1,relu=1,full_C=0)
    def realconv(name,module,input_node,expected_node,pool=False):
        op=ops[module];x=node(input_node);w=z[module+'.weight'].transpose(2,3,1,0).copy();b=z[module+'.bias']
        y=requant8(conv_acc64(x,w,b,op['stride'],op['padding']),op['gemmini_output_scale_fp32'],op['relu'])[0]
        if pool:y=maxpool3_stride2(y)
        expected=node(expected_node);np.testing.assert_array_equal(y,expected)
        oh=(x.shape[1]+2*op['padding'][0]-w.shape[0])//op['stride'][0]+1
        ow=(x.shape[2]+2*op['padding'][1]-w.shape[1])//op['stride'][1]+1
        add(name,2,{'a':x,'b':w,'d':b},expected,op['gemmini_output_scale_fp32'],
            in_h=x.shape[1],in_w=x.shape[2],in_c=x.shape[3],out_c=w.shape[3],out_h=oh,out_w=ow,
            stride=op['stride'][0],padding=op['padding'][0],kernel=w.shape[0],relu=int(op['relu']),
            pool_size=3 if pool else 1,pool_stride=2 if pool else 0,pool_padding=1 if pool else 0)
    realconv('stem_7x7_s2_relu','conv1','input','stem.conv_relu')
    realconv('stem_fused_maxpool','conv1','input','stem.pool',True)
    realconv('conv3x3_s1_real','layer1.0.conv1','stem.pool','layer1.0.conv1_relu')
    realconv('conv3x3_s1_no_relu_real','layer1.0.conv2','layer1.0.conv1_relu','layer1.0.main_preadd')
    realconv('conv3x3_s2_real','layer2.0.conv1','layer1.1.add_relu','layer2.0.conv1_relu')
    realconv('projection1x1_s2_real','layer2.0.downsample.0','layer1.1.add_relu','layer2.0.skip_projection')
    for prefix,skip_node in [('layer1.1','layer1.0.add_relu'),('layer2.0','layer2.0.skip_projection')]:
        skip=node(skip_node);main=node(prefix+'.main_preadd');scale=blocks[prefix]['skip_requant_scale_fp32']
        expected=node(prefix+'.add_relu')
        np.testing.assert_array_equal(residual_add(skip,main,scale)[0],expected)
        add(prefix+'_residual_real',3,{'a':skip,'b':main},expected,a_scale=scale,I=int(np.prod(skip.shape[:-1])),J=skip.shape[-1],relu=1)
    # Synthetic signed residuals force MVIN rounding and add saturation.
    sa=np.array([[127,-128,3,-3,1,-1,126,-127,64]],np.int8)
    sb=np.array([[127,127,1,1,0,0,-128,-128,127]],np.int8)
    add('residual_signed_ties',3,{'a':sa,'b':sb},residual_add(sa,sb,.5)[0],a_scale=.5,I=1,J=9,relu=1)
    x=node('layer4.1.add_relu');expected=node('avgpool');np.testing.assert_array_equal(global_average7(x)[0],expected)
    add('global_average7_real',4,{'a':x},expected,in_h=7,in_c=512)
    av=np.zeros((1,7,7,9),np.int8);av[0,0,0,:]=[0,1,24,25,48,49,50,73,74]
    add('global_average7_boundary',4,{'a':av},global_average7(av)[0],in_h=7,in_c=9)
    x=node('avgpool');w=z['fc.weight'].T.copy();b=z['fc.bias'];expected=node('logits')
    np.testing.assert_array_equal(linear_acc64(x,w,b).astype('<i4'),expected)
    add('fc512x1000_full_int32_real',1,{'a':x,'b':w,'d':b},expected,I=1,J=1000,K=512,relu=0,full_C=1)
    z.close()
    (out/'qops_blob.bin').write_bytes(blob)
    manifest={'abi':1,'sample':a.sample,'source_quant_manifest_sha256':digest(qdir/'quant_manifest.json'),
              'source_sample_manifest_sha256':digest(sd/'sample_manifest.json'),
              'blob_sha256':hashlib.sha256(blob).hexdigest(),'blob_bytes':len(blob),
              'count':len(cases),'max_output_bytes':max(c['bytes'] for c in cases),'arrays':arrays,'cases':cases}
    (out/'qops_manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    signature=bytes.fromhex(digest(out/'qops_manifest.json'))
    lines=['#include "qops.h"','const uint8_t qops_bundle_digest[32] __attribute__((used,section(".rodata.build_info")))={'+','.join(map(str,signature))+'};',
           'const struct qcase qops_cases[]={']
    fields=['bytes','op','I','J','K','in_h','in_w','in_c','out_c','out_h','out_w','stride','padding','kernel',
            'relu','pool_size','pool_stride','pool_padding','full_C','scale_bits','a_scale_bits']
    for c in cases:
        items=[f'.{k}='+('0' if c[k] is None else f'qops_blob+{c[k]}') for k in ('a','b','d','golden')]
        items += [f'.{k}={c.get(k,0)}u' for k in fields]
        lines.append('{'+','.join(items)+'},')
    lines.append('};');(out/'qops_cases.c').write_text('\n'.join(lines)+'\n')
    (out/'qops_generated.h').write_text(f'#define QOPS_COUNT {len(cases)}\n#define QOPS_MAX_OUTPUT {manifest["max_output_bytes"]}\n')
    (out/'qops_blob.S').write_text('.section .rodata.qops_blob,"a",@progbits\n.balign 64\n.globl qops_blob\nqops_blob:\n.incbin "generated/qops_blob.bin"\n.globl qops_blob_end\nqops_blob_end:\n.section .note.GNU-stack,"",@progbits\n')
    print(f'QOPS FIXTURES PASS: {len(cases)} cases; {len(blob):,} blob bytes; sample={a.sample}')
    print('All real operator fixtures recomputed and matched saved integer golden tensors')
    print('Generated:',out)

if __name__=='__main__':main()
