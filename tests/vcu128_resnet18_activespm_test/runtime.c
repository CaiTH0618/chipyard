#include "platform.h"
#include "generated/test_generated.h"
#include <string.h>

struct workspace {
 uint8_t source[TEST_BUFFER_BYTES],mirror[TEST_BUFFER_BYTES],weights[16384];int32_t bias[1024];
} __attribute__((aligned(64)));
struct workspace test_work[2] __attribute__((aligned(64)));
struct test_record test_records[2][TEST_MAX_ITERATIONS][TEST_COUNT] __attribute__((aligned(64)));
_Static_assert(sizeof(struct workspace)==TEST_WORK_BYTES,"workspace ABI");
const uint64_t test_build_info[32] __attribute__((used,section(".rodata.build_info")))={
 BUILD_MAGIC,ABI_VERSION,APP_BASE,APP_END,STACK0_BOTTOM,STACK0_TOP,STACK1_BOTTOM,STACK1_TOP,
 SHARED_BASE,SHARED_BYTES,STATUS_OFFSET,sizeof(struct status),LOG0_BASE,LOG1_BASE,LOG_SIZE,
 SPM0_BASE,SPM1_BASE,SPM_BYTES,CTRL0_BASE,CTRL1_BASE,TEST_COUNT,TEST_MAX_ITERATIONS,
 sizeof(struct test_case),sizeof(struct test_record),sizeof(struct config),TEST_BUFFER_BYTES,TEST_WORK_BYTES,
 2*TEST_WORK_BYTES,2*TEST_MAX_ITERATIONS*TEST_COUNT*sizeof(struct test_record),0x240,0x248,56
};
static inline uint64_t acquire(const uint64_t *p){return __atomic_load_n(p,__ATOMIC_ACQUIRE);}
static inline void release(uint64_t *p,uint64_t v){__atomic_store_n(p,v,__ATOMIC_RELEASE);}
static void fail(struct shared *p,unsigned h,unsigned err) __attribute__((noreturn));
static void fail(struct shared *p,unsigned h,unsigned err){test_io_fence();release(&p->status[h].error,err);release(&p->status[h].state,ERROR);platform_stop();}
static void wait_value(struct shared *p,unsigned h,const uint64_t *v,uint64_t wanted){
 uint64_t begin=platform_cycles();unsigned polls=0;
 while(acquire(v)!=wanted){if((++polls&255)==0){if(acquire(&p->status[h^1].error))fail(p,h,ERR_PEER);if(platform_cycles()-begin>=p->cfg.wait_cycles)fail(p,h,ERR_TIMEOUT);}platform_relax();}
}
static void guard(volatile uint8_t *v,size_t n){for(unsigned j=0;j<64;j++){(v-64)[j]=0xc3;v[n+j]=0x3c;}test_io_fence();}
static int guarded(const volatile uint8_t *v,size_t n){for(unsigned j=0;j<64;j++)if((v-64)[j]!=0xc3 || v[n+j]!=0x3c)return 0;return 1;}
static void fill(volatile uint8_t *v,size_t n,uint32_t s,int small){for(size_t k=0;k<n;k++)v[k]=test_pattern(k,s,small);test_io_fence();}
static void bytes_copy(const void *src,void *dst,size_t n){
 size_t full=n/64;if(full)test_gemmini_copy(src,dst,full,64,64);
 if(n%64)test_gemmini_copy((const uint8_t*)src+full*64,(uint8_t*)dst+full*64,1,n%64,64);
}
static void global_probe(struct shared *p){
 /* Deliberately only reads aligned 64-bit control registers. No DMA writes. */
 for(unsigned h=0;h<2;h++)for(unsigned r=0;r<7;r++){
  uint64_t v=control_read(h,r);p->controls[0][h][r]=v;if(v)fail(p,0,ERR_CONTROL);
 }
 const size_t offsets[]={0,8,16,24,4096,65536,131072,262144,524288,SPM_BYTES-8};
 for(unsigned h=0;h<2;h++)for(unsigned j=0;j<sizeof(offsets)/sizeof(offsets[0]);j++)
  *(volatile uint64_t*)(spm_base(h)+offsets[j])=UINT64_C(0x5a6900001234abcd)^((uint64_t)h<<48)^offsets[j];
 test_io_fence();
 for(unsigned h=0;h<2;h++)for(unsigned j=0;j<sizeof(offsets)/sizeof(offsets[0]);j++){
  uint64_t e=UINT64_C(0x5a6900001234abcd)^((uint64_t)h<<48)^offsets[j];
  if(*(volatile uint64_t*)(spm_base(h)+offsets[j])!=e)fail(p,0,ERR_ALIAS);
 }
 p->alias_ok=1;
}
static void execute(unsigned h,const struct test_case *t,uint32_t seed,volatile uint8_t *out){
 struct workspace *w=&test_work[h];size_t n=test_output_bytes(t);
 if(t->kind==KIND_CPU64){for(size_t k=0;k<n/8;k++)((volatile uint64_t*)out)[k]=test_expected_word(k,seed);test_io_fence();return;}
 guard(out,n);for(size_t k=0;k<n;k++)out[k]=0xa5;
 if(t->kind==KIND_WIDTH){
  for(size_t k=0;k<n;k+=t->rows){uint64_t v=0;for(unsigned j=0;j<t->rows;j++)v|=(uint64_t)test_pattern(k+j,seed,0)<<(8*j);
   if(t->rows==1)out[k]=(uint8_t)v;else if(t->rows==2)*(volatile uint16_t*)(out+k)=(uint16_t)v;else if(t->rows==4)*(volatile uint32_t*)(out+k)=(uint32_t)v;else *(volatile uint64_t*)(out+k)=v;
  }test_io_fence();return;
 }
 uint8_t *source=w->source+64,*mirror=w->mirror+64;guard(mirror,n);memset(mirror,0xa5,n);
 if(t->kind==KIND_COPY){
  guard(source,n);for(size_t k=0;k<n;k++)source[k]=test_expected_byte(t,k,seed);test_io_fence();
  test_gemmini_copy(source,(void*)out,t->rows,t->cols,t->stride);
  /* Immediate Gemmini consumption before the CPU inspects the SPM result. */
  test_gemmini_copy((const void*)out,mirror,t->rows,t->cols,t->stride);return;
 }
 volatile uint8_t *a=(volatile uint8_t*)(spm_base(h)+64),*b=(volatile uint8_t*)(spm_base(h)+SLOT_FRAME+64);
 size_t na=0,nb=0,nd=0;int small=t->kind==KIND_MATMUL || t->kind==KIND_CONV;
 struct qcase q={.op=t->kind==KIND_MATMUL?OP_MATMUL:t->kind==KIND_CONV?OP_CONV:t->kind==KIND_RESADD?OP_RESADD:OP_AVERAGE,
  .bytes=n,.I=t->I,.J=t->J,.K=t->K,.in_h=t->in_h,.in_w=t->in_w,.in_c=t->in_c,.out_c=t->out_c,.out_h=t->out_h,.out_w=t->out_w,
  .kernel=t->kernel,.padding=t->padding,.stride=t->conv_stride,.relu=t->relu,.pool_size=t->pool_size,.pool_stride=t->pool_stride,.pool_padding=t->pool_padding,
  .full_C=t->full_C,.scale_bits=t->scale_bits,.a_scale_bits=t->a_scale_bits,.a=(const uint8_t*)a};
 if(t->kind==KIND_MATMUL){na=(size_t)t->I*t->K;nb=(size_t)t->K*t->J;nd=t->J;}
 else if(t->kind==KIND_CONV){na=(size_t)t->in_h*t->in_w*t->in_c;nb=(size_t)t->kernel*t->kernel*t->in_c*t->out_c;nd=t->out_c;}
 else if(t->kind==KIND_RESADD){na=nb=(size_t)t->I*t->J;}
 else na=(size_t)t->in_h*t->in_w*t->in_c;
 guard(a,na);fill(a,na,seed,small);
 if(t->kind==KIND_RESADD){guard(b,nb);fill(b,nb,seed+7,0);q.b=(const uint8_t*)b;}
 else if(nb){fill(w->weights,nb,seed+7,1);q.b=w->weights;}
 for(size_t j=0;j<nd;j++)w->bias[j]=(int32_t)((j*3+seed+11)%17)-8;
 if(nd)q.d=(const uint8_t*)w->bias;
 test_io_fence();model_run(&q,(void*)out);
 bytes_copy((const void*)out,mirror,n);
}
static void verify(struct shared *p,unsigned h,const struct test_case *t,uint32_t seed,volatile uint8_t *out,struct test_record *r){
 size_t n=test_output_bytes(t);uint64_t ah=UINT64_C(14695981039346656037),eh=ah;
 for(size_t k=0;k<n;k++){
  uint8_t a=out[k],e=test_expected_byte(t,k,seed);ah=(ah^a)*UINT64_C(1099511628211);eh=(eh^e)*UINT64_C(1099511628211);
  if(a!=e){r->first_bad=k;r->expected_byte=e;r->actual_byte=a;p->status[h].first_bad=k;p->status[h].expected_byte=e;p->status[h].actual_byte=a;p->status[h].mismatches=1;fail(p,h,ERR_DATA);}
 }
 r->actual_hash=ah;r->reference_hash=eh;
 if(t->kind!=KIND_CPU64 && !guarded(out,n))fail(p,h,ERR_GUARD);
 if(t->kind>=KIND_COPY){
  uint8_t *mirror=test_work[h].mirror+64;
  if(!guarded(mirror,n))fail(p,h,ERR_GUARD);
  for(size_t k=0;k<n;k++)if(mirror[k]!=out[k]){p->status[h].first_bad=k;p->status[h].expected_byte=out[k];p->status[h].actual_byte=mirror[k];p->status[h].mismatches=1;fail(p,h,ERR_DATA);}
 }
 if(t->kind==KIND_COPY){
  if(!guarded(test_work[h].source+64,n))fail(p,h,ERR_GUARD);
  for(size_t k=0;k<n;k++)if(test_work[h].source[64+k]!=test_expected_byte(t,k,seed))fail(p,h,ERR_DATA);
 }
 if(t->kind>=KIND_MATMUL){
  size_t na=t->kind==KIND_MATMUL?(size_t)t->I*t->K:t->kind==KIND_RESADD?(size_t)t->I*t->J:(size_t)t->in_h*t->in_w*t->in_c;
  const volatile uint8_t *a=(const volatile uint8_t*)(spm_base(h)+64);
  if(!guarded(a,na))fail(p,h,ERR_GUARD);
  for(size_t k=0;k<na;k++)if(a[k]!=test_pattern(k,seed,t->kind==KIND_MATMUL || t->kind==KIND_CONV))fail(p,h,ERR_DATA);
  if(t->kind==KIND_RESADD){const volatile uint8_t *b=(const volatile uint8_t*)(spm_base(h)+SLOT_FRAME+64);if(!guarded(b,na))fail(p,h,ERR_GUARD);for(size_t k=0;k<na;k++)if(b[k]!=test_pattern(k,seed+7,0))fail(p,h,ERR_DATA);}
 }
 r->guard_ok=1;r->state=TEST_PASS;
}
void rocket_main(uintptr_t hart){
 if(hart>1)platform_stop();unsigned h=(unsigned)hart;struct shared *p=platform_shared();struct status *s=&p->status[h];uint64_t epoch=p->cfg.run_id;
 s->hart=h;s->run_id=epoch;s->mode=p->cfg.active_mask;s->first_bad=UINT64_MAX;s->bss_start=platform_image_symbol(0);s->bss_end=platform_image_symbol(1);s->image_end=platform_image_symbol(2);release(&s->state,RUNNING);
 if(h==0){
  const struct config *c=&p->cfg;
  if(c->magic!=CONFIG_MAGIC || c->abi!=ABI_VERSION || !epoch || c->active_mask<1 || c->active_mask>3 || c->suite>1 || !c->iterations || c->iterations>TEST_MAX_ITERATIONS || !c->wait_cycles || c->wait_cycles>UINT64_C(0x7fffffffffffffff))fail(p,h,ERR_CONFIG);
  for(unsigned j=0;j<9;j++)if(c->reserved[j])fail(p,h,ERR_CONFIG);
  global_probe(p);release(&p->boot.value,epoch);
 }else wait_value(p,h,&p->boot.value,epoch);
 for(unsigned iteration=0;iteration<p->cfg.iterations;iteration++){
  uint64_t token=iteration+1;release(&p->ready[h].value,token);
  if(h==0){wait_value(p,h,&p->ready[1].value,token);release(&p->go.value,token);}else wait_value(p,h,&p->go.value,token);
  if(p->cfg.active_mask&(1u<<h))for(unsigned ci=0;ci<TEST_COUNT;ci++){
   const struct test_case *t=&test_cases[ci];if(!p->cfg.suite && t->kind>KIND_WIDTH)continue;
   struct test_record *r=&test_records[h][iteration][ci];uint32_t seed=test_seed(h,iteration,ci);volatile uint8_t *out=(volatile uint8_t*)(spm_base(h)+t->offset);
   s->task_index=iteration;s->stage=ci+1;r->hart=h;r->iteration=iteration;r->case_index=ci;r->output_address=(uintptr_t)out;r->output_bytes=test_output_bytes(t);r->seed=seed;r->kind=t->kind;r->first_bad=UINT64_MAX;
   uint64_t begin=platform_cycles();execute(h,t,seed,out);verify(p,h,t,seed,out,r);r->cycles=platform_cycles()-begin;
   s->local_cycles+=r->cycles;s->tasks_done++;s->verified_tasks++;s->checked_stages++;
  }
  s->iterations_done++;release(&p->done[h].value,token);
  if(h==0){wait_value(p,h,&p->done[1].value,token);release(&p->finish.value,token);}else wait_value(p,h,&p->finish.value,token);
 }
 if(h==0)for(unsigned instance=0;instance<2;instance++)for(unsigned reg=0;reg<7;reg++){
  uint64_t value=control_read(instance,reg);p->controls[1][instance][reg]=value;if(value)fail(p,h,ERR_CONTROL);
 }
 test_io_fence();release(&s->state,DONE);platform_stop();
}
