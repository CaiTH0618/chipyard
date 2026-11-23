#ifndef VCU128_ROCC_INSN_COMPAT_H
#define VCU128_ROCC_INSN_COMPAT_H
/* Only the two forms referenced by the supplied Gemmini headers.
 * Input-only form matches the board-tested gemmini_min.h .insn encoding.
 * Memory clobbers keep CPU data accesses ordered around command emission.
 */
#include <stdint.h>
#define VCU_CAT0(a,b) a##b
#define VCU_CAT(a,b) VCU_CAT0(a,b)
#define VCU_STR0(a) #a
#define VCU_STR(a) VCU_STR0(a)
#define VCU_OPCODE_0 0x0b
#define VCU_OPCODE_1 0x2b
#define VCU_OPCODE_2 0x5b
#define VCU_OPCODE_3 0x7b
#define VCU_OPCODE(x) VCU_CAT(VCU_OPCODE_,x)
#define ROCC_INSTRUCTION_0_R_R(x, rs1, rs2, funct) do { \
    uintptr_t _vcu_a=(uintptr_t)(rs1), _vcu_b=(uintptr_t)(rs2); \
    __asm__ volatile(".insn r " VCU_STR(VCU_OPCODE(x)) ", 3, " VCU_STR(funct) ", x0, %0, %1" \
                     :: "r"(_vcu_a), "r"(_vcu_b) : "memory"); \
} while(0);
#define ROCC_INSTRUCTION(x, rd, rs1, rs2, funct) do { \
    uintptr_t _vcu_a=(uintptr_t)(rs1), _vcu_b=(uintptr_t)(rs2), _vcu_r; \
    __asm__ volatile(".insn r " VCU_STR(VCU_OPCODE(x)) ", 7, " VCU_STR(funct) ", %0, %1, %2" \
                     : "=r"(_vcu_r) : "r"(_vcu_a), "r"(_vcu_b) : "memory"); \
    (rd)=_vcu_r; \
} while(0);
#endif
