#ifndef VCU128_ACTIVESPM_TEST_H
#define VCU128_ACTIVESPM_TEST_H
#define APP_BASE 0x80000000
#define APP_END 0x8f000000
#define STACK0_BOTTOM 0x8fc00000
#define STACK0_TOP 0x8fd00000
#define STACK1_BOTTOM 0x8fe00000
#define STACK1_TOP 0x8ff00000
#define SHARED_BASE 0x8ff00000
#define SHARED_BYTES 4096
#define STATUS_OFFSET 0x400
#define LOG0_BASE 0x8ff10000
#define LOG1_BASE 0x8ff30000
#define LOG_SIZE 65536
#define HBM_END 0x90000000
#define ABI_VERSION 1
#define PREPARED 1
#define RUNNING 2
#define DONE 3
#define ERROR 4
#define MODE_SERIAL 0
#define MODE_DUAL 1
#define CHECK_DEBUG 0
#define CHECK_PERF 1
#define MAX_REPEATS 32
#define MAX_WARMUP 32
#define ERR_CONFIG 1
#define ERR_TIMEOUT 2
#define ERR_PEER 3
#define ERR_DATA 4
#define ERR_GUARD 5
#define ERR_TRAP 7
#define OP_MATMUL 1
#define OP_CONV 2
#define OP_RESADD 3
#define OP_AVERAGE 4
#define REF_KEEP (-1)
#define REF_FINAL (-2)
#ifndef __ASSEMBLER__
#include <stdint.h>
#include <stddef.h>
#define CONFIG_MAGIC UINT64_C(0x4153504d43464731)
#define BUILD_MAGIC UINT64_C(0x4153504d454c4631)
struct qcase {
 const uint8_t *a,*b,*d,*golden;
 uint32_t bytes,op,I,J,K;
 uint32_t in_h,in_w,in_c,out_c,out_h,out_w;
 uint32_t stride,padding,kernel,relu,pool_size,pool_stride,pool_padding,full_C;
 uint32_t scale_bits,a_scale_bits;
};
struct refs { int32_t a,b,d,out; };
struct task { uint32_t task_id,first_stage,stage_count,output_offset; };
struct config {
 uint64_t magic,abi,run_id,active_mask,suite,iterations,wait_cycles;
 uint64_t reserved[9];
};
struct line { uint64_t value; uint8_t pad[56]; };
struct status {
 uint64_t state,hart,run_id,mode,error,task_index,stage,tasks_done,local_cycles,operator_cycles;
 uint64_t mcause,mepc,mtval,log_write,verified_tasks,mismatches,bss_start,bss_end,image_end;
 uint64_t checked_stages,iterations_done,warmup_done,first_bad,expected_byte,actual_byte;
 uint64_t reserved[7];
};
struct shared {
 struct config cfg;
 struct line boot,ready[2],go,done[2],finish;
 uint64_t alias_ok;
 uint64_t controls[2][2][7]; /* before/after, instance, register */
 uint8_t gap[216];
 struct status status[2];
 uint8_t tail[2560];
} __attribute__((aligned(64)));
struct task_result {
 uint64_t task_id,owner,computed,verified,cycles,operator_cycles,checksum,mismatches;
 uint64_t first_stage,first_byte,guard_ok,run_id,output_address,output_bytes,checked_stages,reserved;
} __attribute__((aligned(64)));
_Static_assert(sizeof(struct qcase)==120,"RV64 stage ABI");
_Static_assert(sizeof(struct refs)==16,"reference ABI");
_Static_assert(sizeof(struct config)==128,"config ABI");
_Static_assert(sizeof(struct status)==256,"status ABI");
_Static_assert(offsetof(struct status,mcause)==80,"trap ABI");
_Static_assert(offsetof(struct shared,status)==STATUS_OFFSET,"status offset ABI");
_Static_assert(offsetof(struct shared,alias_ok)==0x240,"probe ABI");
_Static_assert(offsetof(struct shared,controls)==0x248,"control snapshot ABI");
_Static_assert(sizeof(struct shared)==SHARED_BYTES,"shared ABI");
_Static_assert(sizeof(struct task_result)==128,"task result ABI");
_Static_assert(__atomic_always_lock_free(8,0),"inline lock-free 64-bit atomics required");
#define ERR_CONTROL 8
#define ERR_ALIAS 9
#define SPM0_BASE 0x70000000
#define SPM1_BASE 0x70100000
#define SPM_BYTES 1048576
#define CTRL0_BASE 0x10050000
#define CTRL1_BASE 0x10051000
#define SLOT_BYTES 200704
#define SLOT_FRAME 200832
#define TEST_PASS 1
#define KIND_CPU64 1
#define KIND_WIDTH 2
#define KIND_COPY 3
#define KIND_MATMUL 4
#define KIND_CONV 5
#define KIND_RESADD 6
#define KIND_GAP 7
#define CASE_FIELDS 24
struct test_case {
 uint32_t kind,offset,rows,cols,stride,in_h,in_w,in_c,out_c,out_h,out_w;
 uint32_t kernel,padding,conv_stride,pool_size,pool_stride,pool_padding;
 uint32_t I,J,K,relu,full_C,scale_bits,a_scale_bits;
};
struct test_record {
 uint64_t state,hart,iteration,case_index,output_address,output_bytes;
 uint64_t actual_hash,reference_hash,cycles,first_bad,expected_byte,actual_byte;
 uint64_t guard_ok,seed,kind,reserved;
} __attribute__((aligned(64)));
_Static_assert(sizeof(struct test_case)==96,"case ABI");
_Static_assert(sizeof(struct test_record)==128,"record ABI");
extern const struct test_case test_cases[];
extern const uint8_t test_bundle_digest[32];
#ifdef NATIVE_TEST
uintptr_t native_spm_base(unsigned hart);
uint64_t native_control_read(unsigned instance,unsigned index);
#endif
static inline uintptr_t spm_base(unsigned hart){
#ifdef NATIVE_TEST
 return native_spm_base(hart);
#else
 return hart?SPM1_BASE:SPM0_BASE;
#endif
}
static inline void test_io_fence(void){
#ifdef NATIVE_TEST
 __atomic_thread_fence(__ATOMIC_SEQ_CST);
#else
 __asm__ volatile("fence iorw, iorw" ::: "memory");
#endif
}
static inline uint64_t control_read(unsigned instance,unsigned index){
#ifdef NATIVE_TEST
 return native_control_read(instance,index);
#else
 return ((volatile const uint64_t *)(uintptr_t)(instance?CTRL1_BASE:CTRL0_BASE))[index];
#endif
}
uint32_t test_seed(unsigned hart,unsigned iteration,unsigned index);
size_t test_output_bytes(const struct test_case *t);
uint64_t test_expected_word(size_t word,uint32_t seed);
uint8_t test_pattern(size_t index,uint32_t seed,int small);
uint8_t test_expected_byte(const struct test_case *t,size_t index,uint32_t seed);
void test_gemmini_copy(const void *src,void *dst,size_t rows,size_t cols,size_t stride);
void model_run(const struct qcase *c,void *output);
void rocket_main(uintptr_t hart) __attribute__((noreturn));
#endif
#endif
