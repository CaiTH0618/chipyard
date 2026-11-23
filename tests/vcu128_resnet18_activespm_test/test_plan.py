"""Independent NumPy reference for synthetic ActiveSPM functional tests."""
import hashlib, struct
import numpy as np
FIELDS='kind offset rows cols stride in_h in_w in_c out_c out_h out_w kernel padding conv_stride pool_size pool_stride pool_padding I J K relu full_C scale_bits a_scale_bits'.split()
SPM_BASES=(0x70000000,0x70100000);SPM_BYTES=1048576
SLOT_BYTES,SLOT_FRAME=200704,200832
BUFFER_BYTES=SLOT_FRAME;WORK_BYTES=2*BUFFER_BYTES+16384+4096
def bits(f):return struct.unpack('<I',struct.pack('<f',f))[0]
def fp(b):return struct.unpack('<f',struct.pack('<I',b))[0]
def sha(b):return hashlib.sha256(b).hexdigest()
def fnv(data):
    h=14695981039346656037
    for b in data:h=((h^b)*1099511628211)&((1<<64)-1)
    return h
def seed(h,iteration,index):return 0x100+h*0x31+iteration*0x65+index*0x13
def pattern(n,s,small=False):
    k=np.arange(n,dtype=np.int64)
    v=((k*5+s*3+(k>>2))%7)-3 if small else ((k*37+s*13+(k>>3)*17)&255)-128
    return v.astype(np.int8)
def scale(x,s):return np.clip(np.rint(np.asarray(x,dtype=np.float32)*np.float32(s)),-128,127).astype(np.int8)
def bias(n,s):return ((np.arange(n,dtype=np.int32)*3+s)%17-8).astype(np.int32)
def case(name,kind,**kw):
    c=dict.fromkeys(FIELDS,0);c.update(name=name,kind=kind,offset=2*SLOT_FRAME+64,scale_bits=bits(1),a_scale_bits=bits(1));c.update(kw);return c
def cases():
    cs=[case('cpu_full_address_pattern_0',1,offset=0),case('cpu_full_address_pattern_1',1,offset=0)]
    cs += [case('cpu_subword_'+str(w*8),2,offset=SPM_BYTES-1024,rows=w,cols=512) for w in (1,2,4,8)]
    for name,r,c,s,offset in [('tile',8,8,8,64),('tails_stride',7,5,17,4096+64),('tail31',1,31,40,8192+64),('burst64_stride80',8,64,80,16384+64),('largest_at_ram_end',3136,64,64,SPM_BYTES-64-200704)]:
        cs.append(case('gemmini_roundtrip_'+name,3,offset=offset,rows=r,cols=c,stride=s))
    cs += [case('matmul_int8',4,I=8,J=8,K=8),
           case('matmul_tails_int8_scale_half',4,I=9,J=17,K=13,relu=1,scale_bits=bits(.5)),
           case('matmul_full_int32_tails',4,I=9,J=17,K=13,full_C=1)]
    cs += [case('conv3x3_spm_input_output',5,in_h=5,in_w=5,in_c=8,out_c=8,out_h=5,out_w=5,kernel=3,padding=1,conv_stride=1,relu=1),
           case('conv3x3_fused_pool',5,in_h=8,in_w=8,in_c=3,out_c=8,out_h=8,out_w=8,kernel=3,padding=1,conv_stride=1,relu=1,pool_size=2,pool_stride=2),
           case('stem7x7_stride2_fused_pool',5,in_h=16,in_w=16,in_c=3,out_c=8,out_h=8,out_w=8,kernel=7,padding=3,conv_stride=2,relu=1,pool_size=3,pool_stride=2,pool_padding=1)]
    cs += [case('resadd_signed_ties_tail',6,I=3,J=31,a_scale_bits=bits(.5)),
           case('resadd_layer2_real_shape',6,I=784,J=128,relu=1),
           case('resadd_layer3_scaled',6,I=196,J=256,relu=1,a_scale_bits=bits(1.5)),
           case('gap7x7_512_spm_input_output',7,in_h=7,in_w=7,in_c=512,scale_bits=bits(1/49)),
           case('resadd_largest_three_slot_overwrite',6,I=3136,J=64,relu=1)]
    return cs
def output_bytes(c):
    if c['kind']==1:return SPM_BYTES
    if c['kind']==2:return c['cols']
    if c['kind']==3:return c['rows']*c['stride']
    if c['kind'] in (4,6):return c['I']*c['J']*(4 if c['full_C'] else 1)
    if c['kind']==7:return c['in_c']
    oh,ow=c['out_h'],c['out_w']
    if c['pool_stride']:
        oh=(oh+2*c['pool_padding']-c['pool_size'])//c['pool_stride']+1
        ow=(ow+2*c['pool_padding']-c['pool_size'])//c['pool_stride']+1
    return oh*ow*c['out_c']
def expected(c,s):
    kind=c['kind'];n=output_bytes(c)
    if kind==1:
        k=np.arange(n//8,dtype=np.uint64)
        words=(k*np.uint64(0x9e3779b97f4a7c15))^np.uint64((s<<32)^0xa35c96f0874bd219)
        return words.astype('<u8').tobytes()
    if kind==2:return pattern(n,s).tobytes()
    if kind==3:
        a=pattern(n,s).view(np.uint8).reshape(c['rows'],c['stride']).copy();a[:,c['cols']:]=0xa5
        return a.tobytes()
    if kind==4:
        a=pattern(c['I']*c['K'],s,True).astype(np.int32).reshape(c['I'],c['K'])
        b=pattern(c['K']*c['J'],s+7,True).astype(np.int32).reshape(c['K'],c['J'])
        z=a@b+bias(c['J'],s+11)
        if c['full_C']:return z.astype('<i4').tobytes()
        z=scale(z,fp(c['scale_bits']));return np.maximum(z,0).astype(np.int8).tobytes() if c['relu'] else z.tobytes()
    if kind==5:
        a=pattern(c['in_h']*c['in_w']*c['in_c'],s,True).astype(np.int32).reshape(c['in_h'],c['in_w'],c['in_c'])
        w=pattern(c['kernel']**2*c['in_c']*c['out_c'],s+7,True).astype(np.int32).reshape(c['kernel'],c['kernel'],c['in_c'],c['out_c'])
        a=np.pad(a,((c['padding'],c['padding']),(c['padding'],c['padding']),(0,0)))
        z=np.zeros((c['out_h'],c['out_w'],c['out_c']),dtype=np.int32)+bias(c['out_c'],s+11)
        for ky in range(c['kernel']):
            for kx in range(c['kernel']):
                patch=a[ky:ky+c['out_h']*c['conv_stride']:c['conv_stride'],kx:kx+c['out_w']*c['conv_stride']:c['conv_stride']]
                z+=patch@w[ky,kx]
        z=scale(z,fp(c['scale_bits']));z=np.maximum(z,0).astype(np.int8) if c['relu'] else z
        if c['pool_stride']:
            oh=(c['out_h']+2*c['pool_padding']-c['pool_size'])//c['pool_stride']+1;ow=(c['out_w']+2*c['pool_padding']-c['pool_size'])//c['pool_stride']+1
            z=np.pad(z,((c['pool_padding'],c['pool_padding']),(c['pool_padding'],c['pool_padding']),(0,0)),constant_values=-128)
            z=np.stack([z[y*c['pool_stride']:y*c['pool_stride']+c['pool_size'],x*c['pool_stride']:x*c['pool_stride']+c['pool_size']].max(axis=(0,1)) for y in range(oh) for x in range(ow)])
        return z.tobytes()
    if kind==6:
        a=scale(pattern(n,s),fp(c['a_scale_bits'])).astype(np.int32);b=pattern(n,s+7).astype(np.int32)
        z=scale(a+b,1);return np.maximum(z,0).astype(np.int8).tobytes() if c['relu'] else z.tobytes()
    if kind==7:
        a=pattern(c['in_h']*c['in_w']*c['in_c'],s).astype(np.int32).reshape(-1,c['in_c'])
        return scale(a.sum(axis=0),fp(c['scale_bits'])).tobytes()
    raise ValueError('Unknown test kind')
