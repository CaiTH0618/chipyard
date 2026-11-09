"""Host ABI and an independent Python oracle for the target's four tests."""
from dataclasses import dataclass, asdict
import struct

APP_BASE, APP_LIMIT = 0x80000000, 0x80100000
STACK0_BOTTOM, STACK0_TOP = 0x80110000, 0x80120000
STACK1_BOTTOM, STACK1_TOP = 0x80130000, 0x80140000
SHARED_BASE, SHARED_BYTES = 0x80200000, 4096
REQUEST_BASE, RESPONSE_BASE, MAX_PAYLOAD = 0x80300000, 0x80400000, 65536
HBM_END = 0x90000000
STATUS_OFFSET, STATUS_BYTES = 0x400, 256
CONFIG_MAGIC, BUILD_MAGIC, ABI = 0x4453594e43434631, 0x4453594e43454c31, 1
PREPARED, RUNNING, DONE, ERROR = 1, 2, 3, 4
MASKS = {'ping':1, 'block':2, 'barrier':4, 'atomic':8, 'all':15}
TESTS = {0:'bootstrap',1:'ping',2:'block',3:'barrier',4:'atomic',5:'finished'}
ERRORS = {1:'configuration',2:'wait timeout',3:'peer failed',4:'data mismatch',
          5:'unexpected sequence',6:'counter mismatch',7:'RISC-V trap',8:'BSS not zero'}
U64, U32 = (1<<64)-1, (1<<32)-1
DIGEST_INIT, DIGEST_MULT = 14695981039346656037, 1099511628211
CONFIG_FORMAT = '<QIIQQIIIIQQI60s'
assert struct.calcsize(CONFIG_FORMAT)==128
STATUS_FIELDS = '''state hart test round error error_test error_round error_index
expected observed mcause mepc mtval ping_done block_done barrier_done atomic_done
checked_bytes ping_digest block_digest barrier_digest counter_seen cycles_total
cycles_ping cycles_block cycles_barrier cycles_atomic bss_start bss_end image_end run_id reserved'''.split()
assert len(STATUS_FIELDS)==32


@dataclass(frozen=True)
class Config:
    run_id:int
    seed:int=0x123456789abcdef0
    test_mask:int=15
    ping_rounds:int=10000
    block_rounds:int=96
    barrier_rounds:int=1000
    atomic_iters:int=100000
    wait_cycles:int=500000000
    delay_cycles:int=200

    def validate(self):
        limits={'run_id':(1,U64),'seed':(0,U64),'test_mask':(1,15),
                'ping_rounds':(1,10000000),'block_rounds':(1,100000),
                'barrier_rounds':(1,1000000),'atomic_iters':(1,100000000),
                'wait_cycles':(1,(1<<63)-1),'delay_cycles':(0,1000000)}
        for name,(low,high) in limits.items():
            value=getattr(self,name)
            if not isinstance(value,int) or not low<=value<=high:
                raise ValueError(f'{name} must be in [{low}, {high}]')
        return self

    def pack(self):
        self.validate()
        return struct.pack(CONFIG_FORMAT,CONFIG_MAGIC,ABI,128,self.run_id,self.seed,
            self.test_mask,self.ping_rounds,self.block_rounds,self.barrier_rounds,
            self.atomic_iters,self.wait_cycles,self.delay_cycles,bytes(60))

    @classmethod
    def unpack(cls,data):
        if len(data)<128: raise ValueError('short config')
        v=struct.unpack_from(CONFIG_FORMAT,data)
        if v[:3]!=(CONFIG_MAGIC,ABI,128): raise ValueError('configuration ABI mismatch')
        return cls(v[3],v[4],v[5],v[6],v[7],v[8],v[9],v[10],v[11]).validate()


def initial_shared(cfg):
    data=bytearray(SHARED_BYTES);data[:128]=cfg.pack()
    for hart in range(2):
        s={name:0 for name in STATUS_FIELDS}
        s.update(state=PREPARED,hart=hart,run_id=cfg.run_id)
        struct.pack_into('<32Q',data,STATUS_OFFSET+STATUS_BYTES*hart,*(s[name] for name in STATUS_FIELDS))
    return bytes(data)


def parse_status(data):
    if len(data)!=STATUS_BYTES: raise ValueError('short status')
    return dict(zip(STATUS_FIELDS,struct.unpack('<32Q',data)))


def pattern(seed,test,round,index):
    x=(seed ^ (test<<56) ^ ((round*0x9e3779b97f4a7c15)&U64) ^ ((index*0xd1b54a32d192ed03)&U64))&U64
    x=((x^(x>>30))*0xbf58476d1ce4e5b9)&U64
    x=((x^(x>>27))*0x94d049bb133111eb)&U64
    return x^(x>>31)


def ping_reply(x,round):
    x^=0xa5a55a5af00dcafe
    return (((x<<17)|(x>>47))+round)&U64


def block_reply(x,round,index):
    x^=0xa5a55a5a
    return (((x<<7)|(x>>25))+round+index)&U32


def block_bytes(round): return (64,4096,65536)[(round-1)%3]
def digest_add(h,v): return ((h^v)*DIGEST_MULT)&U64


def expected_results(cfg):
    """Recompute every expected word/round, not just target-reported PASS."""
    cfg.validate()
    expected=[dict(ping_done=0,block_done=0,barrier_done=0,atomic_done=0,
                   checked_bytes=0,ping_digest=0,block_digest=0,barrier_digest=0,counter_seen=0) for _ in range(2)]
    for hart,s in enumerate(expected):
        if cfg.test_mask&1:
            h=DIGEST_INIT
            for r in range(1,cfg.ping_rounds+1):
                x=pattern(cfg.seed,1,r,0)
                h=digest_add(h,ping_reply(x,r) if hart==0 else x)
            s.update(ping_done=cfg.ping_rounds,ping_digest=h)
            s['checked_bytes']+=8*cfg.ping_rounds
        if cfg.test_mask&2:
            h=DIGEST_INIT; total=0
            for r in range(1,cfg.block_rounds+1):
                n=block_bytes(r);total+=n
                for i in range(n//4):
                    x=pattern(cfg.seed,2,r,i)&U32
                    h=digest_add(h,block_reply(x,r,i) if hart==0 else x)
            s.update(block_done=cfg.block_rounds,block_digest=h)
            s['checked_bytes']+=total
        if cfg.test_mask&4:
            h=DIGEST_INIT
            for r in range(1,cfg.barrier_rounds+1):
                for i in range(8): h=digest_add(h,pattern(cfg.seed,3,r,(hart^1)*8+i))
            s.update(barrier_done=cfg.barrier_rounds,barrier_digest=h)
            s['checked_bytes']+=64*cfg.barrier_rounds
        if cfg.test_mask&8:
            s.update(atomic_done=cfg.atomic_iters,counter_seen=2*cfg.atomic_iters)
    return expected


def verify_snapshot(shared,request,response,cfg,plan=None):
    if len(shared)!=SHARED_BYTES or len(request)!=MAX_PAYLOAD or len(response)!=MAX_PAYLOAD:
        raise ValueError('snapshot size mismatch')
    if shared[:128]!=cfg.pack(): raise ValueError('configuration changed or wrong run')
    if struct.unpack_from('<Q',shared,0x80)[0]!=cfg.run_id: raise ValueError('bootstrap epoch mismatch')
    expected=expected_results(cfg)
    statuses=[]
    for hart in range(2):
        off=STATUS_OFFSET+hart*STATUS_BYTES;s=parse_status(shared[off:off+STATUS_BYTES]);statuses.append(s)
        if (s['state'],s['hart'],s['run_id'],s['error'],s['test'])!=(DONE,hart,cfg.run_id,0,5):
            raise ValueError(f'hart{hart}: incomplete/wrong-epoch/error result: {s}')
        if not APP_BASE<=s['bss_start']<=s['bss_end']==s['image_end']<=APP_LIMIT:
            raise ValueError(f'hart{hart}: linked memory range mismatch')
        if any(s[k] for k in ('mcause','mepc','mtval')): raise ValueError(f'hart{hart}: unexpected trap metadata')
        if plan:
            for field,symbol in [('bss_start','__bss_start'),('bss_end','__bss_end'),('image_end','__image_end')]:
                if s[field]!=plan.symbols[symbol]: raise ValueError(f'hart{hart}: linker symbol {symbol} mismatch')
        for key,value in expected[hart].items():
            if s[key]!=value: raise ValueError(f'hart{hart}: {key} got {s[key]:#x}, expected {value:#x}')
    # A phase calls begin/end barriers; barrier phase additionally calls twice per round.
    generations=2+2*bin(cfg.test_mask).count('1')
    if cfg.test_mask&4: generations+=2*cfg.barrier_rounds
    if cfg.test_mask&8: generations+=1  # atomic phase has an extra completion barrier
    for hart in range(2):
        if struct.unpack_from('<Q',shared,0x140+64*hart)[0]!=generations:
            raise ValueError(f'hart{hart}: barrier generation count mismatch')
    count=struct.unpack_from('<Q',shared,0x1c0)[0]
    if count!=(2*cfg.atomic_iters if cfg.test_mask&8 else 0): raise ValueError('shared counter mismatch')
    last_test=2 if cfg.test_mask&2 else 1 if cfg.test_mask&1 else 0
    last_round=cfg.block_rounds if last_test==2 else cfg.ping_rounds
    tag=(last_test<<32)|last_round if last_test else 0
    if any(struct.unpack_from('<Q',shared,o)[0]!=tag for o in (0xc0,0x100)):
        raise ValueError('final request/response sequence mismatch')
    if cfg.test_mask&1:
        x=pattern(cfg.seed,1,cfg.ping_rounds,0)
        if struct.unpack_from('<Q',shared,0x200)[0]!=x or struct.unpack_from('<Q',shared,0x240)[0]!=ping_reply(x,cfg.ping_rounds):
            raise ValueError('final ping payload mismatch')
    if cfg.test_mask&2:
        r=cfg.block_rounds;n=block_bytes(r)
        req=struct.unpack_from('<'+str(n//4)+'I',request)
        resp=struct.unpack_from('<'+str(n//4)+'I',response)
        for i,(a,b) in enumerate(zip(req,resp)):
            x=pattern(cfg.seed,2,r,i)&U32
            if a!=x or b!=block_reply(x,r,i): raise ValueError(f'final block word {i} mismatch')
    if cfg.test_mask&4:
        for hart in range(2):
            values=struct.unpack_from('<8Q',shared,0x280+64*hart)
            if values!=tuple(pattern(cfg.seed,3,cfg.barrier_rounds,hart*8+i) for i in range(8)):
                raise ValueError('final barrier contribution mismatch')
    return statuses
