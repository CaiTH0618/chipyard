#include "platform.h"
#include "generated/baseline_generated.h"
#include <string.h>

uint8_t baseline_work[BASE_WORK_BYTES] __attribute__((aligned(64)));
uint8_t baseline_outputs[BASE_OUTPUT_BYTES] __attribute__((aligned(64)));
struct task_result baseline_results[BASE_TASK_COUNT] __attribute__((aligned(64)));
const uint64_t baseline_build_info[32] __attribute__((used,section(".rodata.build_info")))={
 BUILD_MAGIC,ABI_VERSION,APP_BASE,APP_END,STACK0_BOTTOM,STACK0_TOP,STACK1_BOTTOM,STACK1_TOP,
 SHARED_BASE,SHARED_BYTES,STATUS_OFFSET,sizeof(struct status),LOG0_BASE,LOG1_BASE,LOG_SIZE,
 BASE_TASK_COUNT,BASE_STAGE_COUNT,3,BASE_HART_WORK_BYTES,BASE_WORK_BYTES,
 sizeof(struct qcase),sizeof(struct task_result),sizeof(struct task),sizeof(struct config),
 BASE_SLOT_BYTES,BASE_SLOT_FRAME_BYTES,BASE_FINAL_FRAME_BYTES,BASE_OUTPUT_BYTES,
 sizeof(struct refs),MAX_REPEATS,0x248,BASE_FINAL_BYTES
};
static inline uint64_t acquire(const uint64_t *p){return __atomic_load_n(p,__ATOMIC_ACQUIRE);}
static inline void release(uint64_t *p,uint64_t v){__atomic_store_n(p,v,__ATOMIC_RELEASE);}
static void fail(struct shared *p,unsigned h,uint64_t error) __attribute__((noreturn));
static void fail(struct shared *p,unsigned h,uint64_t error){release(&p->status[h].error,error);release(&p->status[h].state,ERROR);platform_stop();}
static void wait_value(struct shared *p,unsigned h,const uint64_t *v,uint64_t value){
 uint64_t start=platform_cycles();unsigned polls=0;
 while(acquire(v)!=value){
  if((++polls&255)==0){
   if(acquire(&p->status[h^1].error))fail(p,h,ERR_PEER);
   if(platform_cycles()-start>=p->cfg.wait_cycles)fail(p,h,ERR_TIMEOUT);
  }
  platform_relax();
 }
}
static unsigned owner(unsigned index,const struct config *c){return c->mode==MODE_SERIAL?0:((index&1)^(unsigned)c->swap);}
static uint8_t *slot(unsigned h,unsigned which){return baseline_work+(size_t)h*BASE_HART_WORK_BYTES+(size_t)which*BASE_SLOT_FRAME_BYTES+64;}
static uint64_t hash_bytes(const uint8_t *v,size_t n){uint64_t x=UINT64_C(14695981039346656037);while(n--){x^=*v++;x*=UINT64_C(1099511628211);}return x;}
static void set_guard(uint8_t *v,size_t n){memset(v-64,0xc3,64);memset(v+n,0x3c,64);}
static int guard_ok(const uint8_t *v,size_t n){for(unsigned j=0;j<64;j++)if((v-64)[j]!=0xc3 || v[n+j]!=0x3c)return 0;return 1;}
static const uint8_t *resolve(unsigned h,int32_t ref,const uint8_t *keep){return ref>=0?slot(h,(unsigned)ref):keep;}
static void compare_stage(struct shared *p,unsigned h,unsigned ti,unsigned k,const struct qcase *c,const uint8_t *out){
 struct task_result *r=&baseline_results[ti];struct status *s=&p->status[h];
 if(!guard_ok(out,c->bytes)){r->guard_ok=0;r->first_stage=k+1;fail(p,h,ERR_GUARD);}
 for(size_t j=0;j<c->bytes;j++)if(out[j]!=c->golden[j]){
  r->first_stage=k+1;r->first_byte=j;r->mismatches=1;
  s->first_bad=j;s->expected_byte=c->golden[j];s->actual_byte=out[j];s->mismatches=1;
  fail(p,h,ERR_DATA);
 }
 r->checked_stages++;s->checked_stages++;
}
void rocket_main(uintptr_t hart){
 if(hart>1)platform_stop();
 unsigned h=(unsigned)hart;struct shared *p=platform_shared();struct status *s=&p->status[h];
 uint64_t epoch=p->cfg.run_id;
 s->hart=h;s->run_id=epoch;s->mode=p->cfg.mode;s->first_bad=UINT64_MAX;
 s->bss_start=platform_image_symbol(0);s->bss_end=platform_image_symbol(1);s->image_end=platform_image_symbol(2);
 release(&s->state,RUNNING);
 /* Hart1 accesses no APP global before hart0 has cleared BSS and released boot. */
 if(h==0){
  const struct config *c=&p->cfg;
  if(c->magic!=CONFIG_MAGIC || c->abi!=ABI_VERSION || !epoch || c->mode>1 || c->swap>1 ||
     (c->mode==MODE_SERIAL && c->swap) || c->task_count!=BASE_TASK_COUNT ||
     !c->wait_cycles || c->wait_cycles>UINT64_C(0x7fffffffffffffff) || c->check_mode>1 ||
     c->warmup>MAX_WARMUP || !c->repeats || c->repeats>MAX_REPEATS)fail(p,h,ERR_CONFIG);
  for(unsigned j=0;j<6;j++)if(c->reserved[j])fail(p,h,ERR_CONFIG);
  release(&p->boot.value,epoch);
 }else wait_value(p,h,&p->boot.value,epoch);
 for(unsigned q=0;q<3;q++)set_guard(slot(h,q),BASE_SLOT_BYTES);
 for(unsigned ti=0;ti<BASE_TASK_COUNT;ti++)if(owner(ti,&p->cfg)==h){
  struct task_result *r=&baseline_results[ti];const struct task *t=&baseline_tasks[ti];
  r->task_id=t->task_id;r->owner=h;r->run_id=epoch;r->first_stage=UINT64_MAX;r->first_byte=UINT64_MAX;
  r->output_address=(uintptr_t)(baseline_outputs+t->output_offset);r->output_bytes=BASE_FINAL_BYTES;r->guard_ok=1;
  set_guard((uint8_t *)(uintptr_t)r->output_address,BASE_FINAL_BYTES);
 }
 const unsigned rounds=(unsigned)(p->cfg.warmup+p->cfg.repeats);
 for(unsigned round=0;round<rounds;round++){
  uint64_t token=(uint64_t)round+1;int measured=round>=p->cfg.warmup;
  release(&p->ready[h].value,token);
  uint64_t batch_begin=0;
  if(h==0){wait_value(p,h,&p->ready[1].value,token);batch_begin=platform_cycles();release(&p->go.value,token);}
  else wait_value(p,h,&p->go.value,token);
  uint64_t local_begin=platform_cycles();
  for(unsigned ti=0;ti<BASE_TASK_COUNT;ti++)if(owner(ti,&p->cfg)==h){
   const struct task *t=&baseline_tasks[ti];struct task_result *r=&baseline_results[ti];
   s->task_index=ti;s->stage=0;uint64_t begin=platform_cycles(),ops=0;
   for(unsigned k=0;k<t->stage_count;k++){
    unsigned index=t->first_stage+k;s->stage=k+1;
    struct qcase c=baseline_stages[index];const struct refs *ref=&baseline_refs[index];
    c.a=resolve(h,ref->a,c.a);c.b=resolve(h,ref->b,c.b);c.d=resolve(h,ref->d,c.d);
    uint8_t *out=ref->out==REF_FINAL?baseline_outputs+t->output_offset:slot(h,(unsigned)ref->out);
    if(p->cfg.check_mode==CHECK_DEBUG){set_guard(out,c.bytes);memset(out,0xa5,c.bytes);}
    uint64_t op_begin=platform_cycles();model_run(&c,out);ops+=platform_cycles()-op_begin;
    if(p->cfg.check_mode==CHECK_DEBUG)compare_stage(p,h,ti,k,&c,out);
   }
   if(measured){r->cycles+=platform_cycles()-begin;r->operator_cycles+=ops;r->computed++;s->tasks_done++;s->operator_cycles+=ops;}
  }
  if(measured){s->local_cycles+=platform_cycles()-local_begin;s->iterations_done++;}else s->warmup_done++;
  release(&p->done[h].value,token);
  if(h==0){
   wait_value(p,h,&p->done[1].value,token);
   if(measured){uint64_t elapsed=platform_cycles()-batch_begin;unsigned n=round-(unsigned)p->cfg.warmup;p->iteration_cycles[n]=elapsed;p->makespan+=elapsed;}
   release(&p->finish.value,token);
  }else wait_value(p,h,&p->finish.value,token);
 }
 /* All measured computation on BOTH harts is over before final comparisons. */
 for(unsigned q=0;q<3;q++)if(!guard_ok(slot(h,q),BASE_SLOT_BYTES))fail(p,h,ERR_GUARD);
 for(unsigned ti=0;ti<BASE_TASK_COUNT;ti++)if(owner(ti,&p->cfg)==h){
  const struct task *t=&baseline_tasks[ti];struct task_result *r=&baseline_results[ti];
  const struct qcase *last=&baseline_stages[t->first_stage+29];
  s->task_index=ti;s->stage=30;
  const uint8_t *out=(const uint8_t *)(uintptr_t)r->output_address;
  if(!guard_ok(out,BASE_FINAL_BYTES)){r->guard_ok=0;fail(p,h,ERR_GUARD);}
  for(size_t j=0;j<BASE_FINAL_BYTES;j++)if(out[j]!=last->golden[j]){
   r->first_stage=30;r->first_byte=j;r->mismatches=1;s->first_bad=j;s->expected_byte=last->golden[j];s->actual_byte=out[j];s->mismatches=1;fail(p,h,ERR_DATA);
  }
  r->checksum=hash_bytes(out,BASE_FINAL_BYTES);r->verified=1;s->verified_tasks++;
 }
 release(&s->state,DONE);platform_stop();
}
