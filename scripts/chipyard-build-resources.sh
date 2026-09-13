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
    return 1
}

_chipyard_require_positive_integer() {
    local name="$1"
    local value="$2"
    if [[ ! "$value" =~ ^[1-9][0-9]*$ ]]; then
        _chipyard_resource_error "$name must be a positive integer, got '$value'"
        return 1
    fi
}

: "${CHIPYARD_CPU_PERCENT:=80}"
: "${CHIPYARD_COMPILE_JOB_MIB:=2048}"
: "${CHIPYARD_JVM_MAX_MIB:=32768}"
: "${CHIPYARD_JVM_MIN_MIB:=4096}"
: "${CHIPYARD_SYSTEM_RESERVE_MIB:=16384}"

for _chipyard_setting in \
    CHIPYARD_CPU_PERCENT \
    CHIPYARD_COMPILE_JOB_MIB \
    CHIPYARD_JVM_MAX_MIB \
    CHIPYARD_JVM_MIN_MIB \
    CHIPYARD_SYSTEM_RESERVE_MIB; do
    _chipyard_require_positive_integer \
        "$_chipyard_setting" "${!_chipyard_setting}" || return 1
done

if ! command -v nproc >/dev/null 2>&1; then
    _chipyard_resource_error "nproc is required to determine the available CPU limit"
    return 1
fi

CHIPYARD_AVAILABLE_CPUS="$(nproc 2>/dev/null)"
_chipyard_require_positive_integer \
    CHIPYARD_AVAILABLE_CPUS "$CHIPYARD_AVAILABLE_CPUS" || return 1

# Round the percentage target up, then clamp it to the CPU affinity limit.
CHIPYARD_CPU_TARGET_JOBS=$((
    (CHIPYARD_AVAILABLE_CPUS * CHIPYARD_CPU_PERCENT + 99) / 100
))
if (( CHIPYARD_CPU_TARGET_JOBS < 1 )); then
    CHIPYARD_CPU_TARGET_JOBS=1
elif (( CHIPYARD_CPU_TARGET_JOBS > CHIPYARD_AVAILABLE_CPUS )); then
    CHIPYARD_CPU_TARGET_JOBS="$CHIPYARD_AVAILABLE_CPUS"
fi

_chipyard_host_available_kib="$(
    awk '$1 == "MemAvailable:" { print $2; exit }' /proc/meminfo 2>/dev/null
)"
_chipyard_require_positive_integer \
    MemAvailableKiB "$_chipyard_host_available_kib" || return 1
_chipyard_host_available_mib=$((_chipyard_host_available_kib / 1024))
if (( _chipyard_host_available_mib < 1 )); then
    _chipyard_resource_error "MemAvailable is less than 1 MiB"
    return 1
fi

# Find the nearest finite cgroup-v2 memory limit. A delegated leaf may be
# unlimited while one of its ancestors supplies the effective bound.
_chipyard_cgroup_available_mib=0
_chipyard_cgroup_limit_found=0
_chipyard_cgroup_path="$(
    awk -F: '$1 == "0" && $2 == "" { print $3; exit }' /proc/self/cgroup 2>/dev/null
)"
if [[ -n "$_chipyard_cgroup_path" && -d /sys/fs/cgroup ]]; then
    _chipyard_cgroup_dir="/sys/fs/cgroup${_chipyard_cgroup_path}"
    while [[ "$_chipyard_cgroup_dir" == /sys/fs/cgroup* ]]; do
        if [[ -r "$_chipyard_cgroup_dir/memory.max" && \
              -r "$_chipyard_cgroup_dir/memory.current" ]]; then
            _chipyard_memory_max="$(<"$_chipyard_cgroup_dir/memory.max")"
            _chipyard_memory_current="$(<"$_chipyard_cgroup_dir/memory.current")"
            if [[ "$_chipyard_memory_max" =~ ^[0-9]+$ && \
                  "$_chipyard_memory_current" =~ ^[0-9]+$ ]]; then
                _chipyard_cgroup_limit_found=1
                if (( _chipyard_memory_max > _chipyard_memory_current )); then
                    _chipyard_cgroup_available_mib=$((
                        (_chipyard_memory_max - _chipyard_memory_current) / 1048576
                    ))
                else
                    _chipyard_cgroup_available_mib=0
                fi
                break
            fi
        fi
        [[ "$_chipyard_cgroup_dir" == "/sys/fs/cgroup" ]] && break
        _chipyard_cgroup_dir="${_chipyard_cgroup_dir%/*}"
        [[ -n "$_chipyard_cgroup_dir" ]] || break
    done
fi

CHIPYARD_EFFECTIVE_AVAILABLE_MIB="$_chipyard_host_available_mib"
if (( _chipyard_cgroup_limit_found &&
      _chipyard_cgroup_available_mib < CHIPYARD_EFFECTIVE_AVAILABLE_MIB )); then
    CHIPYARD_EFFECTIVE_AVAILABLE_MIB="$_chipyard_cgroup_available_mib"
fi

CHIPYARD_JVM_XMX_MIB=$((CHIPYARD_EFFECTIVE_AVAILABLE_MIB / 4))
if (( CHIPYARD_JVM_XMX_MIB > CHIPYARD_JVM_MAX_MIB )); then
    CHIPYARD_JVM_XMX_MIB="$CHIPYARD_JVM_MAX_MIB"
fi
if (( CHIPYARD_JVM_XMX_MIB < CHIPYARD_JVM_MIN_MIB )); then
    _chipyard_resource_error \
        "only ${CHIPYARD_EFFECTIVE_AVAILABLE_MIB} MiB is effectively available; JVM Xmx would be ${CHIPYARD_JVM_XMX_MIB} MiB, below the required ${CHIPYARD_JVM_MIN_MIB} MiB"
    return 1
fi
if (( CHIPYARD_JVM_XMX_MIB < 8192 )); then
    echo "WARNING: Chipyard JVM Xmx is below 8192 MiB; large elaborations may fail." >&2
fi

CHIPYARD_JVM_XMS_MIB=$((CHIPYARD_JVM_XMX_MIB / 4))
if (( CHIPYARD_JVM_XMS_MIB < 2048 )); then
    CHIPYARD_JVM_XMS_MIB=2048
elif (( CHIPYARD_JVM_XMS_MIB > 8192 )); then
    CHIPYARD_JVM_XMS_MIB=8192
fi

_chipyard_memory_reserve_mib=$((
    CHIPYARD_JVM_XMX_MIB + CHIPYARD_SYSTEM_RESERVE_MIB
))
if (( _chipyard_memory_reserve_mib < 32768 )); then
    _chipyard_memory_reserve_mib=32768
fi

if (( CHIPYARD_EFFECTIVE_AVAILABLE_MIB > _chipyard_memory_reserve_mib )); then
    CHIPYARD_MEMORY_LIMIT_JOBS=$((
        (CHIPYARD_EFFECTIVE_AVAILABLE_MIB - _chipyard_memory_reserve_mib) /
        CHIPYARD_COMPILE_JOB_MIB
    ))
else
    CHIPYARD_MEMORY_LIMIT_JOBS=1
fi
if (( CHIPYARD_MEMORY_LIMIT_JOBS < 1 )); then
    CHIPYARD_MEMORY_LIMIT_JOBS=1
fi

CHIPYARD_BUILD_JOBS="$CHIPYARD_CPU_TARGET_JOBS"
if (( CHIPYARD_MEMORY_LIMIT_JOBS < CHIPYARD_BUILD_JOBS )); then
    CHIPYARD_BUILD_JOBS="$CHIPYARD_MEMORY_LIMIT_JOBS"
fi
if (( CHIPYARD_BUILD_JOBS > CHIPYARD_AVAILABLE_CPUS )); then
    CHIPYARD_BUILD_JOBS="$CHIPYARD_AVAILABLE_CPUS"
fi

if [[ " ${MAKEFLAGS:-} " == *"--jobserver-auth="* ||
      " ${MAKEFLAGS:-} " == *"--jobserver-fds="* ]]; then
    echo "NOTICE: preserving the GNU make jobserver inherited from the parent make." >&2
else
    export MAKEFLAGS="-j${CHIPYARD_BUILD_JOBS} -l${CHIPYARD_BUILD_JOBS}"
fi

# Remove stale JVM resource options from SBT_OPTS. Leaving those options there
# would either conflict with this policy in the shell wrapper or be parsed as
# sbt commands by Chipyard's direct `java -jar` make path.
_chipyard_sbt_options=()
if [[ -n "${SBT_OPTS:-}" ]]; then
    read -r -a _chipyard_existing_sbt_options <<< "$SBT_OPTS"
    for _chipyard_option in "${_chipyard_existing_sbt_options[@]}"; do
        case "$_chipyard_option" in
            -J-Xms*|-Xms*|-J-Xmx*|-Xmx*|-J-Xss*|-Xss*|\
            -J-XX:+Use*GC|-XX:+Use*GC|-J-XX:-Use*GC|-XX:-Use*GC|\
            -J-XX:ActiveProcessorCount=*|-XX:ActiveProcessorCount=*|\
            -J-Dsbt.task.cpus=*|-Dsbt.task.cpus=*|-Djava.io.tmpdir=*)
                ;;
            *)
                _chipyard_sbt_options+=("$_chipyard_option")
                ;;
        esac
    done
fi
if (( ${#_chipyard_sbt_options[@]} )); then
    SBT_OPTS="${_chipyard_sbt_options[*]}"
    export SBT_OPTS
else
    unset SBT_OPTS
fi

# Chipyard invokes Java both through the sbt shell wrapper and directly from
# make. JAVA_TOOL_OPTIONS is the one JVM interface honored by both paths.
# Remove only options owned by this policy so repeated sourcing is idempotent.
_chipyard_java_options=()
if [[ -n "${JAVA_TOOL_OPTIONS:-}" ]]; then
    read -r -a _chipyard_existing_java_options <<< "$JAVA_TOOL_OPTIONS"
    for _chipyard_option in "${_chipyard_existing_java_options[@]}"; do
        case "$_chipyard_option" in
            -J-Xms*|-Xms*|-J-Xmx*|-Xmx*|-J-Xss*|-Xss*|\
            -J-XX:+Use*GC|-XX:+Use*GC|-J-XX:-Use*GC|-XX:-Use*GC|\
            -J-XX:ActiveProcessorCount=*|-XX:ActiveProcessorCount=*|\
            -J-Dsbt.task.cpus=*|-Dsbt.task.cpus=*|-Djava.io.tmpdir=*)
                ;;
            *)
                _chipyard_java_options+=("$_chipyard_option")
                ;;
        esac
    done
fi
_chipyard_resource_root="${CY_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
_chipyard_java_tmp="${_chipyard_resource_root}/.java_tmp"
mkdir -p "$_chipyard_java_tmp" || {
    _chipyard_resource_error "cannot create Java temporary directory $_chipyard_java_tmp"
    return 1
}
_chipyard_java_options+=(
    "-Xms${CHIPYARD_JVM_XMS_MIB}m"
    "-Xmx${CHIPYARD_JVM_XMX_MIB}m"
    "-Xss8M"
    "-XX:+UseG1GC"
    "-XX:ActiveProcessorCount=${CHIPYARD_BUILD_JOBS}"
    "-Dsbt.task.cpus=${CHIPYARD_BUILD_JOBS}"
    "-Djava.io.tmpdir=${_chipyard_java_tmp}"
)
JAVA_TOOL_OPTIONS="${_chipyard_java_options[*]}"

export CHIPYARD_CPU_PERCENT
export CHIPYARD_COMPILE_JOB_MIB
export CHIPYARD_JVM_MAX_MIB
export CHIPYARD_JVM_MIN_MIB
export CHIPYARD_SYSTEM_RESERVE_MIB
export CHIPYARD_AVAILABLE_CPUS
export CHIPYARD_CPU_TARGET_JOBS
export CHIPYARD_MEMORY_LIMIT_JOBS
export CHIPYARD_BUILD_JOBS
export CHIPYARD_EFFECTIVE_AVAILABLE_MIB
export CHIPYARD_JVM_XMS_MIB
export CHIPYARD_JVM_XMX_MIB
export JAVA_TOOL_OPTIONS

chipyard_resource_report() {
    printf '%s\n' \
        "Chipyard build resource policy:" \
        "  available CPUs:       ${CHIPYARD_AVAILABLE_CPUS}" \
        "  CPU target jobs:      ${CHIPYARD_CPU_TARGET_JOBS}" \
        "  memory-limit jobs:    ${CHIPYARD_MEMORY_LIMIT_JOBS}" \
        "  build jobs:           ${CHIPYARD_BUILD_JOBS}" \
        "  effective memory:     ${CHIPYARD_EFFECTIVE_AVAILABLE_MIB} MiB" \
        "  JVM Xms:              ${CHIPYARD_JVM_XMS_MIB} MiB" \
        "  JVM Xmx:              ${CHIPYARD_JVM_XMX_MIB} MiB" \
        "  MAKEFLAGS:            ${MAKEFLAGS:-<inherited jobserver>}" \
        "  SBT_OPTS:             ${SBT_OPTS:-<unset>}" \
        "  JAVA_TOOL_OPTIONS:    ${JAVA_TOOL_OPTIONS}"
}

unset _chipyard_setting
unset _chipyard_host_available_kib _chipyard_host_available_mib
unset _chipyard_cgroup_available_mib _chipyard_cgroup_limit_found
unset _chipyard_cgroup_path _chipyard_cgroup_dir
unset _chipyard_memory_max _chipyard_memory_current _chipyard_memory_reserve_mib
unset _chipyard_existing_sbt_options _chipyard_sbt_options
unset _chipyard_existing_java_options _chipyard_java_options _chipyard_option
unset _chipyard_resource_root _chipyard_java_tmp
unset -f _chipyard_resource_error _chipyard_require_positive_integer
