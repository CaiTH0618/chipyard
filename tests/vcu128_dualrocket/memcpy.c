#include <stdint.h>
#include <stddef.h>
#ifndef COPY_BYTES
#define COPY_BYTES 4096
#endif
#ifndef MODE
#define MODE 0
#endif
#if COPY_BYTES < 8 || COPY_BYTES > 32768
#error COPY_BYTES must be between 8 and 32768
#endif
#if MODE < 0 || MODE > 2
#error MODE must be 0(copy), 1(stub), or 2(alive)
#endif
const uint64_t build_config[2] __attribute__((used)) = {COPY_BYTES, MODE};
const uint64_t dual_build_info[2] __attribute__((used)) = {0x4455414C52563031ULL, 2};
static inline void barrier(void) { __asm__ volatile("fence iorw, iorw" ::: "memory"); }
static inline uint64_t cycles(void) {
    uint64_t x; __asm__ volatile("csrr %0, mcycle" : "=r"(x)); return x;
}
void rocket_main(uintptr_t hart) {
    volatile uint64_t *mb=(volatile uint64_t *)(uintptr_t)(0x80030000ULL+hart*64);
    volatile uint64_t *sync=(volatile uint64_t *)(uintptr_t)0x80030100ULL;
#if MODE == 0
    uintptr_t src=hart ? 0x80050000ULL : 0x80010000ULL;
#endif
#if MODE != 2
    uintptr_t dst=hart ? 0x80060000ULL : 0x80020000ULL;
#endif
    mb[5]=hart; mb[6]=COPY_BYTES; mb[4]=1;
    barrier();
    sync[1+hart]=1;
    barrier();
    while (!sync[1+(hart^1)]) {}
    barrier();
    mb[4]=2;
    barrier();
    uint64_t start=cycles();
#if MODE == 0
    volatile const uint8_t *s=(volatile const uint8_t *)src;
    volatile uint8_t *d=(volatile uint8_t *)dst;
    for(size_t i=0;i<COPY_BYTES;i++) d[i]=s[i];
#elif MODE == 1
    *(volatile uint64_t *)dst=UINT64_C(0x1122334455667788)+hart;
#endif
    barrier();
    mb[7]=cycles()-start;
    mb[4]=3;
    barrier();
    mb[0]=UINT64_C(0x524F434B4554444E);
    barrier();
    for(;;) __asm__ volatile("wfi");
}
