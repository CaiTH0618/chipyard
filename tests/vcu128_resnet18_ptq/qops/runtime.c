#include "qops.h"
#include "generated/qops_generated.h"
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
static volatile struct result_block *const result=(volatile struct result_block *)(uintptr_t)RESULT_BASE;
static uint32_t log_position;
uint8_t qops_output[QOPS_MAX_OUTPUT+128] __attribute__((aligned(64)));
const uint64_t qops_build_info[16] __attribute__((used,section(".rodata.build_info")))={
 BUILD_MAGIC,1,ACTIVE_HART,QOPS_COUNT,QOPS_MAX_OUTPUT,APP_BASE,APP_END,STACK_BOTTOM,STACK_TOP,
 RESULT_BASE,LOG_BASE,LOG_SIZE,sizeof(struct result_block),8,8,32};
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


static void park_forever(void) { for(;;) __asm__ volatile("wfi"); }
void exit(int code) {
 result->error=0x100+(unsigned)code;
 barrier(); result->state=ERROR; barrier(); park_forever(); __builtin_unreachable();
}
void abort(void) { exit(99); }
static uint64_t checksum(const void *p0,size_t n) {
 const uint8_t *p=p0; uint64_t h=UINT64_C(14695981039346656037);
 while(n--) { h^=*p++;h*=UINT64_C(1099511628211); } return h;
}
void rocket_main(void) {
 if(result->magic!=QOPS_MAGIC || !result->run_id || result->hart!=ACTIVE_HART || result->case_count!=QOPS_COUNT) exit(91);
 result->image_end=(uintptr_t)__image_end;result->bss_start=(uintptr_t)__bss_start;result->bss_end=(uintptr_t)__bss_end;
 uint64_t whole_start=cycles();
 for(unsigned i=0;i<QOPS_COUNT;i++) {
  const struct qcase *c=&qops_cases[i];
  result->stage=i+1;result->state=RUNNING;barrier();
  memset(qops_output,0xc3,64);memset(qops_output+64,0xa5,c->bytes);memset(qops_output+64+c->bytes,0x3c,64);
  uint64_t t=cycles();qops_run(c,qops_output+64);result->cycles=cycles()-t;
  uint64_t bad=0,first=UINT64_MAX;
  for(size_t j=0;j<c->bytes;j++) if(qops_output[64+j]!=c->golden[j]) { if(!bad) first=j;bad++; }
  for(unsigned j=0;j<64;j++) if(qops_output[j]!=0xc3 || qops_output[64+c->bytes+j]!=0x3c) exit(92);
  result->mismatches=bad;result->first_bad=first;result->output_address=(uintptr_t)(qops_output+64);
  result->output_bytes=c->bytes;result->checksum=checksum(qops_output+64,c->bytes);result->sequence=i+1;
  printf("Case %u: bytes=%u mismatched_bytes=%lu\n",i,c->bytes,(unsigned long)bad);
  barrier();result->state=WAIT_HOST;barrier();
  t=cycles();
  while(result->ack!=i+1) { barrier();if(cycles()-t>UINT64_C(90000000000)) exit(93); }
  if(bad) exit(94);
  result->cases_done=i+1;barrier();
 }
 result->cycles=cycles()-whole_start;barrier();result->state=DONE;barrier();park_forever();
}
