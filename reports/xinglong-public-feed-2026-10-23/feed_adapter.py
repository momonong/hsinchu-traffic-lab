"""Offline HSQ ETag normalizer. No network, model, service or persistence DB."""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
import math
from pathlib import Path

PAIRS = {'ES-0075': ('E-25019', 'E-25018'), 'ES-0011': ('E-25018', 'E-25057')}
VEHICLE_TYPE = 3
DEFAULT_MAX_AGE_SECONDS = 900  # Engineering display limit, not a research gate/SLA.
MAX_WINDOWS, MAX_RECORDS, MAX_BUFFER_BYTES = 48, 96, 131072


def aware(value):
    if isinstance(value, str):
        value = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    if not isinstance(value, dt.datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('timezone-aware datetime required')
    return value


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def safe(value):
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)  # Preserve the anomaly, never impute zero.
    if isinstance(value, dict):
        return {str(k): safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [safe(v) for v in value]
    return value


def encoded(value):
    return json.dumps(safe(value), ensure_ascii=False, allow_nan=False,
                      sort_keys=True, separators=(',', ':')).encode()


def freshness(end_epoch, asof_epoch, max_age_seconds):
    if not finite(max_age_seconds) or max_age_seconds <= 0:
        raise ValueError('positive finite engineering max_age_seconds required')
    if end_epoch is None:
        return {'status': 'unknown', 'age_seconds': None}
    age = asof_epoch-end_epoch
    return {'status': 'future' if age < 0 else 'stale' if age > max_age_seconds else 'fresh',
            'age_seconds': age}


def visibility(segment, as_of, max_age_seconds):
    """Re-evaluate clocks from immutable observations, including every buffer view."""
    value = copy.deepcopy(segment)
    epoch = aware(as_of).timestamp()
    value['freshness'] = freshness(value['window']['end_epoch'], epoch, max_age_seconds)
    value['freshness']['max_age_seconds'] = max_age_seconds
    value['freshness']['limit_semantics'] = 'engineering_display_limit_not_research_threshold'
    receipt = value['receipt']
    anomalies = list(value['clock_integrity']['base_anomalies'])
    full_epoch = aware(receipt['full_body_received_at']).timestamp() if receipt['status'] == 'valid' else None
    for field, source_epoch in value['clock_integrity']['source_clock_epochs'].items():
        if source_epoch > epoch:
            anomalies.append(field+'_after_as_of')
        if full_epoch is not None and source_epoch > full_epoch:
            anomalies.append(field+'_after_full_body_receipt')
    value['clock_integrity'].update(anomalies=anomalies,
        status='metadata_anomaly' if anomalies else 'consistent')
    available = receipt.get('available_epoch')
    if receipt['status'] == 'invalid':
        status = 'invalid_receipt'
    elif available is None:
        status = 'unknown_receipt'
    elif available > epoch:
        status = 'not_yet_available'
    elif value['freshness']['status'] == 'future':
        status = 'future_observation'
    else:
        status = 'known'
    value['availability'] = {
        'status': status,
        'available_at_asof': receipt.get('parse_completed_at') if status == 'known' else None,
        'meaning': 'complete-receipt/parse availability; separate from numeric usability',
    }
    value['usable_asof'] = (status == 'known' and value['quality']['status'] == 'valid'
                            and value['freshness']['status'] == 'fresh'
                            and not value['clock_integrity']['anomalies'])
    value['as_of'] = aware(as_of).isoformat()
    return value


def normalize(static, live, *, as_of, receipt=None, max_age_seconds=DEFAULT_MAX_AGE_SECONDS):
    """Only the fixed two pairs and VehicleType3. Caller receipts are never guessed."""
    as_of = aware(as_of)
    if not finite(max_age_seconds) or max_age_seconds <= 0:
        raise ValueError('positive finite engineering max_age_seconds required')
    if not isinstance(static, dict) or not isinstance(live, dict):
        raise ValueError('county static/live objects required')
    if not isinstance(static.get('ETagPairs'), list) or not isinstance(live.get('ETagPairLives'), list):
        raise ValueError('county ETagPairs/ETagPairLives arrays required')
    identity_errors = []
    if static.get('AuthorityCode') != 'HSQ' or live.get('AuthorityCode') != 'HSQ':
        identity_errors.append('authority_not_HSQ')
    version = static.get('LinkVersion')
    if not isinstance(version, str) or not version or version != live.get('LinkVersion'):
        identity_errors.append('static_live_LinkVersion_mismatch_or_unknown')
    if any(not isinstance(row, dict) for row in static['ETagPairs']+live['ETagPairLives']):
        identity_errors.append('non_object_pair_record')
    segments = []
    source_metadata = {k: live.get(k) for k in ('UpdateTime', 'SrcUpdateTime', 'UpdateInterval', 'SrcUpdateInterval')}
    for pair_id, endpoints in PAIRS.items():
        errors, unknown = list(identity_errors), []
        ss = [row for row in static['ETagPairs'] if isinstance(row, dict) and row.get('ETagPairID') == pair_id]
        ll = [row for row in live['ETagPairLives'] if isinstance(row, dict) and row.get('ETagPairID') == pair_id]
        if len(ss) != 1:
            errors.append('static_pair_missing' if not ss else 'duplicate_static_pair')
        if len(ll) != 1:
            errors.append('live_pair_missing' if not ll else 'duplicate_live_pair')
        s, row = (ss[0] if len(ss) == 1 else {}), (ll[0] if len(ll) == 1 else {})
        endpoint_ok = (s.get('StartETagGantryID'), s.get('EndETagGantryID')) == endpoints
        if not endpoint_ok:
            errors.append('static_endpoint_identity_mismatch')
        for field in ('StartETagStatus', 'EndETagStatus'):
            if type(row.get(field)) is not int or row.get(field) != 0:
                errors.append(field+'_not_normal_zero')
        start = end = collected = None
        window_raw = {k: row.get(k) for k in ('StartTime', 'EndTime', 'DataCollectTime')}
        try:
            start, end, collected = [aware(window_raw[k]).timestamp() for k in ('StartTime', 'EndTime', 'DataCollectTime')]
            if not start < end <= collected:
                raise ValueError('source window order')
        except (ValueError, TypeError, OverflowError):
            errors.append('invalid_or_unknown_observation_window')
            start = end = collected = None
        flows = row.get('Flows')
        if not isinstance(flows, list) or any(not isinstance(f, dict) for f in flows):
            errors.append('invalid_Flows_array'); flows = []
        types = [f.get('VehicleType') for f in flows]
        if any(type(code) is not int for code in types):
            errors.append('invalid_VehicleType_identifier')
        if any(types.count(code) > 1 for code in types):
            errors.append('duplicate_flow_VehicleType')
        selected = [f for f in flows if type(f.get('VehicleType')) is int and f['VehicleType'] == VEHICLE_TYPE]
        if len(selected) != 1:
            unknown.append('VehicleType3_not_reported' if not selected else 'duplicate_VehicleType3')
        f = selected[0] if len(selected) == 1 else {}
        n = f.get('VehicleCount')
        if len(selected) == 1 and (type(n) is not int or n < 0):
            errors.append('invalid_or_unknown_sample_count')
        elif len(selected) == 1 and n == 0:
            unknown.append('zero_paired_samples')
        stats = [f.get(k) for k in ('TravelTime', 'StandardDeviation', 'SpaceMeanSpeed')]
        # Fixed nonzero road segments require positive travel time. SD and
        # reported speed may be zero; zero speed is retained, not called a jam.
        numeric = all(finite(v) and v >= 0 for v in stats) and stats[0] > 0
        count_positive = type(n) is int and n > 0
        if count_positive and not numeric:
            errors.append('nonpositive_TT_negative_nonfinite_or_missing_statistics')
        clock_anomalies, source_epochs = [], {}
        for field in ('UpdateTime', 'SrcUpdateTime'):
            try:
                source_clock = aware(source_metadata[field]).timestamp()
                source_epochs[field] = source_clock
                if end is not None and source_clock < end:
                    clock_anomalies.append(field+'_precedes_observation_end')
            except (ValueError, TypeError, OverflowError):
                clock_anomalies.append(field+'_unknown_or_invalid')
        receipt_value = {'status': 'unknown', 'full_body_received_at': None,
                         'parse_completed_at': None, 'available_epoch': None, 'reasons': []}
        if receipt is not None:
            try:
                full = aware(receipt['full_body_received_at'])
                parsed = aware(receipt['parse_completed_at'])
                if end is None or collected is None or not max(end, collected) <= full.timestamp() <= parsed.timestamp():
                    raise ValueError('receipt must follow observation end/collection and parse must follow full body')
                receipt_value.update(status='valid', full_body_received_at=full.isoformat(),
                    parse_completed_at=parsed.isoformat(), available_epoch=parsed.timestamp())
            except (ValueError, TypeError, KeyError, OverflowError):
                receipt_value.update(status='invalid', reasons=['receipt_unknown_or_clock_order_invalid'])
        value = {
            'pair_id': pair_id,
            'static_identity': {'authority': static.get('AuthorityCode'), 'LinkVersion': version,
                'StartETagGantryID': s.get('StartETagGantryID'), 'EndETagGantryID': s.get('EndETagGantryID'),
                'Description': s.get('Description'), 'LinkIDs': s.get('LinkIDs')},
            'identity_valid': not identity_errors and len(ss) == len(ll) == 1 and endpoint_ok,
            'window': {'raw': window_raw, 'start_epoch': start, 'end_epoch': end,
                'data_collect_epoch': collected, 'semantics': 'end_gantry_crossing_window_not_departure_cohort'},
            'vehicle_type': VEHICLE_TYPE, 'vehicle_type_meaning': 'county_small_vehicle_not_TDCS31_equivalence',
            'measurement': {'paired_sample_count': n,
                'mean_travel_time_seconds': stats[0] if numeric and count_positive else None,
                'standard_deviation_seconds': stats[1] if numeric and count_positive else None,
                'space_mean_speed_kmh': stats[2] if numeric and count_positive else None},
            'reported_statistics': {k: f.get(k) for k in ('VehicleCount', 'TravelTime', 'StandardDeviation', 'SpaceMeanSpeed')},
            'quality': {'status': 'invalid' if errors else 'unknown' if unknown else 'valid',
                'numeric_sample_valid': numeric and count_positive, 'reasons': errors+unknown},
            'zero_speed_reported': f.get('SpaceMeanSpeed') == 0,
            'source_metadata': source_metadata,
            'clock_integrity': {'status': 'metadata_anomaly' if clock_anomalies else 'consistent',
                'base_anomalies': clock_anomalies, 'source_clock_epochs': source_epochs,
                'anomalies': clock_anomalies, 'source_values_not_repaired': True},
            'receipt': receipt_value,
            'issue_vintage_verified': False,
        }
        value['fingerprint'] = hashlib.sha256(encoded({'static': s, 'live': row,
            'versions': [version, live.get('LinkVersion')], 'source_metadata': source_metadata})).hexdigest()
        segments.append(visibility(safe(value), as_of, max_age_seconds))
    return {'schema_version': 1, 'kind': 'offline_normalized_county_snapshot', 'as_of': as_of.isoformat(),
        'segments': segments, 'whole_route_ETA': None, 'trend': None, 'recommendation': None,
        'source_connected': False, 'continuous_history_established': False,
        'limits': 'Two segment means are not summed; sparse samples do not establish reliable forecasts.'}


class RollingBuffer:
    """Bounded revisions, not a history DB. Eviction never substitutes a newer version."""
    def __init__(self, *, max_windows=MAX_WINDOWS, max_records=MAX_RECORDS, max_bytes=MAX_BUFFER_BYTES):
        if not (1 <= max_windows <= MAX_WINDOWS and 1 <= max_records <= MAX_RECORDS
                and 1 <= max_bytes <= MAX_BUFFER_BYTES):
            raise ValueError('buffer limits exceed fixed caps')
        self.max_windows, self.max_records, self.max_bytes = max_windows, max_records, max_bytes
        self.records, self.last_seen, self.evicted_seen = [], None, None

    @staticmethod
    def key(segment):
        return (segment['pair_id'], segment['window']['start_epoch'], segment['window']['end_epoch'])

    @property
    def bytes_used(self):
        return sum(len(encoded(r)) for r in self.records)

    def ingest(self, frame):
        seen = aware(frame['as_of']).timestamp()
        if self.last_seen is not None and seen < self.last_seen:
            raise ValueError('buffer ingest clock rollback; no backfill')
        self.last_seen = seen
        results = []
        for value in frame['segments']:
            key = self.key(value)
            available = value['receipt'].get('available_epoch')
            if (not value['identity_valid'] or value['pair_id'] not in PAIRS
                    or key[1] is None or not finite(available) or available > seen):
                results.append({'pair_id': value['pair_id'], 'status': 'rejected_unknown_identity_window_or_receipt'}); continue
            prior = [r for r in self.records if self.key(r['segment']) == key]
            identities = [r['segment']['static_identity'] for r in self.records if r['segment']['pair_id'] == value['pair_id']]
            if identities and value['static_identity'] != identities[-1]:
                results.append({'pair_id': value['pair_id'], 'status': 'rejected_identity_version_change'}); continue
            if any(r['segment']['fingerprint'] == value['fingerprint'] for r in prior):
                results.append({'pair_id': value['pair_id'], 'status': 'duplicate_ignored'}); continue
            if prior and available <= prior[-1]['segment']['receipt']['available_epoch']:
                results.append({'pair_id': value['pair_id'], 'status': 'rejected_revision_receipt_not_later'}); continue
            record = {'segment': copy.deepcopy(value), 'buffer_seen_epoch': seen,
                'revision': max((r['revision'] for r in prior), default=-1)+1,
                'revision_of': prior[-1]['segment']['fingerprint'] if prior else None}
            if len(encoded(record)) > self.max_bytes:
                results.append({'pair_id': value['pair_id'], 'status': 'rejected_record_too_large'}); continue
            self.records.append(record)
            while True:
                windows = {(r['segment']['window']['start_epoch'], r['segment']['window']['end_epoch']) for r in self.records}
                if len(windows) <= self.max_windows:
                    break
                oldest = min(windows, key=lambda x: x[1])
                self._evict([r for r in self.records if (r['segment']['window']['start_epoch'], r['segment']['window']['end_epoch']) == oldest])
            while len(self.records) > self.max_records or self.bytes_used > self.max_bytes:
                self._evict([self.records[0]])
            results.append({'pair_id': value['pair_id'], 'status': 'revision_recorded' if prior else 'recorded',
                            'revision': record['revision']})
        return results

    def _evict(self, rows):
        for row in rows:
            self.evicted_seen = max(self.evicted_seen or row['buffer_seen_epoch'], row['buffer_seen_epoch'])
            self.records.remove(row)

    def view(self, *, as_of, max_age_seconds=DEFAULT_MAX_AGE_SECONDS):
        now = aware(as_of); epoch = now.timestamp(); chosen = {}
        for row in self.records:
            if row['buffer_seen_epoch'] <= epoch and row['segment']['receipt']['available_epoch'] <= epoch:
                chosen[self.key(row['segment'])] = row
        return {'as_of': now.isoformat(), 'records': [
            {'revision': row['revision'], 'revision_of': row['revision_of'],
             'buffer_seen_at': dt.datetime.fromtimestamp(row['buffer_seen_epoch'], dt.timezone.utc).isoformat(),
             'segment': visibility(row['segment'], now, max_age_seconds)} for row in chosen.values()],
            'history_may_be_incomplete': self.evicted_seen is not None,
            'record_count': len(self.records), 'serialized_bytes': self.bytes_used,
            'continuous_history_established': False, 'trend': None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--static', type=Path, required=True)
    parser.add_argument('--live', type=Path, required=True)
    parser.add_argument('--as-of', help='tz-aware ISO; omitted means actual execution UTC now')
    parser.add_argument('--full-body-received-at')
    parser.add_argument('--parse-completed-at')
    parser.add_argument('--max-age-seconds', type=int, default=DEFAULT_MAX_AGE_SECONDS)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if bool(args.full_body_received_at) != bool(args.parse_completed_at):
        parser.error('both explicit receipt clocks are required together')
    paths = [args.static, args.live]
    if any(p.stat().st_size > 1000000 for p in paths):
        parser.error('local input exceeds1MB limit')
    output = args.output.resolve()
    if not output.is_relative_to(Path(__file__).resolve().parent):
        parser.error('diagnostic output must remain in this isolated stage')
    inputs = [p.read_bytes() for p in paths]
    receipt = {'full_body_received_at': args.full_body_received_at,
               'parse_completed_at': args.parse_completed_at} if args.full_body_received_at else None
    as_of = aware(args.as_of) if args.as_of else dt.datetime.now(dt.timezone.utc)
    result = normalize(*(json.loads(data) for data in inputs), as_of=as_of,
                       receipt=receipt, max_age_seconds=args.max_age_seconds)
    result['input_files'] = [{'name': p.name, 'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}
                             for p, data in zip(paths, inputs)]
    data = encoded(result)+b'\n'
    if len(data) > 20000:
        parser.error('diagnostic exceeds20KB cap')
    with output.open('xb') as stream:
        stream.write(data)
    print(json.dumps({'status': 'offline_snapshot_normalized', 'as_of': result['as_of'],
        'output_bytes': len(data), 'output_sha256': hashlib.sha256(data).hexdigest(),
        'segments': [{'pair_id': s['pair_id'], 'quality': s['quality']['status'],
            'freshness': s['freshness']['status'], 'availability': s['availability']['status'],
            'usable_asof': s['usable_asof']} for s in result['segments']],
        'HTTP': 0, 'fit': 0, 'model_forward': 0}, ensure_ascii=False))


if __name__ == '__main__':
    main()
