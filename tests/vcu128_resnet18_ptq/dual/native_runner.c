/* Same runtime, two POSIX threads, independent scalar model. Native times are ns. */
#include "platform.h"
#include "generated/dual_generated.h"
#include <assert.h>
#include <pthread.h>
#include <sched.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
static struct shared shared_data;
static _Thread_local unsigned this_hart;
static uint64_t first[2],last[2],calls[2],masks[2];
static int inject;
struct shared *platform_shared(void){return &shared_data;}
uintptr_t platform_hart(void){return this_hart;}
uint64_t platform_cycles(void){struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return (uint64_t)t.tv_sec*1000000000+t.tv_nsec;}
void platform_relax(void){sched_yield();}
uint64_t platform_image_symbol(unsigned w){return w==0?0x82000000:0x83000000;}
void platform_stop(void){pthread_exit(NULL);__builtin_unreachable();}
void native_enter_model(const struct qcase *c){
 unsigned h=this_hart,index=(unsigned)(c-dual_stages),ti=index/30;
 unsigned expected=shared_data.cfg.mode==MODE_SERIAL?0:((ti&1)^(unsigned)shared_data.cfg.swap);
 assert(h==expected && ti<DUAL_TASK_COUNT);
 if(!first[h])first[h]=platform_cycles();calls[h]++;masks[h]|=UINT64_C(1)<<ti;
}
void native_leave_model(const struct qcase *c,void *out){
 unsigned index=(unsigned)(c-dual_stages);
 if(inject && index==59)((uint8_t*)out)[0]^=1;
 last[this_hart]=platform_cycles();
}
static void *worker(void *arg){this_hart=(unsigned)(uintptr_t)arg;rocket_main(this_hart);return NULL;}
static uint64_t hash(const uint8_t *p,size_t n){uint64_t x=UINT64_C(14695981039346656037);while(n--){x^=*p++;x*=UINT64_C(1099511628211);}return x;}
int main(int argc,char **argv){
 if(argc<3)return 2;
 unsigned mode=!strcmp(argv[1],"dual"),swap=(unsigned)atoi(argv[2]);inject=argc>3;
 assert(swap<=1 && (mode || !swap));
 shared_data.cfg=(struct config){.magic=CONFIG_MAGIC,.abi=ABI_VERSION,.run_id=0x123456789abcdef0,.mode=mode,.swap=swap,.task_count=DUAL_TASK_COUNT,.wait_cycles=UINT64_C(2000000000000)};
 for(unsigned h=0;h<2;h++)shared_data.status[h].state=PREPARED;
 uint64_t before=hash(dual_blob,dual_blob_end-dual_blob);
 pthread_t threads[2];assert(!pthread_create(&threads[1],NULL,worker,(void*)1));assert(!pthread_create(&threads[0],NULL,worker,(void*)0));
 assert(!pthread_join(threads[0],NULL));assert(!pthread_join(threads[1],NULL));
 assert(before==hash(dual_blob,dual_blob_end-dual_blob));
 if(inject){assert(shared_data.status[0].state==ERROR || shared_data.status[1].state==ERROR);printf("NATIVE ERROR INJECTION PASS: incorrect final logits rejected by shared runtime\n");return 0;}
 uint64_t epoch=shared_data.cfg.run_id;
 assert(shared_data.boot.value==epoch && shared_data.go.value==epoch && shared_data.finish.value==epoch && shared_data.makespan);
 for(unsigned h=0;h<2;h++){
  assert(shared_data.ready[h].value==epoch && shared_data.done[h].value==epoch);
  struct status *s=&shared_data.status[h];unsigned count=0;uint64_t expected_mask=0;
  for(unsigned i=0;i<DUAL_TASK_COUNT;i++)if((mode?((i&1)^swap):0)==h){count++;expected_mask|=UINT64_C(1)<<i;}
  assert(s->state==DONE && !s->error && !s->mismatches && s->tasks_done==count && s->verified_tasks==count);
  assert(calls[h]==count*30 && masks[h]==expected_mask);
 }
 for(unsigned i=0;i<DUAL_TASK_COUNT;i++){
  struct task_result *r=&dual_results[i];unsigned h=mode?((i&1)^swap):0;
  assert(r->task_id==dual_tasks[i].task_id && r->owner==h && r->computed==1 && r->verified==1 && !r->mismatches && r->guard_ok && r->run_id==epoch && r->output_bytes==4000);
 }
 if(mode)assert(first[0]<last[1] && first[1]<last[0]);
 printf("NATIVE DUAL RUNTIME PASS: mode=%s swap=%u tasks=%u, %u chained checkpoints, exact outputs/guards/ownership/epochs; %s\n",argv[1],swap,DUAL_TASK_COUNT,DUAL_STAGE_COUNT,mode?"thread compute intervals overlap":"hart1 performs no model calls");
 return 0;
}
