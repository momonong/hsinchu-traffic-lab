"""TDCS first-observed CSV parsing; no imputation or individual-trip labels."""
from __future__ import annotations

from collections import Counter
import csv
import datetime as dt
import io
import math
import re

TZ = dt.timezone(dt.timedelta(hours=8))
DATASETS = ('M03A', 'M04A', 'M05A')
GATE = re.compile(r'^\d{2}[A-Z]\d{4}[NS]$')
CLASSES = {'31', '32', '41', '42', '5'}
GATES = ('01F0750S','01F0880S','01F0928S','01F0950S','01F0980S')
MODEL_PAIRS = (('01F0750S','01F0880S'),('01F0880S','01F0928S'),
    ('01F0928S','01F0950S'),('01F0950S','01F0980S'),
    ('01F0616S','01F0633S'),('01F0633S','01F0664S'),
    ('01F0664S','01F0681S'),('01F0681S','01F0699S'),
    ('01F0699S','01F0750S'),('01F0980S','01F1045S'))


def timestamp(value):
    """Source-local strings are explicitly Asia/Taipei, never host timezone."""
    for fmt in ('%Y-%m-%d %H:%M', '%Y/%m/%d %H:%M'):
        try:
            return dt.datetime.strptime(value.strip(), fmt).replace(tzinfo=TZ).timestamp()
        except ValueError:
            pass
    raise ValueError('invalid TDCS timestamp')


def parse_csv(body, dataset, source_epoch, available_epoch):
    if dataset not in DATASETS:
        raise ValueError('dataset not admitted')
    if source_epoch % 300 or not math.isfinite(available_epoch):
        raise ValueError('invalid source/available clock')
    raw = body.decode('utf-8-sig', errors='strict')
    counts = Counter(); rows = []; seen = {}; file_errors = []
    if available_epoch < source_epoch:
        file_errors.append('source_timestamp_after_complete_receipt')
    width = 5 if dataset == 'M03A' else 6
    for line, fields in enumerate(csv.reader(io.StringIO(raw), strict=True), 1):
        if len(fields) != width:
            raise ValueError(f'CSV width mismatch at line{line}: expected{width}')
        fields = [v.strip() for v in fields]
        reasons = []; stamp = timestamp(fields[0])
        if stamp != source_epoch:
            reasons.append('timestamp_mismatch')
        first = fields[1].upper(); second = fields[2].upper()
        if not GATE.fullmatch(first): reasons.append('invalid_gate_id')
        if dataset == 'M03A':
            if second not in ('N', 'S') or first[-1:] != second:
                reasons.append('direction_mismatch')
            vehicle = fields[3]; value_text = None; volume_text = fields[4]
        else:
            if not GATE.fullmatch(second): reasons.append('invalid_gate_id')
            if first[-1:] != second[-1:]: reasons.append('cross_direction_pair')
            if first == second: reasons.append('same_gate_pair')
            vehicle = fields[3]; value_text = fields[4]; volume_text = fields[5]
        if vehicle not in CLASSES: reasons.append('unknown_vehicle_class')
        try:
            if not re.fullmatch(r'[+-]?\d+', volume_text): raise ValueError()
            volume = int(volume_text)
        except ValueError:
            volume = None; reasons.append('missing_or_invalid_volume')
        if volume is not None and volume < 0: reasons.append('negative_volume')
        measurement = None
        if value_text is not None:
            try:
                measurement = float(value_text)
                if not math.isfinite(measurement): raise ValueError()
            except ValueError:
                measurement = None; reasons.append('missing_or_nonfinite_measurement')
            if measurement is not None and measurement < 0: reasons.append('negative_measurement')
            if dataset == 'M04A' and measurement == 0 and volume and volume > 0:
                reasons.append('zero_travel_time_with_positive_volume')
        zero_volume = volume == 0
        if zero_volume: counts['zero_volume_rows'] += 1
        if measurement == 0: counts['zero_measurement_rows'] += 1
        key = (stamp, first, second, vehicle)
        row = {'line': line, 'source_epoch': stamp, 'gate_from': first,
               'direction' if dataset == 'M03A' else 'gate_to': second,
               'vehicle_class': vehicle, 'volume': volume,
               'raw_measurement': measurement, 'zero_volume': zero_volume,
               'measurement_available': dataset != 'M03A' and not reasons and not zero_volume,
               'reasons': reasons}
        if key in seen:
            reasons.append('duplicate_key')
            rows[seen[key]]['reasons'].append('duplicate_key')
            rows[seen[key]]['measurement_available'] = False
            counts['duplicate_key_rows'] += 1
        else:
            seen[key] = len(rows)
        rows.append(row)
    if not rows: raise ValueError('empty CSV body')
    for row in rows:
        row['reasons'] = sorted(set(row['reasons']))
        row['healthy'] = not row['reasons'] and not file_errors
        row['measurement_available'] &= row['healthy']
        row['value'] = row['raw_measurement'] if row['measurement_available'] else None
        counts.update(row['reasons'])
    if counts['timestamp_mismatch']: file_errors.append('timestamp_mismatch')
    context = [r for r in rows if r['vehicle_class']=='31' and
               (r['gate_from'] in GATES if dataset=='M03A' else
                (r['gate_from'],r['gate_to']) in MODEL_PAIRS)]
    return {'dataset': dataset, 'row_count': len(rows), 'rows': rows,
            'model_context':context,'context_expected_rows':5 if dataset=='M03A' else 10,
            'context_available_rows':sum(r['healthy'] and (dataset=='M03A' or r['measurement_available'])for r in context),
            'counts': dict(counts), 'file_errors': sorted(set(file_errors)),
            'interval_start_epoch': source_epoch - 300, 'interval_end_epoch': source_epoch,
            'full_body_received_epoch': available_epoch,
            'version': 'first_observed_not_guaranteed_original_publication',
            'timestamp_semantics': 'live source represents preceding5minutes;revised3day version differs',
            'M05_unit': 'source manual says seconds;km/h supported by consistency only,not silently corrected',
            'trip_truth': False}
