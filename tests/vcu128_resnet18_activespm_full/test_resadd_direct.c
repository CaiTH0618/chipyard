/* Host command-level model of the actual resadd_direct.h command issuer.
 * Checks tile coverage, offsets, fences, signed scaling, tails and saturation.
 * It models Gemmini semantics; it cannot prove the hardware implementation.
 */
#include "baseline.h"
#include "include/gemmini_params.h"
#include <assert.h>
#include <stdbool.h>
#include <fenv.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define RELU 1
#define NO_ACTIVATION 0
#define WS 1
static size_t stride, nr, nc, tiles, fences;
static float scales[2];
static int phase, act, omit_b;
static int32_t acc[DIM][DIM];
static uint8_t *seen;
static const elem_t *abase, *bbase;
static elem_t *cbase;
static size_t elements, matrix_j;
static void gemmini_extended_config_st(size_t s,int activation,float scale) {
    assert(phase==0 && s>0 && scale==1.0f);stride=s;act=activation;
}
static void gemmini_config_ex(int dataflow,int activation,int shift) {
    assert(dataflow==WS && activation==0 && shift==0);
}
static void gemmini_extended4_config_ld(size_t s,float scale,bool shrunk,size_t block,int id) {
    assert(s==stride && shrunk && block==DIM && (id==0 || id==1));scales[id]=scale;
}
static void gemmini_extended_mvin(const elem_t *p,uint32_t address,size_t cols,size_t rows) {
    assert(phase==0 && address==UINT32_C(0x80000000));
    assert(rows>0 && rows<=DIM && cols>0 && cols<=DIM);nr=rows;nc=cols;
    size_t offset=(size_t)(p-abase);assert(offset<elements && offset%matrix_j+cols<=matrix_j);
    for(size_t i=0;i<rows;i++)for(size_t j=0;j<cols;j++) {
        assert(offset+i*stride+j<elements);acc[i][j]=MVIN_SCALE(p[i*stride+j],scales[0]);
    }
    phase=1;
}
static void gemmini_extended_mvin2(const elem_t *p,uint32_t address,size_t cols,size_t rows) {
    assert(phase==2 && address==UINT32_C(0xc0000000) && rows==nr && cols==nc);
    size_t offset=(size_t)(p-bbase);assert(offset<elements && offset%matrix_j+cols<=matrix_j);
    for(size_t i=0;i<rows;i++)for(size_t j=0;j<cols;j++) {
        assert(offset+i*stride+j<elements);
        if(!omit_b)acc[i][j]+=MVIN_SCALE(p[i*stride+j],scales[1]);
    }
    phase=3;
}
static void gemmini_extended_mvout(elem_t *p,uint32_t address,size_t cols,size_t rows) {
    assert(phase==4 && address==UINT32_C(0x80000000) && rows==nr && cols==nc);
    size_t offset=(size_t)(p-cbase);assert(offset<elements && offset%matrix_j+cols<=matrix_j);
    for(size_t i=0;i<rows;i++)for(size_t j=0;j<cols;j++) {
        size_t k=offset+i*stride+j;assert(k<elements && !seen[k]);seen[k]=1;
        int v=ACC_SCALE(acc[i][j],1.0f);p[i*stride+j]=(elem_t)(act==RELU && v<0?0:v);
    }
    tiles++;phase=5;
}
static void mock_fence(void) {
    assert(phase==1 || phase==3 || phase==5);phase=phase==5?0:phase+1;fences++;
}
#define gemmini_fence() mock_fence()
#include "resadd_direct.h"
static int8_t independent_scale(int32_t x,float scale) {
    volatile float product=(float)x*scale;
    float rounded=nearbyintf(product);
    return rounded>127?127:rounded<-128?-128:(int8_t)rounded;
}
static unsigned run(unsigned I,unsigned J,float scale,int relu,int corrupt) {
    size_t n=(size_t)I*J;
    elem_t *a=malloc(n),*b=malloc(n),*storage=malloc(n+128),*output=storage+64;
    assert(a && b && storage);memset(storage,0xa5,n+128);
    seen=calloc(n,1);assert(seen);
    for(size_t k=0;k<n;k++) {
        a[k]=(elem_t)((k*37+127)%256-128);b[k]=(elem_t)((k*83+255)%256-128);
    }
    /* Exercise the observed missing-B signature and signed saturation edges. */
    if(n>12615){a[12608]=-1;b[12608]=7;}
    abase=a;bbase=b;cbase=output;elements=n;matrix_j=J;phase=0;tiles=fences=0;omit_b=corrupt;
    struct qcase c={.I=I,.J=J,.a=(const uint8_t*)a,.b=(const uint8_t*)b,.relu=relu};
    resadd_direct(&c,output,scale);
    assert(phase==0 && tiles==((I+7)/8)*((J+7)/8) && fences==3*tiles);
    unsigned mismatches=0;
    for(size_t k=0;k<n;k++) {
        assert(seen[k]);int sum=independent_scale(a[k],scale)+independent_scale(b[k],1.0f);
        int expected=independent_scale(sum,1.0f);if(relu && expected<0)expected=0;
        mismatches+=output[k]!=expected;
        assert(a[k]==(elem_t)((k*37+127)%256-128) || (n>12615 && k==12608));
        assert(b[k]==(elem_t)((k*83+255)%256-128) || (n>12615 && k==12608));
    }
    for(size_t k=0;k<64;k++)assert((uint8_t)storage[k]==0xa5 && (uint8_t)storage[n+64+k]==0xa5);
    if(!corrupt)assert(!mismatches);
    free(a);free(b);free(storage);free(seen);return mismatches;
}
int main(void) {
    assert(fesetround(FE_TONEAREST)==0);
    const unsigned shapes[][2]={{1,9},{3,31},{8,8},{9,17},{49,512},{196,256},{784,128},{3136,64}};
    const float scales[]={0.5f,1.0f,1.5f,2.0f};unsigned cases=0;
    for(unsigned s=0;s<sizeof(shapes)/sizeof(shapes[0]);s++)
        for(unsigned k=0;k<sizeof(scales)/sizeof(scales[0]);k++)for(int relu=0;relu<=1;relu++) {
            run(shapes[s][0],shapes[s][1],scales[k],relu,0);cases++;
        }
    assert(run(784,128,1.0f,1,1)>0);
    printf("RESADD COMMAND MODEL PASS: %u shape/scale/ReLU cases; exact coverage, signed ties, saturation, tails, command/fence order; omitted-B detection\n",cases);
    return 0;
}
