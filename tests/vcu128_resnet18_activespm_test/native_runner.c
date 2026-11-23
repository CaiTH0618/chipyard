/* Host-only protocol/reference harness. Does not model actual TileLink/RoCC. */
#include "platform.h"
#include "generated/test_generated.h"
#include <assert.h>
#include <pthread.h>
#include <sched.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
static struct shared shared;
static uint8_t ram[2][SPM_BYTES] __attribute__((aligned(64)));
static _Thread_local unsigned local_hart;
static const char *inject="none";
extern struct test_record test_records[2][TEST_MAX_ITERATIONS][TEST_COUNT];
extern uint8_t test_work[];
struct shared *platform_shared(void){return &shared;}
uint64_t platform_cycles(void){struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return (uint64_t)t.tv_sec*1000000000+t.tv_nsec;}
uintptr_t platform_hart(void){return local_hart;}
void platform_relax(void){sched_yield();}
uint64_t platform_image_symbol(unsigned w){return w?0x83000000:0x82000000;}
void platform_stop(void){pthread_exit(NULL);__builtin_unreachable();}
uintptr_t native_spm_base(unsigned h){return (uintptr_t)ram[!strcmp(inject,"alias")?0:h];}
uint64_t native_control_read(unsigned h,unsigned r){return !strcmp(inject,"control") && h==1 && r==4?1:0;}
static void input_check(const volatile uint8_t *p,size_t n,uint32_t seed,int small){for(size_t k=0;k<n;k++)assert(p[k]==test_pattern(k,seed,small));}
void model_run(const struct qcase *q,void *output){
 unsigned h=local_hart,i=(unsigned)shared.status[h].stage-1,r=(unsigned)shared.status[h].task_index;
 const struct test_case *t=&test_cases[i];uint32_t s=test_seed(h,r,i);size_t na=0,nb=0;
 assert(shared.cfg.active_mask&(1u<<h));assert((uintptr_t)output==native_spm_base(h)+t->offset);
 if(t->kind==KIND_MATMUL){na=(size_t)t->I*t->K;nb=(size_t)t->K*t->J;}
 else if(t->kind==KIND_CONV){na=(size_t)t->in_h*t->in_w*t->in_c;nb=(size_t)t->kernel*t->kernel*t->in_c*t->out_c;}
 else if(t->kind==KIND_RESADD)na=nb=(size_t)t->I*t->J;
 else na=(size_t)t->in_h*t->in_w*t->in_c;
 input_check(q->a,na,s,t->kind==KIND_MATMUL || t->kind==KIND_CONV);
 if(nb)input_check(q->b,nb,s+7,t->kind!=KIND_RESADD);
 for(size_t k=0;k<test_output_bytes(t);k++)((uint8_t*)output)[k]=test_expected_byte(t,k,s);
 if(!strcmp(inject,"data"))((uint8_t*)output)[0]^=1;
 if(!strcmp(inject,"guard"))((uint8_t*)output)[test_output_bytes(t)]=0;
}
void test_gemmini_copy(const void *src,void *dst,size_t rows,size_t cols,size_t stride){
 assert(shared.cfg.active_mask&(1u<<local_hart));
 for(size_t r=0;r<rows;r++)memcpy((uint8_t*)dst+r*stride,(const uint8_t*)src+r*stride,cols);
 if(!strcmp(inject,"copy"))((uint8_t*)dst)[0]^=1;
}
static void *worker(void *h){local_hart=(unsigned)(uintptr_t)h;rocket_main(local_hart);return NULL;}
int main(int argc,char **argv){
 if(argc!=6)return 2;
 unsigned mask=(unsigned)atoi(argv[1]),suite=(unsigned)atoi(argv[2]),iters=(unsigned)atoi(argv[3]);inject=argv[4];
 shared.cfg=(struct config){.magic=CONFIG_MAGIC,.abi=ABI_VERSION,.run_id=0x123456789abcdef0,.active_mask=mask,.suite=suite,.iterations=iters,.wait_cycles=UINT64_C(20000000000)};
 pthread_t t[2];assert(!pthread_create(&t[1],NULL,worker,(void*)1));assert(!pthread_create(&t[0],NULL,worker,(void*)0));assert(!pthread_join(t[0],NULL));assert(!pthread_join(t[1],NULL));
 if(strcmp(inject,"none")){
  unsigned error=!strcmp(inject,"alias")?ERR_ALIAS:!strcmp(inject,"control")?ERR_CONTROL:!strcmp(inject,"guard")?ERR_GUARD:ERR_DATA;
  assert((shared.status[0].state==ERROR && shared.status[0].error==error)||(shared.status[1].state==ERROR && shared.status[1].error==error));
  printf("EXPECTED ERROR PASS: %s\n",inject);return 0;
 }
 char path[4096];snprintf(path,sizeof path,"%s/shared.bin",argv[5]);FILE *f=fopen(path,"wb");assert(f);assert(fwrite(&shared,1,sizeof shared,f)==sizeof shared);fclose(f);
 struct test_record saved[2][TEST_MAX_ITERATIONS][TEST_COUNT];memcpy(saved,test_records,sizeof saved);
 for(unsigned h=0;h<2;h++)for(unsigned r=0;r<TEST_MAX_ITERATIONS;r++)for(unsigned i=0;i<TEST_COUNT;i++)if(saved[h][r][i].state)saved[h][r][i].output_address=(h?SPM1_BASE:SPM0_BASE)+test_cases[i].offset;
 snprintf(path,sizeof path,"%s/records.bin",argv[5]);f=fopen(path,"wb");assert(f);assert(fwrite(saved,1,sizeof saved,f)==sizeof saved);fclose(f);
 snprintf(path,sizeof path,"%s/work.bin",argv[5]);f=fopen(path,"wb");assert(f);assert(fwrite(test_work,1,2*TEST_WORK_BYTES,f)==2*TEST_WORK_BYTES);fclose(f);
 printf("NATIVE ACTIVESPM RUNTIME PASS mask=%u suite=%u iterations=%u\n",mask,suite,iters);return 0;
}
