#include <stdint.h>
#include <stddef.h>
#include "gemmini_min.h"

#ifndef TEST_MODE
#define TEST_MODE 1 /* 0: mvin/mvout, 1: 8x8 INT32 matmul */
#endif
#ifndef ACTIVE_MASK
#define ACTIVE_MASK 3 /* bit0=hart0, bit1=hart1 */
#endif
#if TEST_MODE != 0 && TEST_MODE != 1
#error TEST_MODE must be 0 or 1
#endif
#if ACTIVE_MASK < 1 || ACTIVE_MASK > 3
#error ACTIVE_MASK must be 1, 2, or 3
#endif

#define MAGIC UINT64_C(0x524F434B4554444E)
#define TRAP UINT64_C(0xBAD0000000000001)
#define MAIL_BASE UINT64_C(0x80030000)
#define SYNC_BASE UINT64_C(0x80030100)
#define A0 UINT64_C(0x80010000)
#define B0 UINT64_C(0x80011000)
#define C0 UINT64_C(0x80020000)
#define A1 UINT64_C(0x80050000)
#define B1 UINT64_C(0x80051000)
#define C1 UINT64_C(0x80060000)

/* Keep the 16-byte legacy metadata ABI, so host can validate the loaded ELF. */
const uint64_t build_config[2] __attribute__((used)) = {8, TEST_MODE};
const uint64_t dual_build_info[2] __attribute__((used)) = {UINT64_C(0x4455414C52563031),2};
const uint64_t gemmini_build_info[2] __attribute__((used)) = {UINT64_C(0x47454D4D494E4931), ACTIVE_MASK};
static inline void barrier(void) { __asm__ volatile("fence iorw, iorw" ::: "memory"); }
static inline uint64_t cycles(void) { uint64_t x; __asm__ volatile("csrr %0, mcycle":"=r"(x)); return x; }

void rocket_main(uintptr_t hart) {
    volatile uint64_t *mb = (volatile uint64_t *)(uintptr_t)(MAIL_BASE + 64*hart);
    volatile uint64_t *sync = (volatile uint64_t *)(uintptr_t)SYNC_BASE;
    const uintptr_t a = hart ? A1 : A0;
    const uintptr_t b = hart ? B1 : B0;
    const uintptr_t c = hart ? C1 : C0;
    const int active = !!(ACTIVE_MASK & (1u << hart));
    mb[5]=hart;
    mb[6]=TEST_MODE;
    mb[4]=1;
    barrier();
    sync[1+hart]=1;
    barrier();
    while(!sync[1+(hart^1)]) { }
    barrier();
    mb[4]=2;
    barrier();
    uint64_t begin=cycles();
    if (active) {
        /* Only minimal Gemmini instructions: no libc, no printf, no CPU matmul. */
        gem_config_ex();
        gem_config_ld(0);
        if (TEST_MODE == 1) gem_config_ld(1);
        gem_config_st(TEST_MODE == 1 ? 8u*sizeof(int32_t) : 8u);
        if (TEST_MODE == 0) {
            gem_mvin((const void *)a, 0, 0);
            gem_mvout((void *)c, 0);
        } else {
            gem_mvin((const void *)a, 0, 0);
            gem_mvin((const void *)b, 8, 1);
            /* Per supplied gemmini.h no-bias path: clear bit 30 for fresh accumulator overwrite. */
            gem_preload(8, GEM_ACC_FULL & ~(UINT32_C(1)<<30));
            gem_compute(0);
            gem_mvout((void *)c, GEM_ACC_FULL);
        }
        /* Local Gemmini API implements gemmini_fence() with CPU fence.
         * Validation depends on hardware RoCC/Gemmini fence completion behavior.
         * This should be checked by host guard/output and, if needed, ILA. */
        gem_fence();
    }
    barrier();
    mb[7]=cycles()-begin;
    mb[4]=3;
    barrier();
    mb[0]=MAGIC;
    barrier();
    for(;;) __asm__ volatile("wfi");
}
