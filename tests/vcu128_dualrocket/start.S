.section .text.init,"ax",@progbits
.balign 4
.globl _start
_start:
    csrw mie, zero
    csrci mstatus, 8
    la t0, trap_entry
    csrw mtvec, t0
    csrr a0, mhartid
    li t0, 2
    bgeu a0, t0, park
.option push
.option norelax
    la gp, __global_pointer$
.option pop
    la sp, _stack_top
    slli t0, a0, 14
    sub sp, sp, t0
    bnez a0, secondary_wait
    la t0, __bss_start
    la t1, __bss_end
clear_bss:
    bgeu t0, t1, publish_init
    sb zero, 0(t0)
    addi t0, t0, 1
    j clear_bss
publish_init:
    li t0, 0x80030100
    fence iorw, iorw
    li t1, 1
    sd t1, 0(t0)
    j enter_c
secondary_wait:
    li t0, 0x80030100
wait_init:
    ld t1, 0(t0)
    beqz t1, wait_init
    fence iorw, iorw
enter_c:
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
    li t0, 0x80030000
    slli t2, t2, 6
    add t0, t0, t2
    csrr t1, mcause
    sd t1, 8(t0)
    csrr t1, mepc
    sd t1, 16(t0)
    csrr t1, mtval
    sd t1, 24(t0)
    csrr t1, mhartid
    sd t1, 40(t0)
    fence iorw, iorw
    li t1, 0xBAD0000000000001
    sd t1, 0(t0)
    fence iorw, iorw
    j park
