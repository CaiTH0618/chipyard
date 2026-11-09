CROSS ?= riscv64-unknown-elf-
CC = $(CROSS)gcc
HART ?= 0
ifneq ($(filter $(HART),0 1),$(HART))
$(error HART must be 0 or 1)
endif
PYTHON ?= python3
CHIPYARD_ROOT ?= $(abspath ../../..)
GEMMINI_SW ?= $(CHIPYARD_ROOT)/generators/gemmini/software/gemmini-rocc-tests
BUILD = build/hart$(HART)
ELF = $(BUILD)/resnet18_full_hart$(HART).elf
CFLAGS = -march=rv64imafdc_zicsr_zifencei -mabi=lp64d -mcmodel=medany -O2 -g \
 -std=gnu99 -ffreestanding -fno-builtin -fno-stack-protector -fno-pic -fno-pie \
 -fno-asynchronous-unwind-tables -msmall-data-limit=0 -mno-relax -ffunction-sections \
 -fdata-sections -fno-lto -DBAREMETAL=1 -DACTIVE_HART=$(HART) -I. -I$(BUILD)/inputs $(EXTRA_CFLAGS)
LDFLAGS = -nostdlib -nostartfiles -static -no-pie -Wl,--build-id=none,--no-relax,--gc-sections,-T,linker.ld,-Map,$(BUILD)/full.map
.PHONY: all headers check inspect host-bundle native-check toolchain-check
all: $(ELF)
toolchain-check:
	@printf '#include <stdio.h>\n#include <stdlib.h>\n' | $(CC) $(EXTRA_CFLAGS) -E -x c - >/dev/null || { echo "Target C headers missing: source /home/cuixh/chipyard/env.sh and check command -v $(CC)"; exit 1; }
headers: toolchain-check
	$(PYTHON) freeze_headers.py --gemmini-sw "$(GEMMINI_SW)" --out "$(BUILD)/inputs"
$(ELF): headers start.S runtime.c full_backend.c full.h linker.ld Makefile generated/full_cases.c generated/full_blob.bin generated/full_manifest.json
	$(CC) $(CFLAGS) $(LDFLAGS) start.S runtime.c full_backend.c generated/full_cases.c generated/full_blob.S -lgcc -o $@
	$(PYTHON) host_resnet18_full.py --elf $@ --manifest generated/full_manifest.json --dry-run
check:
	$(PYTHON) test_offline.py --elf $(ELF) --manifest generated/full_manifest.json
inspect:
	$(CROSS)readelf -h -lW $(ELF)
	$(CROSS)objdump -d $(ELF) > $(BUILD)/full.dis
host-bundle:
	$(PYTHON) package_host.py
native-check: headers
	gcc -std=gnu99 -O2 -fno-fast-math -ffp-contract=off -I. -I$(BUILD)/inputs native_check.c generated/full_cases.c generated/full_blob.S -o $(BUILD)/native_verify
	$(BUILD)/native_verify
