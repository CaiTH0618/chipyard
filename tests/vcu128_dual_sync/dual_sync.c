#include "platform.h"

const uint64_t dual_sync_build_info[16]
 __attribute__((used,section(".rodata.build_info"))) = {
 BUILD_MAGIC,ABI_VERSION,APP_BASE,APP_LIMIT,STACK0_BOTTOM,STACK0_TOP,
 STACK1_BOTTOM,STACK1_TOP,SHARED_BASE,SHARED_BYTES,REQUEST_BASE,RESPONSE_BASE,
 MAX_PAYLOAD,sizeof(struct config),STATUS_OFFSET,STATUS_BYTES
};
/* Also exercise startup BSS initialization; no mutable global runtime state. */
uint64_t startup_bss_probe[64];
struct context {
    struct shared *shared;
    struct status *status;
    unsigned hart;
    uint64_t generation;
};
static inline uint64_t acquire(const uint64_t *p) { return __atomic_load_n(p,__ATOMIC_ACQUIRE); }
static inline void release(uint64_t *p,uint64_t v) { __atomic_store_n(p,v,__ATOMIC_RELEASE); }

static void fail(struct context *c,unsigned error,uint64_t round,uint64_t index,
                 uint64_t expected,uint64_t observed) __attribute__((noreturn));
static void fail(struct context *c,unsigned error,uint64_t round,uint64_t index,
                 uint64_t expected,uint64_t observed) {
    struct status *s=c->status;
    s->error_test=s->test; s->error_round=round; s->error_index=index;
    s->expected=expected; s->observed=observed;
    release(&s->error,error); release(&s->state,ERROR);
    platform_stop();
}
static inline void check_peer(struct context *c) {
    uint64_t error=acquire(&c->shared->status[c->hart^1].error);
    if(error) fail(c,ERR_PEER,c->status->round,0,0,error);
}
static void wait_value(struct context *c,const uint64_t *p,uint64_t expected,int at_least) {
    uint64_t begin=platform_cycles(); unsigned polls=0;
    for(;;) {
        uint64_t got=acquire(p);
        if(got==expected || (at_least && got>expected)) return;
        if(!at_least && got>expected) fail(c,ERR_SEQUENCE,c->status->round,0,expected,got);
        if((++polls&255)==0) {
            check_peer(c);
            if(platform_cycles()-begin >= c->shared->cfg.wait_cycles)
                fail(c,ERR_TIMEOUT,c->status->round,0,expected,got);
        }
        platform_relax();
    }
}
/* Two-party reusable barrier: one writer per cache line, monotonic tickets.
 * The peer may already be at the next barrier, so wait uses >=, not ==. */
static void sync_barrier(struct context *c) {
    ++c->generation;
    release(&c->shared->arrived[c->hart].value,c->generation);
    wait_value(c,&c->shared->arrived[c->hart^1].value,c->generation,1);
}
static void delay(struct context *c,uint64_t round) {
    unsigned maximum=c->shared->cfg.delay_cycles;
    if(!maximum) return;
    uint64_t n=pattern(c->shared->cfg.seed,0,round,c->hart)%((uint64_t)maximum+1);
    uint64_t begin=platform_cycles();
    while(platform_cycles()-begin<n) platform_relax();
}
static void begin_test(struct context *c,unsigned test) {
    c->status->test=test; c->status->round=0; sync_barrier(c);
}
static void ping_test(struct context *c) {
    begin_test(c,TEST_PING); uint64_t begin=platform_cycles(),hash=DIGEST_INIT;
    for(uint64_t round=1; round<=c->shared->cfg.ping_rounds; ++round) {
        c->status->round=round; delay(c,round);
        uint64_t x=pattern(c->shared->cfg.seed,TEST_PING,round,0);
        uint64_t y=ping_reply(x,round),tag=((uint64_t)TEST_PING<<32)|round;
        if(c->hart==0) {
            c->shared->ping_request.value=x;
            release(&c->shared->request_seq.value,tag);
            wait_value(c,&c->shared->response_seq.value,tag,0);
            uint64_t got=c->shared->ping_response.value;
            if(got!=y) fail(c,ERR_DATA,round,0,y,got);
            hash=digest_add(hash,got);
        } else {
            wait_value(c,&c->shared->request_seq.value,tag,0);
            uint64_t got=c->shared->ping_request.value;
            if(got!=x) fail(c,ERR_DATA,round,0,x,got);
            hash=digest_add(hash,got);
            c->shared->ping_response.value=ping_reply(got,round);
            release(&c->shared->response_seq.value,tag);
        }
        c->status->ping_done=round;
    }
    c->status->ping_digest=hash;
    c->status->checked_bytes+=8*c->shared->cfg.ping_rounds;
    c->status->cycles_ping=platform_cycles()-begin; sync_barrier(c);
}
static void block_test(struct context *c) {
    begin_test(c,TEST_BLOCK); uint64_t begin=platform_cycles(),hash=DIGEST_INIT,total=0;
    uint32_t *request=platform_request(),*response=platform_response();
    for(uint64_t round=1; round<=c->shared->cfg.block_rounds; ++round) {
        c->status->round=round; delay(c,round);
        unsigned bytes=block_bytes(round),words=bytes/4;
        uint64_t tag=((uint64_t)TEST_BLOCK<<32)|round;
        if(c->hart==0) {
            for(unsigned i=0;i<words;++i) request[i]=(uint32_t)pattern(c->shared->cfg.seed,TEST_BLOCK,round,i);
            release(&c->shared->request_seq.value,tag);
            wait_value(c,&c->shared->response_seq.value,tag,0);
            for(unsigned i=0;i<words;++i) {
                uint32_t expected=block_reply((uint32_t)pattern(c->shared->cfg.seed,TEST_BLOCK,round,i),(uint32_t)round,i);
                uint32_t got=response[i];
                if(got!=expected) fail(c,ERR_DATA,round,i,expected,got);
                hash=digest_add(hash,got);
            }
        } else {
            wait_value(c,&c->shared->request_seq.value,tag,0);
            for(unsigned i=0;i<words;++i) {
                uint32_t expected=(uint32_t)pattern(c->shared->cfg.seed,TEST_BLOCK,round,i),got=request[i];
                if(got!=expected) fail(c,ERR_DATA,round,i,expected,got);
                hash=digest_add(hash,got); response[i]=block_reply(got,(uint32_t)round,i);
            }
            release(&c->shared->response_seq.value,tag);
        }
        total+=bytes; c->status->block_done=round;
    }
    c->status->checked_bytes+=total; c->status->block_digest=hash;
    c->status->cycles_block=platform_cycles()-begin; sync_barrier(c);
}
static void barrier_test(struct context *c) {
    begin_test(c,TEST_BARRIER); uint64_t begin=platform_cycles(),hash=DIGEST_INIT;
    for(uint64_t round=1; round<=c->shared->cfg.barrier_rounds; ++round) {
        c->status->round=round; delay(c,round);
        for(unsigned i=0;i<8;++i)
            c->shared->contribution[c->hart].word[i]=pattern(c->shared->cfg.seed,TEST_BARRIER,round,c->hart*8+i);
        sync_barrier(c); /* Publish contributions and acquire peer's contribution. */
        for(unsigned i=0;i<8;++i) {
            uint64_t expected=pattern(c->shared->cfg.seed,TEST_BARRIER,round,(c->hart^1)*8+i);
            uint64_t got=c->shared->contribution[c->hart^1].word[i];
            if(got!=expected) fail(c,ERR_DATA,round,i,expected,got);
            hash=digest_add(hash,got);
        }
        sync_barrier(c); /* Do not overwrite until the peer has finished reading. */
        c->status->barrier_done=round;
    }
    c->status->checked_bytes+=64*c->shared->cfg.barrier_rounds;
    c->status->barrier_digest=hash;
    c->status->cycles_barrier=platform_cycles()-begin; sync_barrier(c);
}
static void atomic_test(struct context *c) {
    begin_test(c,TEST_ATOMIC); uint64_t begin=platform_cycles();
    for(uint64_t i=0;i<c->shared->cfg.atomic_iters;++i) {
        __atomic_fetch_add(&c->shared->counter.value,UINT64_C(1),__ATOMIC_RELAXED);
        if((i&1023)==0) { c->status->round=i+1; check_peer(c); delay(c,i+1); }
    }
    c->status->atomic_done=c->shared->cfg.atomic_iters;
    sync_barrier(c); /* Both RMW loops are complete before checking the counter. */
    uint64_t observed=acquire(&c->shared->counter.value),expected=2*c->shared->cfg.atomic_iters;
    c->status->counter_seen=observed;
    if(observed!=expected) fail(c,ERR_COUNTER,c->status->round,0,expected,observed);
    c->status->cycles_atomic=platform_cycles()-begin; sync_barrier(c);
}
static int valid_config(const struct config *p) {
    return p->magic==CONFIG_MAGIC && p->abi==ABI_VERSION && p->bytes==sizeof(*p) && p->run_id &&
        p->test_mask && !(p->test_mask&~15u) &&
        p->ping_rounds && p->ping_rounds<=10000000 &&
        p->block_rounds && p->block_rounds<=100000 &&
        p->barrier_rounds && p->barrier_rounds<=1000000 &&
        p->atomic_iters && p->atomic_iters<=100000000 &&
        p->wait_cycles && p->wait_cycles<=UINT64_C(0x7fffffffffffffff) && p->delay_cycles<=1000000;
}
void rocket_main(uintptr_t hart) {
    if(hart>1) platform_stop();
    struct shared *p=platform_shared();
    struct context c={p,&p->status[hart],(unsigned)hart,0};
    struct status *s=c.status;
    s->hart=hart; s->run_id=p->cfg.run_id;
    s->bss_start=platform_image_symbol(0); s->bss_end=platform_image_symbol(1); s->image_end=platform_image_symbol(2);
    release(&s->state,RUNNING);
    if(!valid_config(&p->cfg)) fail(&c,ERR_CONFIG,0,0,CONFIG_MAGIC,p->cfg.magic);
    if(hart==0) {
        for(unsigned i=0;i<64;++i) if(startup_bss_probe[i]) fail(&c,ERR_BSS,0,i,0,startup_bss_probe[i]);
        release(&p->boot.value,p->cfg.run_id);
    } else {
        /* Before this acquire, no mutable APP/BSS globals are accessed. */
        wait_value(&c,&p->boot.value,p->cfg.run_id,0);
    }
    sync_barrier(&c); uint64_t begin=platform_cycles();
    if(p->cfg.test_mask&1) ping_test(&c);
    if(p->cfg.test_mask&2) block_test(&c);
    if(p->cfg.test_mask&4) barrier_test(&c);
    if(p->cfg.test_mask&8) atomic_test(&c);
    s->test=TEST_FINISH; sync_barrier(&c); check_peer(&c);
    s->cycles_total=platform_cycles()-begin;
    release(&s->state,DONE); platform_stop();
}
