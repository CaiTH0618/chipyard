#ifndef VCU128_RESNET50_RESULT_LAYOUT_H
#define VCU128_RESNET50_RESULT_LAYOUT_H

/* CPU/FBus addresses. The backend alone translates to HBM offsets. */
#define APP_BASE       0x80000000
#define APP_END        0x8f000000
#define STACK_BOTTOM  0x8fe00000
#define STACK_TOP     0x8ff00000
#define RESULT_BASE   0x8ff00000
#define RESULT_REGION 0x00004000
#define LOG_BASE      0x8ff10000
#define LOG_SIZE      0x00010000
#define HBM_END       0x90000000

#define ABI_VERSION 1
#define MODE_INIT 0
#define MODE_FIRST 1
#define MODE_FULL 2
#define STATE_PREPARED 1
#define STATE_RUNNING 2
#define STATE_DONE 3
#define STATE_ERROR 4
#define STAGE_BOOT 1
#define STAGE_BSS 2
#define STAGE_READY 3
#define STAGE_MODEL 100
#define STAGE_FIRST_DONE 201
#define STAGE_CLASSIFY 300
#define STAGE_COMPLETE 400
#define STAGE_TRAP 65535
#define FLAG_OUTPUT_VALID 1
#define FLAG_GOLDEN_OK 2
#define ERROR_EXIT 1
#define ERROR_TRAP 2
#define ERROR_PROTOCOL 3

#ifndef __ASSEMBLER__
#include <stddef.h>
#include <stdint.h>
#define RESULT_MAGIC UINT64_C(0x52534e4554353031)
#define BUILD_MAGIC UINT64_C(0x52533530454c4631)

struct result_block {
    uint64_t magic;                    /* 0 */
    uint32_t abi, size;                /* 8, 12 */
    uint64_t run_id;                   /* 16; prepared by Host */
    uint32_t state, stage;             /* 24, 28 */
    uint32_t mode, hart, error, flags;  /* 32..47 */
    uint64_t cycles_total, cycles_ops; /* 48, 56 */
    uint64_t mcause, mepc, mtval;      /* 64..87 */
    uint64_t image_end, bss_start, bss_end; /* 88..111 */
    uint32_t log_write, batch, classes, reserved0; /* 112..127 */
    uint32_t predictions[4];           /* 128 */
    int32_t scores[4];                 /* 144 */
    uint32_t expected[4];              /* 160 */
    uint64_t checksum, first_bytes;    /* 176, 184 */
    uint8_t reserved[64];              /* 192 */
    int8_t outputs[4 * 1000];          /* 256; [batch][class] */
};
_Static_assert(offsetof(struct result_block, state) == 24, "state ABI");
_Static_assert(offsetof(struct result_block, mcause) == 64, "trap ABI");
_Static_assert(offsetof(struct result_block, outputs) == 256, "output ABI");
_Static_assert(sizeof(struct result_block) == 4256, "result ABI");
_Static_assert(sizeof(struct result_block) <= RESULT_REGION, "result window");
#endif
#endif
