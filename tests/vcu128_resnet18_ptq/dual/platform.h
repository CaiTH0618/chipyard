#ifndef VCU128_RESNET18_DUAL_PLATFORM_H
#define VCU128_RESNET18_DUAL_PLATFORM_H
#include "dual.h"
#ifdef NATIVE_TEST
struct shared *platform_shared(void);
uint64_t platform_cycles(void);
uintptr_t platform_hart(void);
void platform_relax(void);
uint64_t platform_image_symbol(unsigned which);
void platform_stop(void) __attribute__((noreturn));
#else
static inline struct shared *platform_shared(void){return (void *)(uintptr_t)SHARED_BASE;}
static inline uint64_t platform_cycles(void){uint64_t v;__asm__ volatile("csrr %0, mcycle":"=r"(v)::"memory");return v;}
static inline uintptr_t platform_hart(void){uintptr_t v;__asm__ volatile("csrr %0, mhartid":"=r"(v));return v;}
static inline void platform_relax(void){__asm__ volatile("nop");}
static inline uint64_t platform_image_symbol(unsigned w){extern char __bss_start[],__bss_end[],__image_end[];return (uintptr_t)(w==0?__bss_start:w==1?__bss_end:__image_end);}
static inline void platform_stop(void) __attribute__((noreturn));
static inline void platform_stop(void){for(;;)__asm__ volatile("wfi");}
#endif
#endif
