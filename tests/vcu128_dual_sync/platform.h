#ifndef VCU128_DUAL_SYNC_PLATFORM_H
#define VCU128_DUAL_SYNC_PLATFORM_H
#include "layout.h"
#ifdef NATIVE_TEST
struct shared *platform_shared(void);
uint32_t *platform_request(void);
uint32_t *platform_response(void);
uint64_t platform_cycles(void);
void platform_relax(void);
uint64_t platform_image_symbol(unsigned which);
void platform_stop(void) __attribute__((noreturn));
#else
static inline struct shared *platform_shared(void) { return (void *)(uintptr_t)SHARED_BASE; }
static inline uint32_t *platform_request(void) { return (void *)(uintptr_t)REQUEST_BASE; }
static inline uint32_t *platform_response(void) { return (void *)(uintptr_t)RESPONSE_BASE; }
static inline uint64_t platform_cycles(void) {
    uint64_t n; __asm__ volatile("csrr %0, mcycle":"=r"(n)); return n;
}
static inline void platform_relax(void) { __asm__ volatile("nop"); }
static inline uint64_t platform_image_symbol(unsigned which) {
    extern char __bss_start[], __bss_end[], __image_end[];
    return (uintptr_t)(which==0 ? __bss_start : which==1 ? __bss_end : __image_end);
}
static inline void platform_stop(void) __attribute__((noreturn));
static inline void platform_stop(void) { for(;;) __asm__ volatile("wfi"); }
#endif
void rocket_main(uintptr_t hart) __attribute__((noreturn));
#endif
