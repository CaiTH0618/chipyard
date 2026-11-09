"""Shared Host interpretation of result_layout.h, little-endian RV64."""
import struct

APP_BASE, APP_END = 0x80000000, 0x8F000000
STACK_BOTTOM, STACK_TOP = 0x8FE00000, 0x8FF00000
RESULT_BASE, RESULT_REGION = 0x8FF00000, 0x4000
LOG_BASE, LOG_SIZE, HBM_END = 0x8FF10000, 0x10000, 0x90000000
MAGIC, BUILD_MAGIC, ABI = 0x52534E4554353031, 0x52533530454C4631, 1
PREPARED, RUNNING, DONE, ERROR = 1, 2, 3, 4
MODE_NAMES = {0:'init', 1:'first', 2:'full'}
EXPECTED = (75,900,641,897)
HEADER_BYTES, OUTPUT_BYTES, RESULT_BYTES = 256,4000,4256
OUTPUT_VALID, GOLDEN_OK = 1,2
FIELDS = [
    ('magic','Q'),('abi','I'),('size','I'),('run_id','Q'),
    ('state','I'),('stage','I'),('mode','I'),('hart','I'),('error','I'),('flags','I'),
    ('cycles_total','Q'),('cycles_ops','Q'),('mcause','Q'),('mepc','Q'),('mtval','Q'),
    ('image_end','Q'),('bss_start','Q'),('bss_end','Q'),
    ('log_write','I'),('batch','I'),('classes','I'),('reserved0','I'),
    ('predictions','4I'),('scores','4i'),('expected','4I'),
    ('checksum','Q'),('first_bytes','Q'),('reserved','64s'),
]
OFFSETS={}; size=0
for name,fmt in FIELDS:
    OFFSETS[name]=size; size+=struct.calcsize('<'+fmt)
assert size==HEADER_BYTES


def parse_header(data):
    if len(data)<HEADER_BYTES: raise ValueError('short result header')
    result={}
    for name,fmt in FIELDS:
        values=struct.unpack_from('<'+fmt,data,OFFSETS[name])
        result[name]=values[0] if len(values)==1 else values
    return result


def initial_result(run_id,mode):
    data=bytearray(RESULT_REGION)
    values=dict(magic=MAGIC,abi=ABI,size=RESULT_BYTES,run_id=run_id,
                state=PREPARED,mode=mode,batch=4,classes=1000,expected=EXPECTED)
    for name,fmt in FIELDS:
        if name in values:
            value=values[name]
            struct.pack_into('<'+fmt,data,OFFSETS[name],*(value if isinstance(value,tuple) else (value,)))
    return bytes(data)


def fnv64(data):
    value=14695981039346656037
    for byte in data: value=((value ^ byte)*1099511628211)&0xffffffffffffffff
    return value


def verify_final(header,outputs,mode,run_id):
    if (header['magic'],header['abi'],header['size'],header['run_id']) != (MAGIC,ABI,RESULT_BYTES,run_id):
        raise ValueError('wrong/stale result identity')
    if (header['state'],header['mode'],header['hart'],header['error']) != (DONE,mode,0,0):
        raise ValueError('result state/mode/hart/error mismatch')
    if header['stage']!=400: raise ValueError('unexpected final stage')
    if mode!=2:
        if header['flags']&OUTPUT_VALID: raise ValueError('partial mode unexpectedly exported full outputs')
        if mode==1 and header['first_bytes']!=4*56*56*64:
            raise ValueError('unexpected first-layer output dimensions')
        return
    if len(outputs)!=OUTPUT_BYTES or (header['batch'],header['classes'])!=(4,1000):
        raise ValueError('output shape mismatch')
    if header['flags']&(OUTPUT_VALID|GOLDEN_OK)!=(OUTPUT_VALID|GOLDEN_OK):
        raise ValueError('target did not report complete golden validation')
    if header['expected']!=EXPECTED: raise ValueError('unexpected golden class order')
    if fnv64(outputs)!=header['checksum']: raise ValueError('output checksum mismatch')
    values=struct.unpack('<4000b',outputs)
    for batch in range(4):
        scores=values[batch*1000:(batch+1)*1000]
        if len(set(scores))==1: raise ValueError(f'batch {batch}: degenerate constant score vector')
        pred=max(range(1000),key=lambda i:scores[i])
        if (pred,scores[pred])!=(header['predictions'][batch],header['scores'][batch]):
            raise ValueError(f'batch {batch}: Host/target prediction mismatch')
        if scores[pred]!=scores[EXPECTED[batch]]:
            raise ValueError(f'batch {batch}: official golden rule failed')
