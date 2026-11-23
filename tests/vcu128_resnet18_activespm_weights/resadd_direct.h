#ifndef VCU128_BASELINE_RESADD_DIRECT_H
#define VCU128_BASELINE_RESADD_DIRECT_H
/* Workaround candidate for repeated-run loop-resadd corruption.
 * Numerical contract: MVIN_SCALE(A,a_scale) + MVIN_SCALE(B,1),
 * then ACC_SCALE(sum,1), INT8 saturation and optional ReLU.
 * Gemmini performs all data arithmetic. The CPU only issues tile commands.
 * Each operand is transferred into the SAME single accumulator tile:
 * overwrite A -> completion fence -> accumulate B -> completion fence ->
 * move out -> completion fence, before that accumulator tile is reused.
 */
static inline void resadd_direct_fence(void) {
    __asm__ volatile("" ::: "memory");
    gemmini_fence();
    __asm__ volatile("" ::: "memory");
}
static void resadd_direct(const struct qcase *c, elem_t *output, float a_scale) {
    const elem_t *a = (const elem_t *)c->a;
    const elem_t *b = (const elem_t *)c->b;
    const uint32_t overwrite = UINT32_C(0x80000000);
    const uint32_t accumulate = UINT32_C(0xc0000000);
    const size_t stride = (size_t)c->J * sizeof(elem_t);
    gemmini_extended_config_st(stride, c->relu ? RELU : NO_ACTIVATION, 1.0f);
    gemmini_config_ex(WS, 0, 0);
    gemmini_extended4_config_ld(stride, a_scale, true, DIM, 0);
    gemmini_extended4_config_ld(stride, 1.0f, true, DIM, 1);
    for (size_t i = 0; i < c->I; i += DIM) {
        const size_t rows = c->I - i < DIM ? c->I - i : DIM;
        for (size_t j = 0; j < c->J; j += DIM) {
            const size_t cols = c->J - j < DIM ? c->J - j : DIM;
            const size_t offset = i * c->J + j;
            gemmini_extended_mvin(a + offset, overwrite, cols, rows);
            resadd_direct_fence();
            gemmini_extended_mvin2(b + offset, accumulate, cols, rows);
            resadd_direct_fence();
            gemmini_extended_mvout(output + offset, overwrite, cols, rows);
            resadd_direct_fence();
        }
    }
}
#endif
