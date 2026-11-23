"""Validate measured per-stage counters and expose separately timed setup."""
import struct
from protocol import parse_weight_status, parse_status, STATUS_OFFSET


def measurements(snapshot, counters, manifest, resident, repeats, warmup):
    count = manifest['stage_count']
    if len(counters) != count*8:
        raise ValueError('Per-stage timing size differs')
    values = list(struct.unpack('<'+'Q'*count, counters))
    if any(v <= 0 for v in values):
        raise ValueError('Missing measured stage timing')
    statuses = [parse_status(snapshot[STATUS_OFFSET+h*256:STATUS_OFFSET+(h+1)*256]) for h in (0, 1)]
    if sum(values) != sum(s['operator_cycles'] for s in statuses):
        raise ValueError('Per-stage operator timing sum differs')
    weights = [parse_weight_status(snapshot[0x700+h*128:0x780+h*128]) for h in (0, 1)]
    setup = struct.unpack_from('<Q', snapshot, 0x800)[0]
    times = list(struct.unpack_from('<32Q', snapshot, 0x248)[:repeats])
    stages = []
    aggregated = {}
    p = manifest['weights']
    for i, c in enumerate(manifest['model']['cases']):
        wi = p['stage_refs'][i]
        row = dict(stage_index=i, task_index=i//30, node=c['node'],
                   measured_cycles=values[i], mean_cycles_per_execution=values[i]/repeats,
                   weight_selected=wi >= 0, weight_resident=bool(resident and wi >= 0))
        stages.append(row)
        a = aggregated.setdefault(c['node'], dict(node=c['node'], measured_cycles=0,
                                  executions=0, weight_selected=wi >= 0,
                                  weight_resident=bool(resident and wi >= 0)))
        a['measured_cycles'] += values[i]
        a['executions'] += repeats
    for a in aggregated.values():
        a['mean_cycles_per_execution'] = a['measured_cycles']/a['executions']
    return dict(weight_mode='resident' if resident else 'none', weight_profile=p['profile'],
                weight_plan_sha256=manifest['weight_plan_sha256'],
                resident_weights_sha256=manifest['resident_weights_sha256'],
                resident_weight_plan=p, resident_weight_statuses=weights,
                resident_weights_verified_before_and_after=bool(resident),
                setup_makespan_cycles=setup,
                setup_plus_first_batch_cycles=setup+times[0] if warmup == 0 else None,
                setup_timing_note='Target hart0: after config/control checks, immediately before boot release, to first GO barrier; includes workspace guards, scalar weight copy, byte-for-byte initial verification and waiting for peer. Excludes ELF loading/BSS clearing/prior startup. First-batch sum available only with warmup=0; not a guaranteed cold-cache measurement.',
                initialization_excluded_from_iteration_cycles=True,
                per_stage_timings=stages, per_node_timings=list(aggregated.values()))
