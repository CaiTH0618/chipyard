#ifndef VCU128_RESNET18_ACTIVESPM_FULL_H
#define VCU128_RESNET18_ACTIVESPM_FULL_H
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
#define ABI_VERSION 4
#define PREPARED 1
#define RUNNING 2
#define DONE 3
#define ERROR 4
#define MODE_SERIAL 0
#define MODE_DUAL 1
#define MODE_HART1 2
#define STORAGE_HBM 0
#define STORAGE_SPM 1
#define ERR_CONTROL 8
#define ERR_WEIGHT_DATA 9
#define ERR_WEIGHT_GUARD 10
#define WEIGHTS_NONE 0
#define WEIGHTS_RESIDENT 1
#define SPM0_BASE 0x70000000
#define SPM1_BASE 0x70100000
#define SPM_BYTES 1048576
#define CTRL0_BASE 0x10050000
#define CTRL1_BASE 0x10051000
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
#define CONFIG_MAGIC UINT64_C(0x5231385343464734)
#define BUILD_MAGIC UINT64_C(0x52313853454c4634)
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
 uint64_t magic,abi,run_id,mode,swap,task_count,wait_cycles;
 uint64_t check_mode,warmup,repeats,storage,probe_spm,weight_mode,reserved[3];
};
struct line { uint64_t value; uint8_t pad[56]; };
struct status {
 uint64_t state,hart,run_id,mode,error,task_index,stage,tasks_done,local_cycles,operator_cycles;
 uint64_t mcause,mepc,mtval,log_write,verified_tasks,mismatches,bss_start,bss_end,image_end;
 uint64_t checked_stages,iterations_done,warmup_done,first_bad,expected_byte,actual_byte;
 uint64_t storage,work_base,work_guard_ok,reserved[4];
};
struct resident_weight { const uint8_t *source; uint64_t data_offset,bytes; };
struct weight_status {
 uint64_t copy_cycles,verify_cycles,setup_cycles,bytes,entries,verified,guard_ok,resident_base;
 uint64_t first_bad_entry,first_bad_byte,expected_byte,actual_byte,final_verified,final_verify_cycles,reserved[2];
};
struct shared {
 struct config cfg;
 struct line boot,ready[2],go,done[2],finish;
 uint64_t makespan;
 uint64_t iteration_cycles[MAX_REPEATS];
 uint8_t gap[184];
 struct status status[2];
 uint64_t controls_before[2][7],controls_after[2][7];
 uint8_t weight_gap[32];
 struct weight_status weights[2];
 uint64_t setup_makespan;
 uint8_t tail[2040];
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
_Static_assert(offsetof(struct shared,makespan)==0x240,"makespan ABI");
_Static_assert(offsetof(struct shared,iteration_cycles)==0x248,"iteration ABI");
_Static_assert(sizeof(struct shared)==SHARED_BYTES,"shared ABI");
_Static_assert(sizeof(struct task_result)==128,"task result ABI");
_Static_assert(__atomic_always_lock_free(8,0),"inline lock-free 64-bit atomics required");
_Static_assert(offsetof(struct shared,controls_before)==0x600,"control before ABI");
_Static_assert(offsetof(struct shared,controls_after)==0x670,"control after ABI");
_Static_assert(offsetof(struct shared,weights)==0x700,"weights ABI");
_Static_assert(offsetof(struct shared,setup_makespan)==0x800,"setup timing ABI");
_Static_assert(sizeof(struct weight_status)==128,"weight status ABI");
_Static_assert(sizeof(struct resident_weight)==24,"resident table ABI");
#ifdef NATIVE_TEST
uintptr_t native_spm_base(unsigned h);
uint64_t native_spm_control(unsigned instance,unsigned reg);
void native_weight_hook(unsigned phase,unsigned hart);
#endif
extern const struct qcase baseline_stages[];
extern const struct refs baseline_refs[];
extern const int32_t baseline_parents[][3];
extern const struct task baseline_tasks[];
extern const uint8_t baseline_blob[],baseline_blob_end[],baseline_bundle_digest[];
extern uint8_t baseline_work[],baseline_outputs[];
extern struct task_result baseline_results[];
extern const struct resident_weight baseline_resident_weights[];
extern const int32_t baseline_weight_refs[];
extern uint64_t baseline_stage_cycles[];
void resident_init(struct shared *p,unsigned h,uintptr_t spm_base);
void resident_check(struct shared *p,unsigned h,uintptr_t spm_base,unsigned final);
void model_run(const struct qcase *c,void *output);
void rocket_main(uintptr_t hart) __attribute__((noreturn));
#endif
#endif
