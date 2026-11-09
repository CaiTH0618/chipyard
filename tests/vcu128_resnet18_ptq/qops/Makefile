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
ELF = $(BUILD)/resnet18_qops_hart$(HART).elf
CFLAGS = -march=rv64imafdc_zicsr_zifencei -mabi=lp64d -mcmodel=medany -O2 -g \
 -std=gnu99 -ffreestanding -fno-builtin -fno-stack-protector -fno-pic -fno-pie \
 -fno-asynchronous-unwind-tables -msmall-data-limit=0 -mno-relax -ffunction-sections \
 -fdata-sections -fno-lto -DBAREMETAL=1 -DACTIVE_HART=$(HART) -I. -I$(BUILD)/inputs $(EXTRA_CFLAGS)
LDFLAGS = -nostdlib -nostartfiles -static -no-pie -Wl,--build-id=none,--no-relax,--gc-sections,-T,linker.ld,-Map,$(BUILD)/qops.map
.PHONY: all headers check inspect host-bundle native-check
all: $(ELF)
headers:
	$(PYTHON) freeze_headers.py --gemmini-sw "$(GEMMINI_SW)" --out "$(BUILD)/inputs"
$(ELF): headers start.S runtime.c qops_backend.c qops.h linker.ld Makefile generated/qops_cases.c generated/qops_blob.bin generated/qops_manifest.json
	$(CC) $(CFLAGS) $(LDFLAGS) start.S runtime.c qops_backend.c generated/qops_cases.c generated/qops_blob.S -lgcc -o $@
	$(PYTHON) host_resnet18_qops.py --elf $@ --manifest generated/qops_manifest.json --dry-run
check:
	$(PYTHON) test_offline.py --elf $(ELF) --manifest generated/qops_manifest.json
inspect:
	$(CROSS)readelf -h -lW $(ELF)
	$(CROSS)objdump -d $(ELF) > $(BUILD)/qops.dis
host-bundle:
	$(PYTHON) package_host.py
native-check: headers
	gcc -std=gnu99 -O2 -fno-fast-math -ffp-contract=off -I. -I$(BUILD)/inputs native_verify.c generated/qops_cases.c generated/qops_blob.S -o $(BUILD)/native_verify
	$(BUILD)/native_verify
