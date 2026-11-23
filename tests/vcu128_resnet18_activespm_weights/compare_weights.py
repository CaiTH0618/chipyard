#!/usr/bin/env python3
"""Compare same-ELF, same-hardware SPM activation runs with/without pinned weights."""
import argparse
import json
import math
from pathlib import Path
from compare_performance import metrics


def compare(base, resident):
    for r in (base, resident):
        if (r.get('status'), r.get('check'), r.get('abi'), r.get('hardware'), r.get('storage')) != ('PASS', 'perf', 4, 'activespm', 'spm'):
            raise ValueError('Need passing ABI 4 PERF reports on ActiveSPM hardware, with SPM activations')
        if not r.get('eligible_for_performance_comparison') or not r.get('soc_mhz', 0):
            raise ValueError('Missing PERF eligibility/frequency')
        if r.get('activespm_dma_started') is not False or not r.get('work_guards_verified_by_target'):
            raise ValueError('Unexpected DMA or missing guards')
        if any(s['state'] != 3 or s['error'] or s['mismatches'] for s in r['statuses']):
            raise ValueError('Target state not clean')
        if r['final_outputs_checked'] != r['sample_count']:
            raise ValueError('Incomplete final verification')
        if r.get('setup_makespan_cycles', 0) <= 0:
            raise ValueError('Missing setup measurement')
        metrics(r)
    if (base.get('weight_mode'), resident.get('weight_mode')) != ('none', 'resident'):
        raise ValueError('Need --baseline weights=none and --resident weights=resident')
    keys = ['elf_sha256', 'manifest_sha256', 'backend_sha256', 'resadd_direct_sha256',
            'runtime_sha256', 'resident_weights_sha256', 'weight_plan_sha256',
            'layout_sha256', 'model_blob_sha256', 'source_full_manifest_sha256',
            'quant_manifest_sha256', 'mode', 'swap', 'warmup', 'repeats', 'sample_count',
            'soc_mhz', 'poll_sec', 'active_harts', 'final_check_scope', 'cache_condition',
            'capacity', 'weight_profile', 'initialization_excluded_from_iteration_cycles']
    for k in keys:
        if k not in base or k not in resident or base[k] != resident[k]:
            raise ValueError('Reports differ or are missing: ' + k)
    if base.get('bitstream_sha256') and resident.get('bitstream_sha256') and base['bitstream_sha256'] != resident['bitstream_sha256']:
        raise ValueError('Bitstream hashes differ')
    if not resident.get('resident_weights_verified_before_and_after'):
        raise ValueError('Resident data not verified before/after inference')
    for h in resident['active_harts']:
        s = resident['resident_weight_statuses'][h]
        if not all(s[k] for k in ('verified', 'final_verified', 'guard_ok', 'entries', 'bytes')):
            raise ValueError('Missing resident weight verification')
    if any(any(s.values()) for s in base['resident_weight_statuses']):
        raise ValueError('Baseline unexpectedly used resident SRAM')
    for key in ('source_sha256', 'logits_int32'):
        if [p[key] for p in base['predictions']] != [p[key] for p in resident['predictions']]:
            raise ValueError('Prediction/input mismatch: ' + key)
    if [r['owner'] for r in base['task_results']] != [r['owner'] for r in resident['task_results']]:
        raise ValueError('Task assignment differs')
    b, r = metrics(base), metrics(resident)
    before = {x['node']: x for x in base['per_node_timings']}
    after = {x['node']: x for x in resident['per_node_timings']}
    if before.keys() != after.keys() or len(before) != 30:
        raise ValueError('Need all 30 node timings')
    rows = []
    for name, x in before.items():
        y = after[name]
        if x['executions'] != y['executions'] or x['weight_selected'] != y['weight_selected']:
            raise ValueError('Node timing contract differs')
        a, c = x['mean_cycles_per_execution'], y['mean_cycles_per_execution']
        if a <= 0 or c <= 0:
            raise ValueError('Invalid node timing')
        rows.append(dict(node=name, weight_resident=y['weight_resident'],
                         baseline_mean_cycles=a, resident_mean_cycles=c,
                         saved_cycles_per_execution=a-c, speedup=a/c))
    saved = b['mean_batch_cycles']-r['mean_batch_cycles']
    setup_delta = resident['setup_makespan_cycles']-base['setup_makespan_cycles']
    batches = math.ceil(max(0, setup_delta)/saved) if saved > 0 else None
    return dict(status='PASS', comparison='same hardware/ELF: activation-only SPM vs activation+weight SPM',
                baseline=b, resident=r, speedup=b['mean_batch_cycles']/r['mean_batch_cycles'],
                latency_reduction_percent=saved/b['mean_batch_cycles']*100,
                baseline_setup_cycles=base['setup_makespan_cycles'],
                resident_setup_cycles=resident['setup_makespan_cycles'],
                additional_setup_cycles=setup_delta,
                estimated_batches_to_amortize_additional_setup=batches,
                baseline_setup_plus_first_batch_cycles=base['setup_plus_first_batch_cycles'],
                resident_setup_plus_first_batch_cycles=resident['setup_plus_first_batch_cycles'],
                resident_weight_statuses=resident['resident_weight_statuses'],
                selected_weights=resident['resident_weight_plan']['entries'], per_node=rows,
                note='Initialization includes byte verification; amortization is a steady-cost estimate, not measured cold-start break-even. Startup/ELF loading, guaranteed cold cache and HBM traffic are not measured. A slower result is valid; no speedup is assumed.')


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--baseline', type=Path, required=True)
    p.add_argument('--resident', type=Path, required=True)
    p.add_argument('--out', type=Path, default=Path('reports/weight_comparison.json'))
    a = p.parse_args()
    result = compare(json.loads(a.baseline.read_text()), json.loads(a.resident.read_text()))
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(result, indent=2)+'\n')
    print(f"Resident speedup: {result['speedup']:.6f}x; latency reduction: {result['latency_reduction_percent']:.3f}%")
    print('Additional setup cycles:', result['additional_setup_cycles'])
    print('Estimated batches to amortize setup:', result['estimated_batches_to_amortize_additional_setup'])
    for row in result['per_node']:
        if row['weight_resident']:
            print(f"{row['node']}: {row['speedup']:.4f}x; saved {row['saved_cycles_per_execution']:.1f} cycles/execution")
    print('Saved:', a.out)
