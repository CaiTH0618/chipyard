#include "dual.h"
.section .text.init,"ax",@progbits
.balign 4
.globl _start
_start:
    csrw mie, zero
    csrci mstatus, 8
    csrr a0, mhartid
    li t0, 2
    bgeu a0, t0, park
    la t0, trap_entry
    csrw mtvec, t0
.option push
.option norelax
    la gp, __global_pointer$
.option pop
    la sp, _stack0_top
    beqz a0, primary
    la sp, _stack1_top
    /* Secondary enters only stack-based bootstrap code until boot release. */
    j enter_c
primary:
    la t0, __bss_start
    la t1, __bss_end
clear_bss:
    bgeu t0, t1, enter_c
    sd zero, 0(t0)
    addi t0, t0, 8
    j clear_bss
enter_c:
    li t0, 0x6000
    csrs mstatus, t0
    csrw fcsr, zero
    fence iorw, iorw
    fence.i
    csrr a0, mhartid
    call rocket_main
park:
    wfi
    j park
.balign 4
trap_entry:
    csrr t2, mhartid
    li t0, 2
    bgeu t2, t0, park
    li t0, SHARED_BASE + STATUS_OFFSET
    slli t2, t2, 8
    add t0, t0, t2
    csrr t1, mcause
    sd t1, 80(t0)
    csrr t1, mepc
    sd t1, 88(t0)
    csrr t1, mtval
    sd t1, 96(t0)
    li t1, ERR_TRAP
    sd t1, 32(t0)
    li t1, ERROR
    fence iorw, iorw
    sd t1, 0(t0)
    fence iorw, iorw
    j park
