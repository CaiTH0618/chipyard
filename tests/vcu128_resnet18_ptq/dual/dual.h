#ifndef VCU128_RESNET18_DUAL_H
#define VCU128_RESNET18_DUAL_H
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
#ifndef __ASSEMBLER__
#include <stdint.h>
#include <stddef.h>
#define CONFIG_MAGIC UINT64_C(0x5231384455434631)
#define BUILD_MAGIC UINT64_C(0x5231384455454c31)
struct qcase {
 const uint8_t *a,*b,*d,*golden;
 uint32_t bytes,op,I,J,K;
 uint32_t in_h,in_w,in_c,out_c,out_h,out_w;
 uint32_t stride,padding,kernel,relu,pool_size,pool_stride,pool_padding,full_C;
 uint32_t scale_bits,a_scale_bits;
};
struct buffer { uint32_t offset,bytes; };
struct task { uint32_t task_id,first_stage,stage_count,arena_offset; };
struct config {
 uint64_t magic,abi,run_id,mode,swap,task_count,wait_cycles;
 uint64_t reserved[9];
};
struct line { uint64_t value; uint8_t pad[56]; };
struct status {
 uint64_t state,hart,run_id,mode,error,task_index,stage,tasks_done,local_cycles,operator_cycles;
 uint64_t mcause,mepc,mtval,log_write,verified_tasks,mismatches,bss_start,bss_end,image_end;
 uint64_t reserved[13];
};
struct shared {
 struct config cfg;
 struct line boot,ready[2],go,done[2],finish;
 uint64_t makespan;
 uint8_t gap[440];
 struct status status[2];
 uint8_t tail[2560];
} __attribute__((aligned(64)));
struct task_result {
 uint64_t task_id,owner,computed,verified,cycles,operator_cycles,checksum,mismatches;
 uint64_t first_stage,first_byte,guard_ok,run_id,output_address,output_bytes,reserved[2];
} __attribute__((aligned(64)));
_Static_assert(sizeof(struct qcase)==120,"RV64 stage ABI");
_Static_assert(sizeof(struct config)==128,"config ABI");
_Static_assert(sizeof(struct status)==256,"status ABI");
_Static_assert(offsetof(struct status,mcause)==80,"trap ABI");
_Static_assert(offsetof(struct shared,status)==STATUS_OFFSET,"status offset ABI");
_Static_assert(offsetof(struct shared,makespan)==0x240,"makespan ABI");
_Static_assert(sizeof(struct shared)==SHARED_BYTES,"shared ABI");
_Static_assert(sizeof(struct task_result)==128,"task result ABI");
_Static_assert(__atomic_always_lock_free(8,0),"requires inline lock-free 64-bit atomics");
extern const struct qcase dual_stages[];
extern const struct task dual_tasks[];
extern const struct buffer dual_buffers[];
extern const uint32_t dual_output_offsets[];
extern const uint8_t dual_blob[],dual_blob_end[],dual_bundle_digest[];
extern uint8_t dual_arenas[];
extern struct task_result dual_results[];
void model_run(const struct qcase *c,void *output);
void rocket_main(uintptr_t hart) __attribute__((noreturn));
#endif
#endif
