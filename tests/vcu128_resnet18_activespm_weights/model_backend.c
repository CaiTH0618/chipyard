#include "baseline.h"
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
#include "resadd_direct.h"

void model_run(const struct qcase *c, void *output) {
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
        resadd_direct(c, (elem_t *)output, from_bits(c->a_scale_bits));
        break;
    case OP_AVERAGE: {
        /* GAP7x7 = [1 ... 1] (1x49) * NHWC input (49x512).
         * Use the WS matmul reduction instead of repeated accumulator MVIN adds.
         * Preserve the frozen FP32 output scale and INT8 RNE/saturation contract.
         * Trailing entries are zero only to pad the immutable source allocation;
         * the matrix dimension remains K=49, not 64.
         */
        static const elem_t ones[64] __attribute__((aligned(64))) = {
            1,1,1,1,1,1,1, 1,1,1,1,1,1,1, 1,1,1,1,1,1,1,
            1,1,1,1,1,1,1, 1,1,1,1,1,1,1, 1,1,1,1,1,1,1,
            1,1,1,1,1,1,1
        };
        if (c->in_h != 7 || c->in_c != 512) exit(95);
        tiled_matmul_auto(1,512,49,ones,(const elem_t *)c->a,NULL,output,
            49,512,512,512,
            MVIN_SCALE_IDENTITY,MVIN_SCALE_IDENTITY,1,
            NO_ACTIVATION,scale,0,
            true,false,false,false,false,0,WS);
        break;
    }
    default:
        exit(90);
    }
    gemmini_fence();
    __asm__ volatile("fence iorw, iorw" ::: "memory");
}
