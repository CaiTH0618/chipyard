"""Whole immutable weights in the unused SPM tail; no tensor repacking."""
import json
from memory_plan import align64, sha

SPM_BYTES = 1048576
PROFILES = ('early', 'layer2-large', 'manual')


def make_weight_plan(model, layout, profile='early', nodes=None):
    if profile not in PROFILES:
        raise ValueError('Unknown weight profile')
    arrays = {a['offset']: a for a in model['arrays']}
    candidates = []
    seen = set()
    for c in model['cases']:
        r = c['b']
        if c['op'] not in (1, 2) or not r or r['kind'] != 'blob':
            continue
        a = arrays[r['offset']]
        if a['role'] != 'weight' or a['dtype'] != '|i1':
            raise ValueError('Expected an immutable INT8 weight array')
        if r['offset'] in seen:
            continue
        seen.add(r['offset'])
        # Move the existing bytes unchanged: Gemmini layout/scales stay frozen.
        candidates.append(dict(node=c['node'], name=a['name'], source_offset=a['offset'],
                               bytes=a['bytes'], sha256=a['sha256'],
                               frame_bytes=align64(a['bytes']) + 128))
    if not candidates:
        raise ValueError('No complete weight arrays available')
    available = SPM_BYTES - layout['hart_work_bytes']
    requested = list(nodes or [])
    if profile == 'manual':
        if not requested or len(set(requested)) != len(requested):
            raise ValueError('Manual selection needs distinct --weight-nodes')
        known = {c['node'] for c in candidates}
        if set(requested) - known:
            raise ValueError('Unknown weight node(s): ' + ', '.join(sorted(set(requested) - known)))
        choices = [c for c in candidates if c['node'] in requested]
    elif profile == 'layer2-large':
        if requested:
            raise ValueError('--weight-nodes requires --weight-profile manual')
        choices = [c for c in candidates if c['node'] in
                   ('layer2.0.main_preadd', 'layer2.1.conv1_relu', 'layer2.1.main_preadd')]
    else:
        if requested:
            raise ValueError('--weight-nodes requires --weight-profile manual')
        choices = candidates
    cursor = layout['hart_work_bytes']
    entries = []
    for c in choices:
        if cursor + c['frame_bytes'] > SPM_BYTES:
            if profile != 'early':
                raise ValueError('Selected complete weights plus guards exceed SPM tail')
            continue
        entries.append(dict(c, data_offset=cursor + 64))
        cursor += c['frame_bytes']
    if not entries:
        raise ValueError('No selected weight fits into SPM tail')
    by_offset = {e['source_offset']: i for i, e in enumerate(entries)}
    refs = []
    for c in model['cases']:
        r = c['b']
        refs.append(by_offset.get(r['offset'], -1)
                    if c['op'] in (1, 2) and r and r['kind'] == 'blob' else -1)
    payload = sum(e['bytes'] for e in entries)
    return dict(version=1, profile=profile, requested_nodes=requested,
                selection_policy='execution-order first-fit of whole weights; not a measured optimum'
                if profile == 'early' else 'explicit complete-layer selection',
                region_offset=layout['hart_work_bytes'], available_bytes=available,
                region_bytes=cursor-layout['hart_work_bytes'], payload_bytes=payload,
                unused_bytes=SPM_BYTES-cursor, occupied_bytes=cursor,
                entries=entries, candidates=candidates, stage_refs=refs)


def plan_hash(plan):
    return sha(json.dumps(plan, sort_keys=True).encode())


def validate_weight_plan(manifest):
    p = manifest['weights']
    expected = make_weight_plan(manifest['model'], manifest['layout'],
                                p['profile'], p['requested_nodes'])
    if p != expected or manifest['weight_plan_sha256'] != plan_hash(p):
        raise ValueError('Resident weight plan differs')


def build_info(manifest):
    p = manifest['weights']
    return (len(p['entries']), p['payload_bytes'], p['region_offset'],
            p['region_bytes'], 0x700, 128, 24, p['version'])
