#ifndef GEMMINI_MIN_H
#define GEMMINI_MIN_H
#include <stdint.h>
/* ABI checked against supplied local gemmini.h, XCUSTOM_ACC=3 (opcode 0x7b).
 * funct3=3: xs1=1, xs2=1, xd=0; instruction has no destination register.
 * Address interpretation must be checked against local Gemmini controller. */
#define GEM_INSN(F, A, B) do { \
  uintptr_t _a=(uintptr_t)(A), _b=(uintptr_t)(B); \
  __asm__ volatile (".insn r 0x7b, 3, " #F ", x0, %0, %1" \
                    :: "r"(_a), "r"(_b) : "memory"); \
} while(0)
#define GEM_PACK(ADDR,COLS,ROWS) ((((uint64_t)(ROWS))<<48) | (((uint64_t)(COLS))<<32) | (uint32_t)(ADDR))
#define GEM_CONFIG 0
#define GEM_MVIN2 1
#define GEM_MVIN 2
#define GEM_MVOUT 3
#define GEM_COMPUTE_PRELOADED 4
#define GEM_PRELOAD 6
#define GEM_FLUSH 7
#define GEM_CONFIG_EX 0
#define GEM_CONFIG_LD 1
#define GEM_CONFIG_ST 2
#define GEM_WS 1
#define GEM_ACC_FULL (UINT32_C(0xE0000000))
#define GEM_GARBAGE (UINT32_C(0xffffffff))
/* Original gemmini_extended3_config_ex with dataflow=WS, act=0, shift=0,
 * acc_scale=1.0f, C_stride=1, A_stride=1, no transpose */
static inline void gem_config_ex(void) {
    GEM_INSN(0, ((uint64_t)UINT32_C(0x3f800000)<<32) | (UINT64_C(1)<<16) | (UINT64_C(1)<<2), (UINT64_C(1)<<48));
}
/* IDs 0 and 1 are separate mvin engines. Stride is 8 bytes for INT8 8x8. */
static inline void gem_config_ld(uint32_t id) {
    GEM_INSN(0, ((uint64_t)UINT32_C(0x3f800000)<<32) | (UINT64_C(8)<<16) | ((uint64_t)id<<3) | GEM_CONFIG_LD, 8);
}
static inline void gem_config_st(uint32_t stride) {
    GEM_INSN(0, GEM_CONFIG_ST, ((uint64_t)UINT32_C(0x3f800000)<<32) | stride);
}
static inline void gem_mvin(const void *dram, uint32_t spad, int id) {
    if (id == 0) { GEM_INSN(2, dram, GEM_PACK(spad,8,8)); }
    else { GEM_INSN(1, dram, GEM_PACK(spad,8,8)); }
}
static inline void gem_mvout(void *dram, uint32_t spad) {
    GEM_INSN(3, dram, GEM_PACK(spad,8,8));
}
static inline void gem_preload(uint32_t B, uint32_t C) {
    GEM_INSN(6, GEM_PACK(B,8,8), GEM_PACK(C,8,8));
}
static inline void gem_compute(uint32_t A) {
    GEM_INSN(4, GEM_PACK(A,8,8), GEM_PACK(GEM_GARBAGE,8,8));
}
static inline void gem_fence(void) {
    __asm__ volatile ("fence iorw, iorw" ::: "memory");
}
#endif
