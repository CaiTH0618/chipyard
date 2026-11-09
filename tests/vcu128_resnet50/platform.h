#ifndef VCU128_RESNET50_PLATFORM_H
#define VCU128_RESNET50_PLATFORM_H
#include "result_layout.h"
void platform_stage(unsigned stage);
void platform_model_begin(void);
int platform_stop_after_first(void);
void platform_first_output(const void *data, size_t bytes);
void platform_final_output(const void *data, unsigned batch, unsigned classes);
void platform_operator_cycles(uint64_t cycles);
int resnet50_infer(void);
#endif
