#include "result_layout.h"
.section .text.init,"ax",@progbits
.balign 4
.globl _start
_start:
    csrw mie, zero
    csrci mstatus, 8
    csrr t0, mhartid
    bnez t0, park
    la t0, trap_entry
    csrw mtvec, t0
.option push
.option norelax
    la gp, __global_pointer$
.option pop
    la sp, _stack_top
    /* Supplied DTS advertises F and D. Integer weights still use FP scales. */
    li t0, 0x6000
    csrs mstatus, t0
    csrw fcsr, zero
    li t0, RESULT_BASE
    li t1, STAGE_BOOT
    sw t1, 28(t0)
    li t1, STATE_RUNNING
    fence iorw, iorw
    sw t1, 24(t0)
    li t1, STAGE_BSS
    sw t1, 28(t0)
    la t0, __bss_start
    la t1, __bss_end
clear_bss:
    bgeu t0, t1, enter_c
    sd zero, 0(t0)
    addi t0, t0, 8
    j clear_bss
enter_c:
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
    sd t1, 64(t0)
    csrr t1, mepc
    sd t1, 72(t0)
    csrr t1, mtval
    sd t1, 80(t0)
    li t1, ERROR_TRAP
    sw t1, 40(t0)
    li t1, STAGE_TRAP
    sw t1, 28(t0)
    li t1, STATE_ERROR
    fence iorw, iorw
    sw t1, 24(t0)
    fence iorw, iorw
    j park
