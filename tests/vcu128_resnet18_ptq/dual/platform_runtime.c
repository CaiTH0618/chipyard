#include "platform.h"
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
static struct line log_positions[2] __attribute__((aligned(64)));
static inline void barrier(void){__asm__ volatile("fence iorw, iorw" ::: "memory");}
static void log_char(char c) {
    volatile char *log = (volatile char *)(uintptr_t)(platform_hart() ? LOG1_BASE : LOG0_BASE);
    log[log_positions[platform_hart()].value % LOG_SIZE] = c;
    ++log_positions[platform_hart()].value;
}
static void publish_log(void) {
    barrier(); platform_shared()->status[platform_hart()].log_write = log_positions[platform_hart()].value; barrier();
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
    uint32_t before = log_positions[platform_hart()].value;
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
    return (int)(log_positions[platform_hart()].value - before);
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


void exit(int code) {
 struct shared *p=platform_shared();unsigned h=platform_hart();
 p->status[h].error=0x100+(unsigned)code;barrier();p->status[h].state=ERROR;barrier();platform_stop();
}
void abort(void){exit(99);}
