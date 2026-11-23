/* Scalar checker, independent of Gemmini's command tiling. Not the compute path. */
#include "test.h"
#include "include/gemmini_params.h"
static float f32(uint32_t u){union{uint32_t u;float f;}v={.u=u};return v.f;}
uint32_t test_seed(unsigned h,unsigned iteration,unsigned index){return 0x100+h*0x31+iteration*0x65+index*0x13;}
uint64_t test_expected_word(size_t k,uint32_t s){return (k*UINT64_C(0x9e3779b97f4a7c15))^((uint64_t)s<<32)^UINT64_C(0xa35c96f0874bd219);}
uint8_t test_pattern(size_t k,uint32_t s,int small){int v=small?(int)((k*5+s*3+(k>>2))%7)-3:(int)((k*37+s*13+(k>>3)*17)&255)-128;return (uint8_t)(int8_t)v;}
static int val(size_t k,uint32_t s,int small){return (int8_t)test_pattern(k,s,small);}
static int bias(size_t k,uint32_t s){return (int)((k*3+s)%17)-8;}
static int quant(int v,float scale,int relu){int r=ACC_SCALE(v,scale);return relu&&r<0?0:r;}
size_t test_output_bytes(const struct test_case *t){
 if(t->kind==KIND_CPU64)return SPM_BYTES;
 if(t->kind==KIND_WIDTH)return t->cols;
 if(t->kind==KIND_COPY)return (size_t)t->rows*t->stride;
 if(t->kind==KIND_MATMUL || t->kind==KIND_RESADD)return (size_t)t->I*t->J*(t->full_C?4:1);
 if(t->kind==KIND_GAP)return t->in_c;
 unsigned h=t->out_h,w=t->out_w;
 if(t->pool_stride){h=(h+2*t->pool_padding-t->pool_size)/t->pool_stride+1;w=(w+2*t->pool_padding-t->pool_size)/t->pool_stride+1;}
 return (size_t)h*w*t->out_c;
}
static int conv(const struct test_case *t,unsigned y,unsigned x,unsigned oc,uint32_t s){
 int z=bias(oc,s+11);
 for(unsigned ky=0;ky<t->kernel;ky++)for(unsigned kx=0;kx<t->kernel;kx++){
  int iy=(int)(y*t->conv_stride+ky)-(int)t->padding,ix=(int)(x*t->conv_stride+kx)-(int)t->padding;
  if(iy<0 || ix<0 || iy>=(int)t->in_h || ix>=(int)t->in_w)continue;
  for(unsigned ch=0;ch<t->in_c;ch++)z+=val(((size_t)iy*t->in_w+ix)*t->in_c+ch,s,1)*val((((size_t)ky*t->kernel+kx)*t->in_c+ch)*t->out_c+oc,s+7,1);
 }
 return quant(z,f32(t->scale_bits),t->relu);
}
uint8_t test_expected_byte(const struct test_case *t,size_t k,uint32_t s){
 if(t->kind==KIND_CPU64)return (uint8_t)(test_expected_word(k/8,s)>>(8*(k%8)));
 if(t->kind==KIND_WIDTH)return test_pattern(k,s,0);
 if(t->kind==KIND_COPY)return k%t->stride<t->cols?test_pattern(k,s,0):0xa5;
 if(t->kind==KIND_RESADD){int a=MVIN_SCALE(val(k,s,0),f32(t->a_scale_bits));return (uint8_t)(int8_t)quant(a+val(k,s+7,0),1,t->relu);}
 if(t->kind==KIND_MATMUL){
  size_t element=t->full_C?k/4:k;unsigned i=element/t->J,j=element%t->J;int z=bias(j,s+11);
  for(unsigned p=0;p<t->K;p++)z+=val((size_t)i*t->K+p,s,1)*val((size_t)p*t->J+j,s+7,1);
  if(t->full_C)return (uint8_t)((uint32_t)z>>(8*(k%4)));
  return (uint8_t)(int8_t)quant(z,f32(t->scale_bits),t->relu);
 }
 if(t->kind==KIND_GAP){int z=0;for(unsigned p=0;p<t->in_h*t->in_w;p++)z+=val((size_t)p*t->in_c+k,s,0);return (uint8_t)(int8_t)quant(z,f32(t->scale_bits),0);}
 unsigned oh=t->out_h,ow=t->out_w;
 if(t->pool_stride){oh=(oh+2*t->pool_padding-t->pool_size)/t->pool_stride+1;ow=(ow+2*t->pool_padding-t->pool_size)/t->pool_stride+1;}
 (void)oh;unsigned ch=k%t->out_c,x=(k/t->out_c)%ow,y=k/(t->out_c*ow);
 if(!t->pool_stride)return (uint8_t)(int8_t)conv(t,y,x,ch,s);
 int z=-128;
 for(unsigned ky=0;ky<t->pool_size;ky++)for(unsigned kx=0;kx<t->pool_size;kx++){
  int iy=(int)(y*t->pool_stride+ky)-(int)t->pool_padding,ix=(int)(x*t->pool_stride+kx)-(int)t->pool_padding;
  if(iy>=0 && ix>=0 && iy<(int)t->out_h && ix<(int)t->out_w){int v=conv(t,iy,ix,ch,s);if(v>z)z=v;}
 }
 return (uint8_t)(int8_t)z;
}
