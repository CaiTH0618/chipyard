CROSS ?= riscv64-unknown-elf-
CC = $(CROSS)gcc
PYTHON ?= python3
CHIPYARD_ROOT ?= $(abspath ../..)
GEMMINI_SW ?= $(CHIPYARD_ROOT)/generators/gemmini/software/gemmini-rocc-tests
BUILD = build
ELF = $(BUILD)/resnet18_activespm_full.elf
CFLAGS = -march=rv64imafdc_zicsr_zifencei -mabi=lp64d -mcmodel=medany -O2 -g \
 -std=gnu99 -ffreestanding -fno-builtin -fno-stack-protector -fno-pic -fno-pie \
 -fno-asynchronous-unwind-tables -msmall-data-limit=0 -mno-relax -ffunction-sections \
 -fdata-sections -fno-lto -DBAREMETAL=1 -I. -I$(BUILD)/inputs $(EXTRA_CFLAGS)
LDFLAGS = -nostdlib -nostartfiles -static -no-pie -Wl,--build-id=none,--no-relax,--gc-sections,-T,linker.ld,-Map,$(BUILD)/baseline.map
.PHONY: all headers toolchain-check backend-check check check-resadd host-bundle native-numeric-check
all: $(ELF)
toolchain-check:
	@printf '#include <stdio.h>\n#include <stdlib.h>\n' | $(CC) $(EXTRA_CFLAGS) -E -x c - >/dev/null || { echo "Target C headers missing: source /home/cuixh/chipyard/env.sh and check command -v $(CC)"; exit 1; }
headers: toolchain-check
	$(PYTHON) freeze_headers.py --gemmini-sw "$(GEMMINI_SW)" --out "$(BUILD)/inputs"
backend-check:
	$(PYTHON) -c 'import hashlib,json; from pathlib import Path; m=json.loads(Path("generated/baseline_manifest.json").read_text()); assert all(m.get(k)==hashlib.sha256(Path(f).read_bytes()).hexdigest() for k,f in [("backend_sha256","model_backend.c"),("resadd_direct_sha256","resadd_direct.h"),("runtime_sha256","runtime.c")]), "Sources changed: rerun prepare_full_spm.py from the same full export before make"'
$(ELF): headers backend-check start.S runtime.c platform_runtime.c model_backend.c resadd_direct.h baseline.h platform.h linker.ld Makefile generated/baseline_graph.c generated/baseline_blob.bin generated/baseline_manifest.json generated/baseline_generated.h generated/baseline_blob.S
	$(CC) $(CFLAGS) $(LDFLAGS) start.S runtime.c platform_runtime.c model_backend.c generated/baseline_graph.c generated/baseline_blob.S -lgcc -o $@
	$(PYTHON) host_resnet18_activespm_full.py --elf $@ --dry-run
check:
	$(PYTHON) test_offline.py
check-resadd: headers
	gcc -std=gnu99 -O2 -Wall -Wextra -Werror -fno-fast-math -ffp-contract=off -I. -I$(BUILD)/inputs test_resadd_direct.c -lm -o $(BUILD)/test_resadd_direct
	$(BUILD)/test_resadd_direct
host-bundle:
	$(PYTHON) package_host.py
native-numeric-check: headers
	gcc -std=gnu99 -O2 -pthread -fno-fast-math -ffp-contract=off -DNATIVE_TEST -DNUMERIC_REFERENCE -I. -I$(BUILD)/inputs native_runner.c native_reference.c runtime.c generated/baseline_graph.c generated/baseline_blob.S -o $(BUILD)/native_numeric
	$(BUILD)/native_numeric serial debug 0 0 1
