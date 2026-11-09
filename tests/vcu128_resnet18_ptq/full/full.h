#ifndef VCU128_RESNET18_FULL_H
#define VCU128_RESNET18_FULL_H
#define APP_BASE 0x80000000
#define APP_END 0x8f000000
#define STACK_BOTTOM 0x8fe00000
#define STACK_TOP 0x8ff00000
#define RESULT_BASE 0x8ff00000
#define RESULT_REGION 4096
#define LOG_BASE 0x8ff10000
#define LOG_SIZE 65536
#define HBM_END 0x90000000
#define PREPARED 1
#define RUNNING 2
#define DONE 3
#define ERROR 4
#define WAIT_HOST 5
#define OP_MATMUL 1
#define OP_CONV 2
#define OP_RESADD 3
#define OP_AVERAGE 4
#ifndef __ASSEMBLER__
#include <stdint.h>
#include <stddef.h>
#define FULL_MAGIC UINT64_C(0x52313846554c4c31)
#define BUILD_MAGIC UINT64_C(0x52313846454c4631)
struct qcase {
 const uint8_t *a, *b, *d, *golden;
 uint32_t bytes, op, I, J, K;
 uint32_t in_h, in_w, in_c, out_c, out_h, out_w;
 uint32_t stride, padding, kernel, relu, pool_size, pool_stride, pool_padding, full_C;
 uint32_t scale_bits, a_scale_bits;
};
struct result_block {
 uint64_t magic, run_id, state, stage, hart, case_count, error, cases_done;
 uint64_t cycles, mismatches, first_bad, output_address, output_bytes, checksum;
 uint64_t mcause, mepc, mtval, ack, sequence, image_end, bss_start, bss_end, log_write;
 uint64_t reserved[9];
};
_Static_assert(sizeof(struct result_block)==256,"result size");
_Static_assert(offsetof(struct result_block,state)==16,"state ABI");
_Static_assert(offsetof(struct result_block,ack)==136,"ack ABI");
_Static_assert(offsetof(struct result_block,mcause)==112,"trap ABI");
extern const struct qcase full_cases[];
extern const uint8_t full_blob[], full_blob_end[], full_bundle_digest[];
extern char __image_end[], __bss_start[], __bss_end[];
extern uint8_t full_output[];
struct full_buffer { uint32_t offset, bytes; };
extern const uint32_t full_output_offsets[];
extern const struct full_buffer full_buffers[];
extern const uint32_t full_buffer_count;
void full_run(const struct qcase *c, void *output);
void rocket_main(void);
#endif
#endif
