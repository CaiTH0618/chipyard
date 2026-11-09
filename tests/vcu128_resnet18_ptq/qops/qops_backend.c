#include "qops.h"
#include "include/gemmini.h"
#include <string.h>
_Static_assert(DIM==8 && sizeof(elem_t)==1 && sizeof(acc_t)==4,"expected INT8 8x8 Gemmini");
#ifndef HAS_MVIN_SCALE
#error MVIN scaling required
#endif
#ifndef ACC_SCALE_T_IS_FLOAT
#error FP32 accumulator scaling required
#endif
static float from_bits(uint32_t bits) { union { uint32_t u; float f; } v={.u=bits}; return v.f; }
/* Candidate workaround for the observed I=1,J=9 loop-resadd failure.
 * Keep the numerical contract: MVIN_SCALE(A,s) + MVIN_SCALE(B,1), then
 * ACC_SCALE(sum,1) and optional ReLU. All arithmetic still runs on Gemmini.
 * Use one accumulator tile at a time; small rows and tail columns are exact.
 * The existing driver fence path is retained between the dependent stages.
 */
static void resadd_small_direct(const struct qcase *c, elem_t *output) {
    const elem_t *a = (const elem_t *)c->a;
    const elem_t *b = (const elem_t *)c->b;
    const uint32_t overwrite = UINT32_C(0x80000000);
    const uint32_t accumulate = UINT32_C(0xc0000000);
    const size_t stride = c->J * sizeof(elem_t);
    gemmini_extended_config_st(stride, c->relu ? RELU : NO_ACTIVATION, 1.0f);
    gemmini_config_ex(WS, 0, 0);
    gemmini_extended4_config_ld(stride, from_bits(c->a_scale_bits), true, DIM, 0);
    gemmini_extended4_config_ld(stride, 1.0f, true, DIM, 1);
    for (size_t j = 0; j < c->J; j += DIM) {
        const size_t cols = c->J - j < DIM ? c->J - j : DIM;
        gemmini_extended_mvin(a + j, overwrite, cols, c->I);
        gemmini_fence();
        gemmini_extended_mvin2(b + j, accumulate, cols, c->I);
        gemmini_fence();
        gemmini_extended_mvout(output + j, overwrite, cols, c->I);
        gemmini_fence();
    }
}

void qops_run(const struct qcase *c, void *output) {
    const float scale=from_bits(c->scale_bits);
    gemmini_fence();
    gemmini_flush(0);
    switch(c->op) {
    case OP_MATMUL:
        tiled_matmul_auto(c->I,c->J,c->K,(const elem_t*)c->a,(const elem_t*)c->b,
            c->d,output,c->K,c->J,c->J,c->J,
            MVIN_SCALE_IDENTITY,MVIN_SCALE_IDENTITY,1,
            c->relu ? RELU : NO_ACTIVATION,scale,0,
            true,false,false,c->full_C,false,0,WS);
        break;
    case OP_CONV:
        tiled_conv_auto(1,c->in_h,c->in_w,c->in_c,c->out_c,c->out_h,c->out_w,
            c->stride,1,1,c->padding,c->kernel,false,false,false,false,false,
            (const elem_t*)c->a,(const elem_t*)c->b,(const acc_t*)c->d,(elem_t*)output,
            c->relu ? RELU : NO_ACTIVATION,scale,c->pool_size,c->pool_stride,c->pool_padding,WS);
        break;
    case OP_RESADD:
        if (c->I > 0 && c->I < DIM) {
            resadd_small_direct(c, (elem_t *)output);
            break;
        }
        tiled_resadd_auto(c->I,c->J,from_bits(c->a_scale_bits),1,1,
            (const elem_t*)c->a,(const elem_t*)c->b,(elem_t*)output,c->relu,WS);
        break;
    case OP_AVERAGE:
        tiled_global_average_auto((const elem_t*)c->a,(elem_t*)output,1,c->in_c,c->in_h,WS);
        break;
    default:
        exit(90);
    }
    gemmini_fence();
    __asm__ volatile("fence iorw, iorw" ::: "memory");
}
