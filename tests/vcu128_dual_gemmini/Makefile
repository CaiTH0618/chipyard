CROSS ?= riscv64-unknown-elf-
CC = $(CROSS)gcc
OBJDUMP = $(CROSS)objdump
READELF = $(CROSS)readelf
TEST_MODE ?= 1
ACTIVE_MASK ?= 3
CFLAGS = -march=rv64ima_zicsr_zifencei -mabi=lp64 -mcmodel=medany \
 -O2 -g -ffreestanding -fno-builtin -fno-stack-protector -fno-pic \
 -fno-pie -fno-asynchronous-unwind-tables -msmall-data-limit=0 -mno-relax \
 -DTEST_MODE=$(TEST_MODE) -DACTIVE_MASK=$(ACTIVE_MASK)
LDFLAGS = -nostdlib -nostartfiles -static -no-pie \
 -Wl,--build-id=none,--no-relax,-T,linker.ld,-Map,dual_gemmini.map
.PHONY: all clean inspect FORCE
all: dual_gemmini.elf
dual_gemmini.elf: start.S gemmini_test.c gemmini_min.h linker.ld Makefile FORCE
	$(CC) $(CFLAGS) $(LDFLAGS) start.S gemmini_test.c -o $@
inspect:
	$(READELF) -h -lW -A dual_gemmini.elf
	$(OBJDUMP) -d dual_gemmini.elf > dual_gemmini.dis
clean:
	rm -f dual_gemmini.elf dual_gemmini.map dual_gemmini.dis
