#!/usr/bin/env bash

# Configure bounded parallelism and JVM memory for Chipyard builds.
# This file must be sourced after env.sh so that the settings remain in the
# caller's shell and are inherited by sbt and recursive make processes.

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    echo "ERROR: source scripts/chipyard-build-resources.sh; do not execute it." >&2
    exit 1
fi

_chipyard_resource_error() {
    echo "ERROR: chipyard build resource policy: $*" >&2
}

_chipyard_configure_resources() {
    local setting value
    for setting in CHIPYARD_CPU_PERCENT CHIPYARD_COMPILE_JOB_MIB \
        CHIPYARD_SYSTEM_RESERVE_MIB CHIPYARD_JVM_MAX_MIB CHIPYARD_JVM_MIN_MIB; do
        value="${!setting-}"
        if [[ -n "$value" && ! "$value" =~ ^[1-9][0-9]*$ ]]; then
            _chipyard_resource_error "$setting must be a positive integer, got '$value'"
            return 1
        fi
    done

    local available_cpus memory_kib
    if ! available_cpus=$(nproc 2>/dev/null) || [[ ! "$available_cpus" =~ ^[1-9][0-9]*$ ]]; then
        _chipyard_resource_error "could not determine the available CPU count with nproc"
        return 1
    fi
    if ! memory_kib=$(awk '$1 == "MemTotal:" { print $2; exit }' /proc/meminfo) ||
        [[ ! "$memory_kib" =~ ^[1-9][0-9]*$ ]]; then
        _chipyard_resource_error "could not read MemTotal from /proc/meminfo"
        return 1
    fi
    local memory_mib=$((memory_kib / 1024))

    # Any ancestor's cgroup v2 hard limit can constrain the process.
    local cgroup_path cgroup_dir memory_limit_bytes memory_limit_mib
    cgroup_path=$(awk 'sub(/^0::/, "") { print; exit }' /proc/self/cgroup)
    if [[ "$cgroup_path" == /* ]]; then
        cgroup_dir="/sys/fs/cgroup${cgroup_path%/}"
        while :; do
            if [[ -r "$cgroup_dir/memory.max" ]]; then
                memory_limit_bytes=$(<"$cgroup_dir/memory.max")
                if [[ "$memory_limit_bytes" =~ ^[0-9]+$ ]]; then
                    memory_limit_mib=$((memory_limit_bytes / 1048576))
                    if (( memory_limit_mib < memory_mib )); then
                        memory_mib=$memory_limit_mib
                    fi
                fi
            fi
            [[ "$cgroup_dir" == /sys/fs/cgroup ]] && break
            cgroup_dir="${cgroup_dir%/*}"
        done
    fi

    local cpu_percent="${CHIPYARD_CPU_PERCENT:-97}"
    local compile_job_mib="${CHIPYARD_COMPILE_JOB_MIB:-768}"
    local cpu_jobs=$((available_cpus * cpu_percent / 100))
    if (( cpu_jobs < 1 )); then
        cpu_jobs=1
    elif (( cpu_jobs > available_cpus )); then
        cpu_jobs=$available_cpus
    fi

    # Reserve 10% of memory for the system by default.
    local build_memory_mib=$((memory_mib * 90 / 100))
    if [[ -n "${CHIPYARD_SYSTEM_RESERVE_MIB:-}" ]]; then
        build_memory_mib=$((memory_mib - CHIPYARD_SYSTEM_RESERVE_MIB))
    fi
    if (( build_memory_mib < 1 )); then
        _chipyard_resource_error "no build memory remains after reserving system memory"
        return 1
    fi
    local memory_jobs=$((build_memory_mib / compile_job_mib))
    if (( memory_jobs < 1 )); then
        memory_jobs=1
    fi
    local build_jobs=$cpu_jobs
    if (( memory_jobs < build_jobs )); then
        build_jobs=$memory_jobs
    fi

    # Most native compilation follows elaboration, so both use the build budget.
    # Leave 10% of that budget for JVM memory outside the Java heap.
    local jvm_xmx_mib=$((build_memory_mib * 90 / 100))
    local heap_min_mib="${CHIPYARD_JVM_MIN_MIB:-1}"
    local heap_cap_mib="${CHIPYARD_JVM_MAX_MIB:-$jvm_xmx_mib}"
    if (( jvm_xmx_mib > heap_cap_mib )); then
        jvm_xmx_mib=$heap_cap_mib
    fi
    if (( jvm_xmx_mib < heap_min_mib )); then
        _chipyard_resource_error "JVM Xmx would be ${jvm_xmx_mib} MiB (minimum ${heap_min_mib} MiB)"
        return 1
    fi
    local jvm_xms_mib=$((jvm_xmx_mib / 4))
    if (( jvm_xms_mib > 2048 )); then
        jvm_xms_mib=2048
    fi

    local repo_root="${CY_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
    local java_tmp="$repo_root/.java_tmp"
    if ! mkdir -p "$java_tmp"; then
        _chipyard_resource_error "cannot create Java temporary directory $java_tmp"
        return 1
    fi

    export CHIPYARD_CPU_PERCENT="$cpu_percent" CHIPYARD_COMPILE_JOB_MIB="$compile_job_mib"
    export CHIPYARD_SYSTEM_RESERVE_MIB CHIPYARD_JVM_MAX_MIB CHIPYARD_JVM_MIN_MIB
    export CHIPYARD_AVAILABLE_CPUS="$available_cpus" CHIPYARD_CPU_TARGET_JOBS="$cpu_jobs"
    export CHIPYARD_MEMORY_LIMIT_JOBS="$memory_jobs" CHIPYARD_BUILD_JOBS="$build_jobs"
    export CHIPYARD_EFFECTIVE_AVAILABLE_MIB="$memory_mib" CHIPYARD_BUILD_MEMORY_MIB="$build_memory_mib"
    export CHIPYARD_JVM_XMX_MIB="$jvm_xmx_mib" CHIPYARD_JVM_XMS_MIB="$jvm_xms_mib"

    if [[ "${MAKEFLAGS:-}" == *--jobserver-auth=* || "${MAKEFLAGS:-}" == *--jobserver-fds=* ]]; then
        echo "NOTICE: preserving the GNU make jobserver inherited from the parent make." >&2
    else
        export MAKEFLAGS="-j${build_jobs}"
    fi

    # Both the sbt launcher and direct Java calls use JAVA_TOOL_OPTIONS.
    # Replace only the flags managed here, including after repeated sourcing.
    local option
    local -a old_options options
    for setting in SBT_OPTS JAVA_TOOL_OPTIONS; do
        read -r -a old_options <<< "${!setting-}"
        options=()
        for option in "${old_options[@]}"; do
            case "${option#-J}" in
                -Xms*|-Xmx*|-Xss*|-XX:[+-]Use*GC|-XX:ActiveProcessorCount=*|\
                -Dsbt.task.cpus=*|-Djava.io.tmpdir=*) ;;
                *) options+=("$option") ;;
            esac
        done
        if [[ "$setting" == JAVA_TOOL_OPTIONS ]]; then
            options+=("-Xms${jvm_xms_mib}m" "-Xmx${jvm_xmx_mib}m" "-Xss8M" "-XX:+UseG1GC"
                "-XX:ActiveProcessorCount=$build_jobs" "-Dsbt.task.cpus=$build_jobs"
                "-Djava.io.tmpdir=$java_tmp")
        fi
        if (( ${#options[@]} )); then
            export "$setting=${options[*]}"
        else
            unset "$setting"
        fi
    done
}

if ! _chipyard_configure_resources; then
    unset -f _chipyard_configure_resources _chipyard_resource_error
    return 1
fi
unset -f _chipyard_configure_resources _chipyard_resource_error

chipyard_resource_report() {
    printf '%s\n' \
        "Chipyard build resource policy:" \
        "  available CPUs:       ${CHIPYARD_AVAILABLE_CPUS}" \
        "  CPU target jobs:      ${CHIPYARD_CPU_TARGET_JOBS}" \
        "  memory-limit jobs:    ${CHIPYARD_MEMORY_LIMIT_JOBS}" \
        "  build jobs:           ${CHIPYARD_BUILD_JOBS}" \
        "  memory capacity:      ${CHIPYARD_EFFECTIVE_AVAILABLE_MIB} MiB" \
        "  build memory budget:  ${CHIPYARD_BUILD_MEMORY_MIB} MiB" \
        "  JVM Xms:              ${CHIPYARD_JVM_XMS_MIB} MiB" \
        "  JVM Xmx:              ${CHIPYARD_JVM_XMX_MIB} MiB" \
        "  MAKEFLAGS:            ${MAKEFLAGS:-<inherited jobserver>}" \
        "  SBT_OPTS:             ${SBT_OPTS:-<unset>}" \
        "  JAVA_TOOL_OPTIONS:    ${JAVA_TOOL_OPTIONS}"
}
