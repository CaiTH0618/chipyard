CROSS ?= riscv64-unknown-elf-
CC = $(CROSS)gcc
PYTHON ?= python3
CHIPYARD_ROOT ?= $(abspath ../..)
GEMMINI_SW ?= $(CHIPYARD_ROOT)/generators/gemmini/software/gemmini-rocc-tests
BUILD = build
ELF = $(BUILD)/activespm_test.elf
CFLAGS = -march=rv64imafdc_zicsr_zifencei -mabi=lp64d -mcmodel=medany -O2 -g -std=gnu99 -ffreestanding -fno-builtin -fno-stack-protector -fno-pic -fno-pie -fno-asynchronous-unwind-tables -msmall-data-limit=0 -mno-relax -ffunction-sections -fdata-sections -fno-lto -DBAREMETAL=1 -I. -I$(BUILD)/inputs $(EXTRA_CFLAGS)
LDFLAGS = -nostdlib -nostartfiles -static -no-pie -Wl,--build-id=none,--no-relax,--gc-sections,-T,linker.ld,-Map,$(BUILD)/activespm_test.map
.PHONY: all headers toolchain-check source-check check host-bundle
all: $(ELF)
toolchain-check:
	@printf '#include <stdio.h>\n#include <stdlib.h>\n' | $(CC) $(EXTRA_CFLAGS) -E -x c - >/dev/null || { echo "Target C headers missing: source Chipyard env.sh and use its working RISC-V toolchain"; exit 1; }
headers: toolchain-check
	$(PYTHON) freeze_headers.py --gemmini-sw "$(GEMMINI_SW)" --out "$(BUILD)/inputs"
source-check:
	$(PYTHON) -c 'import json,hashlib; from pathlib import Path; m=json.loads(Path("generated/test_manifest.json").read_text()); assert all(hashlib.sha256(Path(n).read_bytes()).hexdigest()==h for n,h in m["source_sha256"].items()), "Sources changed: rerun prepare_test.py"'
$(ELF): headers source-check start.S runtime.c reference.c model_backend.c test.h platform.h platform_runtime.c resadd_direct.h linker.ld generated/test_cases.c generated/test_generated.h generated/test_manifest.json
	$(CC) $(CFLAGS) $(LDFLAGS) start.S runtime.c reference.c platform_runtime.c model_backend.c generated/test_cases.c -lgcc -o $@
	$(PYTHON) host_activespm_test.py --elf $@ --dry-run
check:
	$(PYTHON) test_offline.py
host-bundle:
	$(PYTHON) package_host.py
