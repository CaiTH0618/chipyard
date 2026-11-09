#include "platform.h"
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifndef TEST_MODE
#define TEST_MODE MODE_FULL
#endif
#if TEST_MODE < 0 || TEST_MODE > 2
#error invalid TEST_MODE
#endif

extern char __image_end[], __bss_start[], __bss_end[];
const uint64_t resnet50_build_info[20]
    __attribute__((used, section(".rodata.build_info"))) = {
    BUILD_MAGIC, ABI_VERSION, TEST_MODE, APP_BASE, APP_END,
    STACK_BOTTOM, STACK_TOP, RESULT_BASE, RESULT_REGION, LOG_BASE,
    LOG_SIZE, 8, 8, 32, 4, 1000, 256, 4000, sizeof(struct result_block), 1
};
static volatile struct result_block *const result =
    (volatile struct result_block *)(uintptr_t)RESULT_BASE;
static uint64_t begin_cycles;
static uint32_t log_position;
static inline void barrier(void) {
    __asm__ volatile("fence iorw, iorw" ::: "memory");
}
static uint64_t cycles(void) {
    uint64_t value;
    __asm__ volatile("csrr %0, mcycle" : "=r"(value));
    return value;
}
static void log_char(char c) {
    volatile char *log = (volatile char *)(uintptr_t)LOG_BASE;
    log[log_position % LOG_SIZE] = c;
    ++log_position;
}
static void publish_log(void) {
    barrier(); result->log_write = log_position; barrier();
}
static void log_string(const char *s) {
    if (!s) s = "(null)";
    while (*s) log_char(*s++);
}
static void log_number(uint64_t value, unsigned base, unsigned width, char pad) {
    char digits[32]; unsigned count = 0;
    do { digits[count++] = "0123456789abcdef"[value % base]; value /= base; } while (value);
    while (width > count) { log_char(pad); --width; }
    while (count) log_char(digits[--count]);
}
int printf(const char *format, ...) {
    uint32_t before = log_position;
    va_list ap; va_start(ap, format);
    while (*format) {
        if (*format != '%') { log_char(*format++); continue; }
        ++format;
        char pad = ' '; unsigned width = 0; int length = 0;
        if (*format == '0') { pad = '0'; ++format; }
        while (*format >= '0' && *format <= '9') width = width * 10 + (*format++ - '0');
        while (*format == 'l') { ++length; ++format; }
        if (*format == 'z') { length = 2; ++format; }
        char spec = *format;
        if (!spec) break;
        ++format;
        if (spec == '%') log_char('%');
        else if (spec == 's') log_string(va_arg(ap, const char *));
        else if (spec == 'c') log_char((char)va_arg(ap, int));
        else if (spec == 'p') { log_string("0x"); log_number((uintptr_t)va_arg(ap, void *), 16, width, pad); }
        else if (spec == 'd' || spec == 'i') {
            int64_t v = length >= 2 ? va_arg(ap, long long) :
                        length == 1 ? va_arg(ap, long) : va_arg(ap, int);
            uint64_t magnitude = (uint64_t)v;
            if (v < 0) { log_char('-'); magnitude = 0 - magnitude; if (width) --width; }
            log_number(magnitude, 10, width, pad);
        } else if (spec == 'u' || spec == 'x' || spec == 'X') {
            uint64_t v = length >= 2 ? va_arg(ap, unsigned long long) :
                         length == 1 ? va_arg(ap, unsigned long) : va_arg(ap, unsigned int);
            log_number(v, spec == 'u' ? 10 : 16, width, pad);
        } else { log_char('%'); log_char(spec); }
    }
    va_end(ap); publish_log();
    return (int)(log_position - before);
}
int puts(const char *s) { log_string(s); log_char('\n'); publish_log(); return 0; }
int putchar(int c) { log_char((char)c); publish_log(); return (unsigned char)c; }
void *memset(void *p, int c, size_t n) { unsigned char *d=p; while(n--) *d++=(unsigned char)c; return p; }
void *memcpy(void *d0, const void *s0, size_t n) { unsigned char *d=d0; const unsigned char *s=s0; while(n--) *d++=*s++; return d0; }
void *memmove(void *d0, const void *s0, size_t n) {
    unsigned char *d=d0; const unsigned char *s=s0;
    if ((uintptr_t)d < (uintptr_t)s) return memcpy(d0,s0,n);
    while(n) { --n; d[n]=s[n]; } return d0;
}
int memcmp(const void *a0,const void *b0,size_t n) { const unsigned char *a=a0,*b=b0; while(n--) { if(*a!=*b) return *a-*b; ++a; ++b; } return 0; }
size_t strlen(const char *s) { size_t n=0; while(s[n]) ++n; return n; }
int strcmp(const char *a,const char *b) { while(*a && *a==*b) {++a;++b;} return (unsigned char)*a-(unsigned char)*b; }
int strncmp(const char *a,const char *b,size_t n) { while(n--) { if(*a!=*b || !*a) return (unsigned char)*a-(unsigned char)*b; ++a;++b; } return 0; }
/* The supplied DTS supports D. No libm/newlib syscalls are required. */
double sqrt(double value) { double out; __asm__("fsqrt.d %0, %1" : "=f"(out) : "f"(value)); return out; }

void platform_stage(unsigned stage) { barrier(); result->stage=stage; barrier(); }
void platform_model_begin(void) { begin_cycles=cycles(); platform_stage(STAGE_MODEL); }
int platform_stop_after_first(void) { return TEST_MODE == MODE_FIRST; }
static uint64_t checksum(const void *data, size_t bytes) {
    const unsigned char *p=data;
    uint64_t hash=UINT64_C(14695981039346656037);
    while(bytes--) { hash ^= *p++; hash *= UINT64_C(1099511628211); }
    return hash;
}
void platform_first_output(const void *data, size_t bytes) {
    barrier(); result->checksum=checksum(data,bytes); result->first_bytes=bytes;
    platform_stage(STAGE_FIRST_DONE);
}
void platform_final_output(const void *data, unsigned batch, unsigned classes) {
    if (batch!=4 || classes!=1000) { result->error=ERROR_PROTOCOL; exit(1); }
    const int8_t *input=data;
    barrier();
    for (unsigned i=0;i<4000;++i) result->outputs[i]=input[i];
    result->checksum=checksum(data,4000);
    result->flags |= FLAG_OUTPUT_VALID;
    platform_stage(STAGE_CLASSIFY);
}
void platform_operator_cycles(uint64_t value) { result->cycles_ops=value; }

static void finish(int status) __attribute__((noreturn));
static void finish(int status) {
    if (TEST_MODE == MODE_FULL && status == 0) {
        if (!(result->flags & FLAG_OUTPUT_VALID)) { status=1; result->error=ERROR_PROTOCOL; }
        else {
            int good=1;
            for(unsigned b=0;b<4;++b) {
                unsigned best=0;
                for(unsigned c=1;c<1000;++c)
                    if(result->outputs[b*1000+c] > result->outputs[b*1000+best]) best=c;
                result->predictions[b]=best;
                result->scores[b]=result->outputs[b*1000+best];
                unsigned expected=result->expected[b];
                if(best!=expected && result->outputs[b*1000+best]!=result->outputs[b*1000+expected]) good=0;
            }
            if(good) result->flags |= FLAG_GOLDEN_OK; else status=1;
        }
    }
    if(begin_cycles) result->cycles_total=cycles()-begin_cycles;
    if(status && !result->error) result->error=ERROR_EXIT;
    if(!status) result->stage=STAGE_COMPLETE;
    publish_log(); barrier();
    result->state=status ? STATE_ERROR : STATE_DONE;
    barrier();
    for(;;) __asm__ volatile("wfi");
}
void exit(int status) { finish(status); }
void abort(void) { finish(1); }

void rocket_main(void) {
    uint64_t run_id=result->run_id;
    volatile unsigned char *p=(volatile unsigned char *)result;
    for(size_t i=0;i<sizeof(*result);++i) p[i]=0;
    result->magic=RESULT_MAGIC; result->abi=ABI_VERSION; result->size=sizeof(*result);
    result->run_id=run_id; result->mode=TEST_MODE; result->hart=0;
    result->batch=4; result->classes=1000;
    const unsigned expected[4]={75,900,641,897};
    for(unsigned i=0;i<4;++i) result->expected[i]=expected[i];
    result->image_end=(uintptr_t)__image_end;
    result->bss_start=(uintptr_t)__bss_start; result->bss_end=(uintptr_t)__bss_end;
    barrier(); result->state=STATE_RUNNING; platform_stage(STAGE_READY);
    printf("ResNet50 mode=%u DIM=8 hart0; image_end=0x%lx bss_end=0x%lx\n",
           (unsigned)TEST_MODE,(unsigned long)(uintptr_t)__image_end,(unsigned long)(uintptr_t)__bss_end);
    if(TEST_MODE==MODE_INIT) { puts("INIT_ONLY: full linked image initialized; model not executed"); finish(0); }
    int status=resnet50_infer();
    finish(status);
}
