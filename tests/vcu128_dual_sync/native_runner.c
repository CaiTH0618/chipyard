/* Execute the very same dual_sync.c with pthreads. This checks software logic,
 * not RISC-V memory ordering, boot RTL, XDMA, or FPGA cache coherence. */
#define _POSIX_C_SOURCE 200809L
#include "platform.h"
#include <pthread.h>
#include <sched.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

static struct shared area;
static uint32_t request[MAX_PAYLOAD/4] __attribute__((aligned(64)));
static uint32_t response[MAX_PAYLOAD/4] __attribute__((aligned(64)));
static int late_hart=-1;
struct shared *platform_shared(void) { return &area; }
uint32_t *platform_request(void) { return request; }
uint32_t *platform_response(void) { return response; }
uint64_t platform_cycles(void) {
    struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);
    return (uint64_t)t.tv_sec*UINT64_C(1000000000)+(uint64_t)t.tv_nsec;
}
void platform_relax(void) { sched_yield(); }
uint64_t platform_image_symbol(unsigned which) { return which==0 ? APP_BASE+0x1000 : APP_BASE+0x1200; }
void platform_stop(void) { pthread_exit(NULL); }
static void *run(void *argument) {
    uintptr_t hart=(uintptr_t)argument;
    if((int)hart==late_hart) { struct timespec t={0,5000000};nanosleep(&t,NULL); }
    rocket_main(hart);
}
int main(int argc,char **argv) {
    if(argc<3 || argc>4) { fprintf(stderr,"native_runner input.bin output.bin [missing-peer|late0|late1]\n");return 2; }
    FILE *in=fopen(argv[1],"rb");
    if(!in) { perror("input");return 2; }
    size_t got=fread(&area,1,sizeof(area),in);fclose(in);
    if(got!=sizeof(area)) { fprintf(stderr,"short input\n");return 2; }
    memset(request,0xa6,sizeof(request));memset(response,0x59,sizeof(response));
    int missing=argc==4 && !strcmp(argv[3],"missing-peer");
    if(argc==4 && !strcmp(argv[3],"late0")) late_hart=0;
    if(argc==4 && !strcmp(argv[3],"late1")) late_hart=1;
    pthread_t threads[2];
    for(uintptr_t h=0;h<(missing?1u:2u);++h)
        if(pthread_create(&threads[h],NULL,run,(void *)h)) return 3;
    for(unsigned h=0;h<(missing?1u:2u);++h) pthread_join(threads[h],NULL);
    FILE *out=fopen(argv[2],"wb");if(!out) { perror("output");return 2; }
    int ok=fwrite(&area,1,sizeof(area),out)==sizeof(area) &&
           fwrite(request,1,sizeof(request),out)==sizeof(request) &&
           fwrite(response,1,sizeof(response),out)==sizeof(response);
    if(fclose(out)) ok=0;
    return ok?0:2;
}
