CROSS ?= riscv64-unknown-elf-
CC = $(CROSS)gcc
PYTHON ?= python3
CHIPYARD_ROOT ?= $(abspath ../../..)
GEMMINI_SW ?= $(CHIPYARD_ROOT)/generators/gemmini/software/gemmini-rocc-tests
BUILD = build
ELF = $(BUILD)/resnet18_dual.elf
CFLAGS = -march=rv64imafdc_zicsr_zifencei -mabi=lp64d -mcmodel=medany -O2 -g \
 -std=gnu99 -ffreestanding -fno-builtin -fno-stack-protector -fno-pic -fno-pie \
 -fno-asynchronous-unwind-tables -msmall-data-limit=0 -mno-relax -ffunction-sections \
 -fdata-sections -fno-lto -DBAREMETAL=1 -I. -I$(BUILD)/inputs $(EXTRA_CFLAGS)
LDFLAGS = -nostdlib -nostartfiles -static -no-pie -Wl,--build-id=none,--no-relax,--gc-sections,-T,linker.ld,-Map,$(BUILD)/dual.map
.PHONY: all headers check inspect host-bundle native-check toolchain-check
all: $(ELF)
toolchain-check:
	@printf '#include <stdio.h>\n#include <stdlib.h>\n' | $(CC) $(EXTRA_CFLAGS) -E -x c - >/dev/null || { echo "Target C headers missing: source /home/cuixh/chipyard/env.sh and check command -v $(CC)"; exit 1; }
headers: toolchain-check
	$(PYTHON) freeze_headers.py --gemmini-sw "$(GEMMINI_SW)" --out "$(BUILD)/inputs"
$(ELF): headers start.S runtime.c platform_runtime.c model_backend.c dual.h linker.ld Makefile generated/dual_graph.c generated/dual_blob.bin generated/dual_manifest.json
	$(CC) $(CFLAGS) $(LDFLAGS) start.S runtime.c platform_runtime.c model_backend.c generated/dual_graph.c generated/dual_blob.S -lgcc -o $@
	$(PYTHON) host_resnet18_dual.py --elf $@ --manifest generated/dual_manifest.json --dry-run
check:
	$(PYTHON) test_offline.py --elf $(ELF) --manifest generated/dual_manifest.json
inspect:
	$(CROSS)readelf -h -lW $(ELF)
	$(CROSS)objdump -d $(ELF) > $(BUILD)/dual.dis
host-bundle:
	$(PYTHON) package_host.py
native-check: headers
	gcc -std=gnu99 -O2 -pthread -fno-fast-math -ffp-contract=off -DNATIVE_TEST -I. -I$(BUILD)/inputs native_runner.c native_model.c runtime.c generated/dual_graph.c generated/dual_blob.S -o $(BUILD)/native_runner
	$(BUILD)/native_runner serial 0
	$(BUILD)/native_runner dual 0
	$(BUILD)/native_runner dual 1
