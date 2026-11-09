#include "platform.h"
#include "generated/dual_generated.h"
#include <string.h>

uint8_t dual_arenas[DUAL_ARENA_BYTES] __attribute__((aligned(64)));
struct task_result dual_results[DUAL_TASK_COUNT] __attribute__((aligned(64)));
const uint64_t dual_build_info[24] __attribute__((used,section(".rodata.build_info")))={
 BUILD_MAGIC,ABI_VERSION,APP_BASE,APP_END,STACK0_BOTTOM,STACK0_TOP,STACK1_BOTTOM,STACK1_TOP,
 SHARED_BASE,SHARED_BYTES,STATUS_OFFSET,sizeof(struct status),LOG0_BASE,LOG1_BASE,LOG_SIZE,
 DUAL_TASK_COUNT,DUAL_STAGE_COUNT,DUAL_BUFFER_COUNT,DUAL_ARENA_STRIDE,DUAL_ARENA_BYTES,
 sizeof(struct qcase),sizeof(struct task_result),sizeof(struct task),sizeof(struct config)
};
static inline uint64_t acquire(const uint64_t *p){return __atomic_load_n(p,__ATOMIC_ACQUIRE);}
static inline void release(uint64_t *p,uint64_t v){__atomic_store_n(p,v,__ATOMIC_RELEASE);}
static void fail(struct shared *p,unsigned h,uint64_t error) __attribute__((noreturn));
static void fail(struct shared *p,unsigned h,uint64_t error){release(&p->status[h].error,error);release(&p->status[h].state,ERROR);platform_stop();}
static void wait_epoch(struct shared *p,unsigned h,const uint64_t *v,uint64_t epoch){
 uint64_t start=platform_cycles();unsigned polls=0;
 while(acquire(v)!=epoch){
  if((++polls&255)==0){
   if(acquire(&p->status[h^1].error))fail(p,h,ERR_PEER);
   if(platform_cycles()-start>=p->cfg.wait_cycles)fail(p,h,ERR_TIMEOUT);
  }
  platform_relax();
 }
}
static unsigned owner(unsigned index,uint64_t mode,uint64_t swap){return mode==MODE_SERIAL?0:((index&1)^(unsigned)swap);}
static uint64_t checksum(const uint8_t *v,size_t n){uint64_t x=UINT64_C(14695981039346656037);while(n--){x^=*v++;x*=UINT64_C(1099511628211);}return x;}
static int guards_ok(unsigned task_index){
 size_t base=dual_tasks[task_index].arena_offset;
 for(unsigned i=0;i<DUAL_BUFFER_COUNT;i++){
  const struct buffer *b=&dual_buffers[i];
  for(unsigned j=0;j<64;j++)if(dual_arenas[base+b->offset-64+j]!=0xc3 || dual_arenas[base+b->offset+b->bytes+j]!=0x3c)return 0;
 }
 return 1;
}
void rocket_main(uintptr_t hart){
 if(hart>1)platform_stop();
 unsigned h=(unsigned)hart;struct shared *p=platform_shared();struct status *s=&p->status[h];
 uint64_t epoch=p->cfg.run_id;
 s->hart=h;s->run_id=epoch;s->mode=p->cfg.mode;
 s->bss_start=platform_image_symbol(0);s->bss_end=platform_image_symbol(1);s->image_end=platform_image_symbol(2);
 release(&s->state,RUNNING);
 /* Only hart0 clears APP BSS in start.S. Hart1 accesses no APP globals before boot. */
 if(h==0){
  if(p->cfg.magic!=CONFIG_MAGIC || p->cfg.abi!=ABI_VERSION || !epoch || p->cfg.mode>1 || p->cfg.swap>1 || p->cfg.task_count!=DUAL_TASK_COUNT || !p->cfg.wait_cycles || p->cfg.wait_cycles>UINT64_C(0x7fffffffffffffff))fail(p,h,ERR_CONFIG);
  release(&p->boot.value,epoch);
 }else wait_epoch(p,h,&p->boot.value,epoch);
 /* Private task arenas are initialized before GO, outside the compute interval. */
 for(unsigned ti=0;ti<DUAL_TASK_COUNT;ti++)if(owner(ti,p->cfg.mode,p->cfg.swap)==h){
  const struct task *t=&dual_tasks[ti];struct task_result *r=&dual_results[ti];
  r->task_id=t->task_id;r->owner=h;r->run_id=epoch;r->first_stage=UINT64_MAX;r->first_byte=UINT64_MAX;
  for(unsigned bi=0;bi<DUAL_BUFFER_COUNT;bi++){
   const struct buffer *b=&dual_buffers[bi];uint8_t *v=dual_arenas+t->arena_offset+b->offset;
   memset(v-64,0xc3,64);memset(v,0xa5,b->bytes);memset(v+b->bytes,0x3c,64);
  }
 }
 release(&p->ready[h].value,epoch);
 uint64_t batch_begin=0;
 if(h==0){wait_epoch(p,h,&p->ready[1].value,epoch);batch_begin=platform_cycles();release(&p->go.value,epoch);}
 else wait_epoch(p,h,&p->go.value,epoch);
 uint64_t local_begin=platform_cycles();
 for(unsigned ti=0;ti<DUAL_TASK_COUNT;ti++)if(owner(ti,p->cfg.mode,p->cfg.swap)==h){
  const struct task *t=&dual_tasks[ti];struct task_result *r=&dual_results[ti];
  s->task_index=ti;s->stage=0;uint64_t begin=platform_cycles(),op_cycles=0;
  for(unsigned k=0;k<t->stage_count;k++){
   unsigned index=t->first_stage+k;s->stage=k+1;
   uint64_t op_begin=platform_cycles();model_run(&dual_stages[index],dual_arenas+dual_output_offsets[index]);op_cycles+=platform_cycles()-op_begin;
  }
  r->cycles=platform_cycles()-begin;r->operator_cycles=op_cycles;
  const struct qcase *last=&dual_stages[t->first_stage+t->stage_count-1];
  r->output_address=(uintptr_t)(dual_arenas+dual_output_offsets[t->first_stage+t->stage_count-1]);r->output_bytes=last->bytes;
  r->computed=1;s->operator_cycles+=op_cycles;s->tasks_done++;
 }
 s->local_cycles=platform_cycles()-local_begin;
 /* Backends retain their board-validated accelerator completion fences. */
 release(&p->done[h].value,epoch);
 if(h==0){
  wait_epoch(p,h,&p->done[1].value,epoch);p->makespan=platform_cycles()-batch_begin;
  release(&p->finish.value,epoch);
 }else wait_epoch(p,h,&p->finish.value,epoch);
 /* Neither core compares tensors or hashes while the peer is still computing. */
 for(unsigned ti=0;ti<DUAL_TASK_COUNT;ti++)if(owner(ti,p->cfg.mode,p->cfg.swap)==h){
  const struct task *t=&dual_tasks[ti];struct task_result *r=&dual_results[ti];
  r->guard_ok=guards_ok(ti);if(!r->guard_ok)fail(p,h,ERR_GUARD);
  for(unsigned k=0;k<t->stage_count;k++){
   unsigned index=t->first_stage+k;const struct qcase *c=&dual_stages[index];const uint8_t *out=dual_arenas+dual_output_offsets[index];
   for(size_t j=0;j<c->bytes;j++)if(out[j]!=c->golden[j]){
    if(!r->mismatches){r->first_stage=k+1;r->first_byte=j;}r->mismatches++;
   }
  }
  r->checksum=checksum((const uint8_t *)(uintptr_t)r->output_address,r->output_bytes);
  if(r->mismatches){s->mismatches=r->mismatches;fail(p,h,ERR_DATA);}
  r->verified=1;s->verified_tasks++;
 }
 release(&s->state,DONE);platform_stop();
}
