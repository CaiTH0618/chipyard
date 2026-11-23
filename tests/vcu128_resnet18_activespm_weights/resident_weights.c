#include "platform.h"
#include "generated/baseline_generated.h"

static void fence_memory(void) {
#ifndef NATIVE_TEST
 __asm__ volatile("fence iorw, iorw" ::: "memory");
#else
 __atomic_thread_fence(__ATOMIC_SEQ_CST);
#endif
}
static void weight_fail(struct shared *p,unsigned h,unsigned code,unsigned entry,
                        uint64_t byte,unsigned expected,unsigned actual) {
 struct weight_status *s=&p->weights[h];
 s->first_bad_entry=entry;s->first_bad_byte=byte;
 s->expected_byte=expected;s->actual_byte=actual;
 __atomic_store_n(&p->status[h].error,code,__ATOMIC_RELEASE);
 __atomic_store_n(&p->status[h].state,ERROR,__ATOMIC_RELEASE);
 platform_stop();
}
static void guards(uintptr_t address,size_t bytes) {
 volatile uint8_t *dst=(volatile uint8_t *)address;
 for(unsigned j=0;j<64;j++){dst[(ptrdiff_t)j-64]=0xc3;dst[bytes+j]=0x3c;}
}
void resident_check(struct shared *p,unsigned h,uintptr_t base,unsigned final) {
 struct weight_status *s=&p->weights[h];uint64_t begin=platform_cycles();
 for(unsigned i=0;i<BASE_WEIGHT_COUNT;i++) {
  const struct resident_weight *w=&baseline_resident_weights[i];
  const volatile uint8_t *dst=(const volatile uint8_t *)(base+w->data_offset);
  for(unsigned j=0;j<64;j++) {
   unsigned a=dst[(ptrdiff_t)j-64],b=dst[w->bytes+j];
   if(a!=0xc3)weight_fail(p,h,ERR_WEIGHT_GUARD,i,j,0xc3,a);
   if(b!=0x3c)weight_fail(p,h,ERR_WEIGHT_GUARD,i,w->bytes+j,0x3c,b);
  }
  for(size_t j=0;j<w->bytes;j++) {
   unsigned actual=dst[j],expected=w->source[j];
   if(actual!=expected)weight_fail(p,h,ERR_WEIGHT_DATA,i,j,expected,actual);
  }
 }
 fence_memory();uint64_t elapsed=platform_cycles()-begin;
 if(final){s->final_verify_cycles=elapsed;s->final_verified=1;}
 else{s->verify_cycles=elapsed;s->verified=1;}
 s->guard_ok=1;
}
void resident_init(struct shared *p,unsigned h,uintptr_t base) {
 struct weight_status *s=&p->weights[h];uint64_t begin=platform_cycles();
 s->first_bad_entry=UINT64_MAX;s->first_bad_byte=UINT64_MAX;
 s->resident_base=base+BASE_WEIGHT_REGION_OFFSET;
 s->bytes=BASE_WEIGHT_BYTES;s->entries=BASE_WEIGHT_COUNT;
 for(unsigned i=0;i<BASE_WEIGHT_COUNT;i++) {
  const struct resident_weight *w=&baseline_resident_weights[i];
  uintptr_t address=base+w->data_offset;guards(address,w->bytes);
  /* RV64 scalar copy, no ActiveSPM DMA. Volatile prevents optimizing away
   * real SRAM accesses. Generator aligns all source/destination payloads. */
  volatile uint64_t *dst=(volatile uint64_t *)address;
  const uint64_t *src=(const uint64_t *)w->source;
  size_t n=w->bytes/8;
  for(size_t j=0;j<n;j++)dst[j]=src[j];
  volatile uint8_t *tail=(volatile uint8_t *)(address+n*8);
  for(size_t j=n*8;j<w->bytes;j++)*tail++=w->source[j];
 }
 fence_memory();s->copy_cycles=platform_cycles()-begin;
#ifdef NATIVE_TEST
 native_weight_hook(0,h);
#endif
 resident_check(p,h,base,0);s->setup_cycles=platform_cycles()-begin;
}
