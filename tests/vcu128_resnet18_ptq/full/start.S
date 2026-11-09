#include "full.h"
.section .text.init,"ax",@progbits
.balign 4
.globl _start
_start:
    csrw mie, zero
    csrci mstatus, 8
    csrr t0, mhartid
    li t1, ACTIVE_HART
    bne t0, t1, park
    la t0, trap_entry
    csrw mtvec, t0
.option push
.option norelax
    la gp, __global_pointer$
.option pop
    la sp, _stack_top
    li t0, 0x6000
    csrs mstatus, t0
    csrw fcsr, zero
    li t0, RESULT_BASE
    li t1, RUNNING
    fence iorw, iorw
    sd t1, 16(t0)
    la t0, __bss_start
    la t1, __bss_end
1:
    bgeu t0, t1, 2f
    sd zero, 0(t0)
    addi t0, t0, 8
    j 1b
2:
    fence iorw, iorw
    fence.i
    call rocket_main
park:
    wfi
    j park
.balign 4
trap_entry:
    li t0, RESULT_BASE
    csrr t1, mcause
    sd t1, 112(t0)
    csrr t1, mepc
    sd t1, 120(t0)
    csrr t1, mtval
    sd t1, 128(t0)
    li t1, 2
    sd t1, 48(t0)
    li t1, ERROR
    fence iorw, iorw
    sd t1, 16(t0)
    fence iorw, iorw
    j park
