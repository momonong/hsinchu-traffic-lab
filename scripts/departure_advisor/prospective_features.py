"""Pure first-received TDCS feature construction for frozen shadow models.

No HTTP, filesystem writes, fits, model forward or old outcome reads occur here.
"""
from __future__ import annotations

import datetime as dt
import math

import numpy as np

from .data import NOMINALS, SHIFTS, case_features
from .parser import MODEL_PAIRS, TZ

ALLOWED_DAYS = ('2026-10-21', '2026-10-22', '2026-10-23', '2026-10-26', '2026-10-27')


def local_epoch(day, minute):
    return (dt.datetime.fromisoformat(day).replace(tzinfo=TZ) + dt.timedelta(minutes=minute)).timestamp()


def issue_identity(day, nominal):
    if day not in ALLOWED_DAYS or nominal not in NOMINALS:
        raise ValueError('outside fixed prospective morning schedule')
    return f'{day}-N{nominal:04d}'


def issue_slots():
    return [dict(issue_id=issue_identity(day, nominal), day=day, nominal=nominal,
                 issue_epoch=local_epoch(day, nominal - 45), deadline_epoch=local_epoch(day, nominal + 45))
            for day in ALLOWED_DAYS for nominal in NOMINALS]


def source_slots(day):
    if day not in ALLOWED_DAYS:
        raise ValueError('source day not admitted')
    return [dict(source_epoch=local_epoch(day, minute), due_epoch=local_epoch(day, minute) + 540)
            for minute in range(260, 521, 5)]


def restart_missed_slots(now, existing_issue_ids):
    """Past slots are explicit misses, never historical forecast opportunities."""
    return [slot for slot in issue_slots()
            if slot['issue_epoch'] <= now and slot['issue_id'] not in existing_issue_ids]


def available(record):
    clocks = [record.get(key) for key in ('full_body_received_epoch', 'parse_finished_epoch')]
    if any(not isinstance(x, (int, float)) or not math.isfinite(x) for x in clocks):
        return None
    source = record.get('source_epoch')
    if not isinstance(source, (int, float)) or not math.isfinite(source):
        return None
    if any(x < source for x in clocks) or clocks[1] < clocks[0]:
        return None
    return max(clocks)


def _rows(record):
    parsed = record.get('parsed')
    if not isinstance(parsed, dict) or parsed.get('file_errors'):
        return {}
    if parsed.get('dataset') != record['dataset']:
        raise ValueError('receipt/parsed dataset mismatch')
    if parsed.get('interval_end_epoch') != record['source_epoch'] or parsed.get('interval_start_epoch') != record['source_epoch'] - 300:
        raise ValueError('receipt/parsed live interval mismatch')
    rows = {}
    for row in parsed.get('model_context', []):
        if row.get('vehicle_class') != '31':
            continue
        key = (row.get('gate_from'), row.get('gate_to'))
        if key not in MODEL_PAIRS:
            continue
        if key in rows:
            rows[key] = None  # Duplicate source keys cannot contribute a value.
        else:
            rows[key] = row
    return rows


def _value(row):
    if row is None or not row.get('healthy') or not row.get('measurement_available') or row.get('reasons'):
        return None
    value, volume = row.get('raw_measurement'), row.get('volume')
    if (not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0
            or not isinstance(volume, int) or isinstance(volume, bool) or volume <= 0):
        return None
    return float(value), volume


def build_live_features(records, day, nominal):
    """Keep canonical I-10-lag positions, including the planned latest missing slot.

    Records are ordered by durable request sequence. The first attempted source
    version stays first even if it is unhealthy, incomplete or too late.
    """
    issue_id = issue_identity(day, nominal)
    cutoff = local_epoch(day, nominal - 45)
    first, rejected, used = {}, [], []
    sequences = set()
    for record in sorted(records, key=lambda x: x['sequence']):
        sequence = record['sequence']
        if not isinstance(sequence, int) or sequence < 1 or sequence in sequences:
            raise ValueError('durable source sequence must be positive and unique')
        sequences.add(sequence)
        dataset, end = record.get('dataset'), record.get('source_epoch')
        if dataset not in ('M03A', 'M04A', 'M05A') or not isinstance(end, (int, float)) or not math.isfinite(end) or end % 300:
            raise ValueError('unadmitted source identity')
        identity = (dataset, end)
        if identity in first:
            rejected.append(dict(sequence=sequence, reason='later_source_version_never_substituted'))
            continue
        first[identity] = record

    observations = {}
    separate_counts = {i: {'M04A': 0, 'M05A': 0} for i in range(10)}
    last_bucket = nominal - 45 - 10
    grid = {local_epoch(day, last_bucket - lag): last_bucket - lag for lag in range(0, 61, 5)}
    for start, minute in grid.items():
        end = start + 300
        values = {}
        for dataset in ('M04A', 'M05A'):
            record = first.get((dataset, end))
            if record is None:
                continue
            clock = available(record)
            if clock is None or clock > cutoff or end > cutoff or record.get('status') != 'received_parsed':
                rejected.append(dict(sequence=record['sequence'], reason='not_complete_and_valid_by_issue'))
                continue
            if dt.datetime.fromtimestamp(start, TZ).date().isoformat() != day:
                rejected.append(dict(sequence=record['sequence'], reason='different_calendar_day'))
                continue
            values[dataset] = _rows(record)
            used.append({key: record.get(key) for key in ('sequence', 'dataset', 'source_epoch',
                        'payload_sha256', 'parsed_sha256', 'full_body_received_epoch', 'parse_finished_epoch')})
        for link, pair in enumerate(MODEL_PAIRS):
            a = _value(values.get('M04A', {}).get(pair))
            b = _value(values.get('M05A', {}).get(pair))
            separate_counts[link]['M04A'] += a is not None
            separate_counts[link]['M05A'] += b is not None
            if a is not None and b is not None and a[1] == b[1]:
                observations[(day, minute, link)] = (a[0] / 60, b[0], a[1])

    link_gates = []
    for link, pair in enumerate(MODEL_PAIRS):
        valid = [minute for date, minute, index in observations if date == day and index == link]
        age = (cutoff - local_epoch(day, max(valid) + 5)) if valid else None
        reasons = []
        if separate_counts[link]['M04A'] < 12: reasons.append('M04_fewer_than_12_positive_slots')
        if separate_counts[link]['M05A'] < 12: reasons.append('M05_fewer_than_12_positive_slots')
        if len(valid) < 12: reasons.append('paired_equal_count_fewer_than_12_slots')
        if age is None or age > 900 or age < 0: reasons.append('latest_valid_interval_end_not_within_15min')
        link_gates.append(dict(link=link, pair=list(pair), valid_paired_slots=len(valid),
                               positive_slots=separate_counts[link], latest_interval_end_age_seconds=age,
                               eligible=not reasons, reasons=reasons))
    matrix = np.asarray([case_features(observations, day, nominal, shift) for shift in SHIFTS], dtype=np.float32)
    return {'issue_id': issue_id, 'issue_epoch': cutoff, 'input_cutoff_epoch': cutoff,
            'raw_features': matrix, 'observations': observations,
            'coverage': {'valid_link_slots': len(observations), 'fixed_link_slots': 130, 'link_gates': link_gates},
            'input_gate_passed': all(row['eligible'] for row in link_gates),
            'source_inputs': used, 'rejected': rejected, 'feature_grid_shifted': False,
            'policy_qualified': False, 'forecast_display_method': 'empirical'}
