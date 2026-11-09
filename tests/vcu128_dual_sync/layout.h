#ifndef VCU128_DUAL_SYNC_LAYOUT_H
#define VCU128_DUAL_SYNC_LAYOUT_H
#define APP_BASE       0x80000000
#define APP_LIMIT      0x80100000
#define STACK0_BOTTOM  0x80110000
#define STACK0_TOP     0x80120000
#define STACK1_BOTTOM  0x80130000
#define STACK1_TOP     0x80140000
#define SHARED_BASE    0x80200000
#define SHARED_BYTES   0x00001000
#define REQUEST_BASE   0x80300000
#define RESPONSE_BASE  0x80400000
#define MAX_PAYLOAD    0x00010000
#define STATUS_OFFSET  0x400
#define STATUS_BYTES   0x100
#define ABI_VERSION    1
#define PREPARED       1
#define RUNNING        2
#define DONE           3
#define ERROR          4
#define ERR_CONFIG     1
#define ERR_TIMEOUT    2
#define ERR_PEER       3
#define ERR_DATA       4
#define ERR_SEQUENCE   5
#define ERR_COUNTER    6
#define ERR_TRAP       7
#define ERR_BSS        8
#define TEST_PING      1
#define TEST_BLOCK     2
#define TEST_BARRIER   3
#define TEST_ATOMIC    4
#define TEST_FINISH    5

#ifndef __ASSEMBLER__
#include <stdint.h>
#include <stddef.h>
#define CONFIG_MAGIC UINT64_C(0x4453594e43434631)
#define BUILD_MAGIC  UINT64_C(0x4453594e43454c31)
#define DIGEST_INIT  UINT64_C(14695981039346656037)
#define DIGEST_MULT  UINT64_C(1099511628211)

/* Host writes once, before START. Rocket never modifies this configuration. */
struct config {
    uint64_t magic;
    uint32_t abi, bytes;
    uint64_t run_id, seed;
    uint32_t test_mask, ping_rounds, block_rounds, barrier_rounds;
    uint64_t atomic_iters, wait_cycles;
    uint32_t delay_cycles;
    uint8_t reserved[60];
};
struct line { uint64_t value; uint8_t pad[56]; };
struct contribution { uint64_t word[8]; };
struct status {
    uint64_t state, hart, test, round;
    uint64_t error, error_test, error_round, error_index;
    uint64_t expected, observed, mcause, mepc, mtval;
    uint64_t ping_done, block_done, barrier_done, atomic_done;
    uint64_t checked_bytes, ping_digest, block_digest, barrier_digest;
    uint64_t counter_seen, cycles_total, cycles_ping, cycles_block;
    uint64_t cycles_barrier, cycles_atomic;
    uint64_t bss_start, bss_end, image_end, run_id, reserved;
};
struct shared {
    struct config cfg;                 /* 0x000..0x07f */
    struct line boot;                  /* 0x080: written only by hart0 */
    struct line request_seq;           /* 0x0c0: written only by hart0 */
    struct line response_seq;          /* 0x100: written only by hart1 */
    struct line arrived[2];            /* 0x140, 0x180: one writer each */
    struct line counter;               /* 0x1c0: atomic RMW by both */
    struct line ping_request;          /* 0x200: owned by protocol */
    struct line ping_response;         /* 0x240 */
    struct contribution contribution[2]; /* 0x280, 0x2c0 */
    uint8_t gap[256];                  /* 0x300..0x3ff */
    struct status status[2];           /* 0x400, 0x500 */
    uint8_t tail[2560];                /* through 0xfff */
} __attribute__((aligned(64)));
_Static_assert(sizeof(struct config)==128, "config ABI");
_Static_assert(sizeof(struct line)==64, "cache line ABI");
_Static_assert(sizeof(struct status)==STATUS_BYTES, "status ABI");
_Static_assert(offsetof(struct status, error)==32, "error ABI");
_Static_assert(offsetof(struct status, mcause)==80, "trap ABI");
_Static_assert(offsetof(struct shared, status)==STATUS_OFFSET, "status offset ABI");
_Static_assert(offsetof(struct shared, request_seq)==0xc0, "request ABI");
_Static_assert(offsetof(struct shared, contribution)==0x280, "payload ABI");
_Static_assert(sizeof(struct shared)==SHARED_BYTES, "shared ABI");
_Static_assert(__atomic_always_lock_free(8,0), "requires inline 64-bit atomic operations");

static inline uint64_t digest_add(uint64_t h,uint64_t v) { return (h^v)*DIGEST_MULT; }
static inline uint64_t pattern(uint64_t seed,unsigned test,uint64_t round,uint64_t index) {
    uint64_t x=seed ^ ((uint64_t)test<<56) ^ (round*UINT64_C(0x9e3779b97f4a7c15)) ^
        (index*UINT64_C(0xd1b54a32d192ed03));
    x=(x^(x>>30))*UINT64_C(0xbf58476d1ce4e5b9);
    x=(x^(x>>27))*UINT64_C(0x94d049bb133111eb);
    return x^(x>>31);
}
static inline uint64_t ping_reply(uint64_t x,uint64_t round) {
    x^=UINT64_C(0xa5a55a5af00dcafe);
    return ((x<<17)|(x>>47))+round;
}
static inline uint32_t block_reply(uint32_t x,uint32_t round,uint32_t index) {
    x^=UINT32_C(0xa5a55a5a);
    return ((x<<7)|(x>>25))+round+index;
}
static inline unsigned block_bytes(uint64_t round) {
    unsigned i=(unsigned)((round-1)%3);
    return i==0 ? 64 : i==1 ? 4096 : MAX_PAYLOAD;
}
#endif
#endif
