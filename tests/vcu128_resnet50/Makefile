CROSS ?= riscv64-unknown-elf-
CC = $(CROSS)gcc
OBJDUMP = $(CROSS)objdump
READELF = $(CROSS)readelf
PYTHON ?= python3
CHIPYARD_ROOT ?= $(abspath ../..)
GEMMINI_SW ?= $(CHIPYARD_ROOT)/generators/gemmini/software/gemmini-rocc-tests
MODE ?= full
MODE_NUMBER_init = 0
MODE_NUMBER_first = 1
MODE_NUMBER_full = 2
MODE_NUMBER = $(MODE_NUMBER_$(MODE))
ifeq ($(MODE_NUMBER),)
$(error MODE must be init, first or full)
endif
BUILD = build/$(MODE)
ELF = $(BUILD)/resnet50_$(MODE).elf
ARCH ?= rv64imafdc_zicsr_zifencei
ABI ?= lp64d
CFLAGS = -march=$(ARCH) -mabi=$(ABI) -mcmodel=medany -O2 -g \
 -std=gnu99 -ffreestanding -fno-builtin -fno-stack-protector -fno-pic \
 -fno-pie -fno-asynchronous-unwind-tables -msmall-data-limit=0 -mno-relax \
 -ffunction-sections -fdata-sections -fno-lto \
 -DBAREMETAL=1 -DTEST_MODE=$(MODE_NUMBER) \
 -I. -I$(BUILD)/inputs -Wno-incompatible-pointer-types
LDFLAGS = -nostdlib -nostartfiles -static -no-pie \
 -Wl,--build-id=none,--no-relax,--gc-sections,-T,linker.ld,-Map,$(BUILD)/resnet50_$(MODE).map
.PHONY: all prepare inspect dry-run clean
all: $(ELF)
prepare:
	$(PYTHON) prepare_model.py --gemmini-sw "$(GEMMINI_SW)" --out "$(BUILD)/inputs"
$(ELF): prepare start.S platform_runtime.c platform.h result_layout.h linker.ld Makefile
	$(CC) $(CFLAGS) $(LDFLAGS) start.S platform_runtime.c $(BUILD)/inputs/resnet50_model.c -lgcc -o $@
	$(READELF) -h -lW $@
	$(PYTHON) host_resnet50_test.py --elf $@ --dry-run
inspect:
	$(READELF) -h -lW -A $(ELF)
	$(OBJDUMP) -d $(ELF) > $(BUILD)/resnet50_$(MODE).dis
dry-run:
	$(PYTHON) host_resnet50_test.py --elf $(ELF) --dry-run
clean:
	rm -rf build
