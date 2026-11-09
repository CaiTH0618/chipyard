CROSS ?= riscv64-unknown-elf-
CC = $(CROSS)gcc
OBJDUMP = $(CROSS)objdump
READELF = $(CROSS)readelf
HOSTCC ?= gcc
PYTHON ?= python3
ARCH ?= rv64imac_zicsr_zifencei
ABI ?= lp64
BUILD = build
ELF = $(BUILD)/dual_sync.elf
.DELETE_ON_ERROR:
CFLAGS = -march=$(ARCH) -mabi=$(ABI) -mcmodel=medany -O2 -g -std=gnu11 \
 -ffreestanding -fno-builtin -fno-stack-protector -fno-pic -fno-pie \
 -fno-asynchronous-unwind-tables -msmall-data-limit=0 -mno-relax \
 -ffunction-sections -fdata-sections -fno-lto -Wall -Wextra -Werror
LDFLAGS = -nostdlib -nostartfiles -static -no-pie \
 -Wl,--build-id=none,--no-relax,--gc-sections,-T,linker.ld,-Map,$(BUILD)/dual_sync.map
.PHONY: all inspect dry-run check clean
all: $(ELF)
$(BUILD):
	mkdir -p $(BUILD)
$(ELF): start.S dual_sync.c platform.h layout.h linker.ld Makefile | $(BUILD)
	$(CC) $(CFLAGS) $(LDFLAGS) start.S dual_sync.c -lgcc -o $@
	$(READELF) -h -lW $@
	$(OBJDUMP) -d $@ > $(BUILD)/dual_sync.dis
	$(PYTHON) verify_disassembly.py $(BUILD)/dual_sync.dis
	$(PYTHON) host_dual_sync_test.py --elf $@ --dry-run
inspect: $(ELF)
	$(READELF) -h -lW -A $(ELF)
dry-run: $(ELF)
	$(PYTHON) host_dual_sync_test.py --elf $(ELF) --dry-run
$(BUILD)/native_runner: dual_sync.c layout.h platform.h native_runner.c | $(BUILD)
	$(HOSTCC) -O2 -g -std=gnu11 -Wall -Wextra -Werror -DNATIVE_TEST -pthread dual_sync.c native_runner.c -o $@
check: $(BUILD)/native_runner
	$(PYTHON) test_offline.py
clean:
	rm -rf build
