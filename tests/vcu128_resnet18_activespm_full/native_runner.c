/* POSIX test harness for the SAME runtime. Native ticks are nanoseconds.
 * The synthetic backend tests lifetime/ABI/barriers, not quantized arithmetic.
 * NUMERIC_REFERENCE selects the independent scalar numerical backend instead.
 */
#include "platform.h"
#include "generated/baseline_generated.h"
#include <assert.h>
#include <pthread.h>
#include <sched.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
static struct shared data;
static _Thread_local unsigned this_hart;
static uint64_t calls[2];
static const char *injection="none";
static uint8_t spm[2][SPM_BYTES] __attribute__((aligned(64)));
uintptr_t native_spm_base(unsigned h){return (uintptr_t)spm[!strcmp(injection,"alias")?0:h];}
uint64_t native_spm_control(unsigned h,unsigned r){return !strcmp(injection,"control") && h==1 && r==4?1:0;}
struct shared *platform_shared(void){return &data;}
uintptr_t platform_hart(void){return this_hart;}
uint64_t platform_cycles(void){struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return (uint64_t)t.tv_sec*1000000000+t.tv_nsec;}
void platform_relax(void){sched_yield();}
uint64_t platform_image_symbol(unsigned which){return which==0?0x82000000:0x83000000;}
void platform_stop(void){pthread_exit(NULL);__builtin_unreachable();}
static unsigned index_of(const struct qcase *c){
 for(unsigned i=0;i<BASE_STAGE_COUNT;i++)if(c->golden==baseline_stages[i].golden)return i;
 abort();
}
void native_enter_model(const struct qcase *c){
 unsigned i=index_of(c),ti=i/30,h=this_hart;
 unsigned expected=data.cfg.mode==MODE_SERIAL?0:data.cfg.mode==MODE_HART1?1:((ti&1)^(unsigned)data.cfg.swap);
 assert(h==expected);calls[h]++;
}
void native_leave_model(const struct qcase *c,void *out){
 unsigned i=index_of(c);
 if(i==0 && !strcmp(injection,"data"))((uint8_t*)out)[0]^=1;
 if(i==0 && !strcmp(injection,"guard"))((uint8_t*)out)[c->bytes]=0;
 sched_yield();
}
#ifndef NUMERIC_REFERENCE
void model_run(const struct qcase *c,void *out){
 native_enter_model(c);unsigned index=index_of(c);int bad=0;
 const uint8_t *inputs[3]={c->a,c->b,c->d};
 for(unsigned k=0;k<3;k++){
  int parent=baseline_parents[index][k];
  if(parent>=0 && memcmp(inputs[k],baseline_stages[parent].golden,baseline_stages[parent].bytes))bad=1;
 }
 memcpy(out,c->golden,c->bytes);
 if(bad)((uint8_t*)out)[0]^=1; /* propagate input corruption through the graph */
 native_leave_model(c,out);
}
#endif
static void *worker(void *arg){this_hart=(unsigned)(uintptr_t)arg;rocket_main(this_hart);return NULL;}
int main(int argc,char **argv){
 if(argc<6)return 2;
 unsigned mode=!strcmp(argv[1],"dual")?MODE_DUAL:!strcmp(argv[1],"hart1")?MODE_HART1:MODE_SERIAL,check=!strcmp(argv[2],"perf"),swap=(unsigned)atoi(argv[3]),warm=(unsigned)atoi(argv[4]),repeats=(unsigned)atoi(argv[5]);
 if(argc>6)injection=argv[6];
 unsigned storage=argc>7?(unsigned)atoi(argv[7]):0,probe=argc>8?(unsigned)atoi(argv[8]):0;
 memset(spm,0x5a,sizeof spm);
 data.cfg=(struct config){.magic=CONFIG_MAGIC,.abi=ABI_VERSION,.run_id=0x123456789abcdef0,.mode=mode,.swap=swap,.task_count=BASE_TASK_COUNT,.wait_cycles=UINT64_C(200000000000),.check_mode=check,.warmup=warm,.repeats=repeats,.storage=storage,.probe_spm=probe};
 pthread_t threads[2];assert(!pthread_create(&threads[1],NULL,worker,(void*)1));assert(!pthread_create(&threads[0],NULL,worker,(void*)0));
 assert(!pthread_join(threads[0],NULL));assert(!pthread_join(threads[1],NULL));
 if(strcmp(injection,"none")){
  unsigned err=!strcmp(injection,"guard")?ERR_GUARD:!strcmp(injection,"control")?ERR_CONTROL:ERR_DATA;int found=0;
  for(unsigned h=0;h<2;h++)if(data.status[h].state==ERROR && data.status[h].error==err)found=1;
  assert(found);printf("EXPECTED ERROR PASS: %s\n",injection);return 0;
 }
 for(unsigned h=0;h<2;h++){
  struct status *s=&data.status[h];unsigned owned=0;
  for(unsigned ti=0;ti<BASE_TASK_COUNT;ti++)if((mode==MODE_SERIAL?0:mode==MODE_HART1?1:((ti&1)^swap))==h)owned++;
  assert(s->state==DONE && !s->error && !s->mismatches);
  assert(s->tasks_done==owned*repeats && s->verified_tasks==owned);
  assert(s->checked_stages==(check?0:owned*30*(warm+repeats)));
  assert(s->iterations_done==repeats && s->warmup_done==warm);
  assert(calls[h]==owned*30*(warm+repeats));
  assert(data.ready[h].value==warm+repeats && data.done[h].value==warm+repeats);
  if(storage && !owned)for(size_t j=0;j<SPM_BYTES;j++)assert(spm[h][j]==0x5a);
 }
 uint64_t sum=0;for(unsigned i=0;i<repeats;i++){assert(data.iteration_cycles[i]);sum+=data.iteration_cycles[i];}
 assert(sum==data.makespan && data.boot.value==data.cfg.run_id && data.go.value==warm+repeats && data.finish.value==warm+repeats);
 for(unsigned ti=0;ti<BASE_TASK_COUNT;ti++){
  struct task_result *r=&baseline_results[ti];const struct qcase *last=&baseline_stages[ti*30+29];
  assert(r->owner==(mode==MODE_SERIAL?0:mode==MODE_HART1?1:((ti&1)^swap)) && r->computed==repeats && r->verified==1 && r->guard_ok && !r->mismatches);
  assert(!memcmp((const void*)(uintptr_t)r->output_address,last->golden,4000));
 }
 if(getenv("BASELINE_SNAPSHOT_DIR")){
  char symbol_path[4096];snprintf(symbol_path,sizeof symbol_path,"%s/symbols.json",getenv("BASELINE_SNAPSHOT_DIR"));FILE *sf=fopen(symbol_path,"w");assert(sf);fprintf(sf,"{\"baseline_work\":%llu,\"baseline_outputs\":%llu}\n",(unsigned long long)(uintptr_t)baseline_work,(unsigned long long)(uintptr_t)baseline_outputs);fclose(sf);
  if(storage)for(unsigned h=0;h<2;h++)data.status[h].work_base=h?SPM1_BASE:SPM0_BASE;
  char path[4096];snprintf(path,sizeof path,"%s/shared.bin",getenv("BASELINE_SNAPSHOT_DIR"));FILE *f=fopen(path,"wb");assert(f);assert(fwrite(&data,1,sizeof data,f)==sizeof data);fclose(f);
  snprintf(path,sizeof path,"%s/results.bin",getenv("BASELINE_SNAPSHOT_DIR"));f=fopen(path,"wb");assert(f);assert(fwrite(baseline_results,1,sizeof(struct task_result)*BASE_TASK_COUNT,f)==sizeof(struct task_result)*BASE_TASK_COUNT);fclose(f);
 }
 printf("NATIVE RUNTIME PASS: %s %s swap=%u warmup=%u repeats=%u; slots, counters, guards, ownership, barriers\n",argv[1],argv[2],swap,warm,repeats);
 return 0;
}
