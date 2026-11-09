/* Optional x86 check of fixture layout/math against the frozen C scaling macros.
 * This does not execute Gemmini and cannot replace board testing. */
#include "full.h"
#include "generated/full_generated.h"
#include "include/gemmini_params.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <limits.h>
static float fp(uint32_t u) { union {uint32_t u;float f;} v={.u=u};return v.f; }
static int8_t scaled(int64_t a,float s,int relu) {
 if(a<INT32_MIN || a>INT32_MAX) {fprintf(stderr,"acc overflow\n");exit(2);}
 int32_t v=ACC_SCALE((int32_t)a,s);if(relu && v<0)v=0;return (int8_t)v;
}
uint8_t full_output[FULL_ARENA_BYTES] __attribute__((aligned(64)));
int main(void) {
 uint8_t *tmp=malloc(FULL_ARENA_BYTES);
 if(!tmp)return 2;
 for(unsigned j=0;j<full_buffer_count;j++) {
  const struct full_buffer *p=&full_buffers[j];
  memset(full_output+p->offset-64,0xc3,64);memset(full_output+p->offset+p->bytes,0x3c,64);
 }
 for(unsigned n=0;n<FULL_COUNT;n++) {
  const struct qcase *c=&full_cases[n];const int8_t *a=(const int8_t*)c->a,*b=(const int8_t*)c->b;
  const int32_t *d=(const int32_t*)c->d;float s=fp(c->scale_bits);
  uint8_t *out=full_output+full_output_offsets[n];
  memset(out,0xa5,c->bytes);
  if(c->op==OP_MATMUL) {
   for(unsigned i=0;i<c->I;i++)for(unsigned j=0;j<c->J;j++) {
    int64_t v=d[j];for(unsigned k=0;k<c->K;k++)v+=(int64_t)a[i*c->K+k]*b[k*c->J+j];
    if(v<INT32_MIN || v>INT32_MAX)return 3;
    if(c->full_C)((int32_t*)out)[i*c->J+j]=(int32_t)v;else ((int8_t*)out)[i*c->J+j]=scaled(v,s,c->relu);
   }
  } else if(c->op==OP_CONV) {
   int8_t *dst=c->pool_stride?(int8_t*)tmp:(int8_t*)out;
   for(unsigned y=0;y<c->out_h;y++)for(unsigned x=0;x<c->out_w;x++)for(unsigned o=0;o<c->out_c;o++) {
    int64_t v=d[o];
    for(unsigned ky=0;ky<c->kernel;ky++)for(unsigned kx=0;kx<c->kernel;kx++) {
     int iy=(int)(y*c->stride+ky)-(int)c->padding,ix=(int)(x*c->stride+kx)-(int)c->padding;
     if(iy<0 || ix<0 || iy>=(int)c->in_h || ix>=(int)c->in_w)continue;
     for(unsigned ch=0;ch<c->in_c;ch++)v+=(int64_t)a[(iy*c->in_w+ix)*c->in_c+ch]*b[((ky*c->kernel+kx)*c->in_c+ch)*c->out_c+o];
    }
    dst[(y*c->out_w+x)*c->out_c+o]=scaled(v,s,c->relu);
   }
   if(c->pool_stride) {
    unsigned oh=(c->out_h+2*c->pool_padding-c->pool_size)/c->pool_stride+1;
    unsigned ow=(c->out_w+2*c->pool_padding-c->pool_size)/c->pool_stride+1;
    for(unsigned y=0;y<oh;y++)for(unsigned x=0;x<ow;x++)for(unsigned ch=0;ch<c->out_c;ch++) {
     int v=-128;
     for(unsigned ky=0;ky<c->pool_size;ky++)for(unsigned kx=0;kx<c->pool_size;kx++) {
      int iy=(int)(y*c->pool_stride+ky)-(int)c->pool_padding,ix=(int)(x*c->pool_stride+kx)-(int)c->pool_padding;
      if(iy>=0 && ix>=0 && iy<(int)c->out_h && ix<(int)c->out_w) {
       int val=dst[(iy*c->out_w+ix)*c->out_c+ch];if(val>v)v=val;
      }
     }
     ((int8_t*)out)[(y*ow+x)*c->out_c+ch]=(int8_t)v;
    }
   }
  } else if(c->op==OP_RESADD) {
   for(unsigned i=0;i<c->I*c->J;i++)((int8_t*)out)[i]=scaled((int32_t)MVIN_SCALE(a[i],fp(c->a_scale_bits))+(int32_t)MVIN_SCALE(b[i],1),1,c->relu);
  } else if(c->op==OP_AVERAGE) {
   for(unsigned ch=0;ch<c->in_c;ch++) {
    int64_t v=0;for(unsigned i=0;i<c->in_h*c->in_h;i++)v+=a[i*c->in_c+ch];
    ((int8_t*)out)[ch]=scaled(v,(float)(1.0/(c->in_h*c->in_h)),0);
   }
  } else return 4;
  if(memcmp(out,c->golden,c->bytes)) {fprintf(stderr,"native case %u differs\n",n);return 5;}
  for(unsigned j=0;j<full_buffer_count;j++)for(unsigned k=0;k<64;k++) {
   const struct full_buffer *p=&full_buffers[j];
   if(full_output[p->offset-64+k]!=0xc3 || full_output[p->offset+p->bytes+k]!=0x3c)return 6;
  }
  printf("NATIVE chained stage %u/%u PASS\n",n+1,FULL_COUNT);fflush(stdout);
 }
 printf("NATIVE CHAINED GRAPH PASS: %u checkpoints, true predecessor outputs, exact scalar INT64/C scaling; no Gemmini execution\n",FULL_COUNT);
 free(tmp);return 0;
}
