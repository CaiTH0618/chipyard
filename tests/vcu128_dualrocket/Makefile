CROSS ?= riscv64-unknown-elf-
CC = $(CROSS)gcc
OBJDUMP = $(CROSS)objdump
READELF = $(CROSS)readelf
SIZE ?= 4096
MODE ?= 0
CFLAGS = -march=rv64ima_zicsr_zifencei -mabi=lp64 -mcmodel=medany \
 -O2 -g -ffreestanding -fno-builtin -fno-stack-protector -fno-pic \
 -fno-pie -fno-asynchronous-unwind-tables -msmall-data-limit=0 -mno-relax \
 -DCOPY_BYTES=$(SIZE) -DMODE=$(MODE)
LDFLAGS = -nostdlib -nostartfiles -static -no-pie \
 -Wl,--build-id=none,--no-relax,-T,linker.ld,-Map,dual_memcpy.map
.PHONY: all clean inspect FORCE
all: dual_memcpy.elf
# Recompile on every explicit build so changes to SIZE/MODE cannot reuse a stale ELF.
dual_memcpy.elf: start.S memcpy.c linker.ld Makefile FORCE
	$(CC) $(CFLAGS) $(LDFLAGS) start.S memcpy.c -o $@
inspect:
	$(READELF) -h -lW -A dual_memcpy.elf
	$(OBJDUMP) -d dual_memcpy.elf > dual_memcpy.dis
clean:
	rm -f dual_memcpy.elf dual_memcpy.map dual_memcpy.dis
