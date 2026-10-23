"""Deterministic scoring of published forecasts against newly mature M06 only.

Does not select a new model or promote a policy. The original empirical display
and policy=None remain fixed even if descriptive shadow gates later pass.
"""
from __future__ import annotations

import datetime as dt
import json
import math
from pathlib import Path

import numpy as np

from .data import NOMINALS, SHIFTS
from .decision import score_quantiles, evaluate_schedule, bootstrap_netgain
from .parser import TZ
from .prospective_features import ALLOWED_DAYS, issue_slots, local_epoch
from .prospective_store import atomic_json, digest

METHODS = ('HGB', 'TCN_ensemble', 'empirical', 'current_proxy')


def verified_json(record, root):
    path = (root / record['path']).resolve()
    allowed = [root / 'reports/prospective-departure-2026-10-20', root / 'outputs/prospective-departure-2026-10-20']
    if not any(path.is_relative_to(directory.resolve()) for directory in allowed):
        raise ValueError('evaluation input outside this stage')
    if digest(path) != record['sha256']: raise ValueError('published evaluation input hash mismatch')
    return json.loads(path.read_text())


def load_published_forecast(issue, root, *, expected_program=None, expected_protocol=None, expected_manifest=None):
    if issue.get('status') != 'sealed_shadow_only': return None
    publication = verified_json(issue['publication'], root)
    seal = verified_json(publication['seal'], root)
    forecast = verified_json(publication['forecast'], root)
    if seal['forecast'] != publication['forecast'] or publication['issue_id'] != seal['issue_id'] or seal['issue_id'] != forecast['issue_id']:
        raise ValueError('forecast/seal/publication identity mismatch')
    if (publication['program_sha256'] != seal['program_sha256']
            or publication['protocol_sha256'] != seal['protocol_sha256']):
        raise ValueError('publication/seal protocol identity mismatch')
    if ((expected_program is not None and publication['program_sha256'] != expected_program)
            or (expected_protocol is not None and publication['protocol_sha256'] != expected_protocol)
            or (expected_manifest is not None and forecast['input_manifest_sha256'] != expected_manifest)):
        raise ValueError('forecast differs from this admitted run contract')
    I = forecast['issue_epoch']
    complete = publication['seal_completed_epoch']
    clocks = [I, complete, forecast['input_cutoff_epoch'], forecast['finished_epoch'],
              seal['sealed_epoch'], publication['earliest_candidate_epoch']]
    if any(not isinstance(value, (int, float)) or not math.isfinite(value) for value in clocks):
        raise ValueError('nonfinite forecast clock')
    if (not publication['primary_evaluation_valid'] or publication['issue_epoch'] != I
            or forecast['input_cutoff_epoch'] != I or complete > I + 60
            or complete >= publication['earliest_candidate_epoch']
            or not I <= forecast['finished_epoch'] <= seal['sealed_epoch'] <= complete):
        raise ValueError('forecast publication clock contract mismatch')
    for source in forecast['source_inputs']:
        clocks = [source['full_body_received_epoch'], source['parse_finished_epoch']]
        if any(not isinstance(value, (int, float)) or not math.isfinite(value) for value in clocks) or max(clocks) > I:
            raise ValueError('future source in sealed issue')
    return forecast


def evaluate_available(store, check):
    from .prospective import ROOT, REPORT, record
    keys = [(day, nominal, shift) for day in ALLOWED_DAYS for nominal in NOMINALS for shift in SHIFTS]
    bags = {(day, minute): np.empty((0, 3), dtype=np.float64)
            for day in ALLOWED_DAYS for minute in range(360, 600, 15)}
    truth_inputs = []; complete_days = []
    for day in ALLOWED_DAYS:
        job = store.state['truth_days'].get(day, {})
        if job.get('status') != 'complete' or not job.get('parsed'): continue
        receipt = verified_json(job['parsed'], ROOT)
        if not receipt.get('all_required_origin_hours_complete') or receipt['day'] != day:
            raise ValueError('truth day lacks full bounded origin-hour coverage')
        path = ROOT / receipt['rows']['path']
        if digest(path) != receipt['rows']['sha256']: raise ValueError('truth selected-row hash mismatch')
        values = {minute: [] for minute in range(360, 600, 15)}
        midnight = local_epoch(day, 0)
        with path.open() as stream:
            for number, line in enumerate(stream, 1):
                if number % 1000 == 0: check()
                row = json.loads(line)
                entry = dt.datetime.fromisoformat(row['entry_time']).timestamp()
                exit_time = dt.datetime.fromisoformat(row['exit_time']).timestamp()
                minute = (entry - midnight) / 60; candidate = int(minute // 15) * 15
                if candidate not in values: raise ValueError('truth row outside fixed candidate population')
                T = row['duration_minutes']; A = (exit_time - midnight) / 60 - candidate
                if not np.isfinite([T, A]).all() or T <= 0 or not 0 <= A - T < 15:
                    raise ValueError('truth target identity invalid')
                values[candidate].append((T, A, (exit_time - midnight) / 60))
        for minute, rows in values.items(): bags[(day, minute)] = np.asarray(rows).reshape(-1, 3)
        complete_days.append(day); truth_inputs.extend([job['parsed'], receipt['rows']])
    arrays = {method: np.full((len(keys), 4), np.nan) for method in METHODS}
    indices = {key: index for index, key in enumerate(keys)}; forecast_inputs = []; statuses = {}
    for slot in issue_slots():
        issue = store.state['issues'].get(slot['issue_id'], {})
        statuses[slot['issue_id']] = issue.get('status', 'not_yet_observed')
        forecast = load_published_forecast(issue, ROOT, expected_program=store.program_sha,
                                           expected_protocol=store.protocol_sha,
                                           expected_manifest=digest(REPORT / 'input-manifest.json'))
        if forecast is None: continue
        if forecast['day'] != slot['day'] or forecast['nominal'] != slot['nominal'] or forecast['issue_epoch'] != slot['issue_epoch']:
            raise ValueError('scheduled issue/forecast mismatch')
        forecast_inputs.extend([issue['forecast'], issue['seal'], issue['publication']])
        for method in METHODS:
            values = np.asarray(forecast['methods'][method]['final'], dtype=float)
            if values.shape != (5, 4): raise ValueError('fixed five-candidate/four-head shape mismatch')
            for shift, value in zip(SHIFTS, values): arrays[method][indices[(slot['day'], slot['nominal'], shift)]] = value
    result = {'status': 'descriptive_prospective_shadow_not_policy_promotion', 'as_of_epoch': store.clock(),
              'fixed_scheduled_days': list(ALLOWED_DAYS), 'scheduled_issues': 60, 'scheduled_candidates': 300,
              'complete_truth_days': complete_days, 'pending_or_blocked_truth_days': [day for day in ALLOWED_DAYS if day not in complete_days],
              'issue_statuses': statuses, 'forecast_display_method': 'empirical', 'policy_selected_method': None,
              'policy_qualified': False, 'new_selection_or_fit': False, 'methods': {},
              'qualification_requires_all_five_truth_days': True, 'inputs': truth_inputs + forecast_inputs}
    # Nominal is an action baseline even when every model abstains. It is not a
    # claim that keeping the nominal window is optimal.
    result['fixed_nominal_baseline'] = evaluate_schedule(arrays['empirical'], keys, bags, .5, fixed_shift=0)
    for method, predictions in arrays.items():
        check()
        metrics = score_quantiles(predictions, keys, bags)
        policies = {}
        for cost in (0, .25, .5, 1):
            policies[str(cost)] = evaluate_schedule(predictions, keys, bags, cost)
            policies[str(cost)]['research_shadow_only'] = True
            policies[str(cost)]['not_a_recommendation'] = True
        primary = policies['0.5']
        bootstrap = (bootstrap_netgain(primary['issues']) if len(complete_days) == 5 else
                     {'status': 'pending_all_five_truth_days', 'interval': None, 'supports_positive_gain': False})
        common = np.isfinite(predictions).all(axis=1) & np.isfinite(arrays['empirical']).all(axis=1)
        issue_mask = np.repeat(common.reshape(60, 5).all(axis=1), 5)
        matched = predictions.copy(); matched[~issue_mask] = np.nan
        result['methods'][method] = {'metrics': metrics, 'finite_candidates': int(np.isfinite(predictions).all(axis=1).sum()),
                                     'cost_sensitivity': policies, 'day_bootstrap_primary': bootstrap,
                                     'common_issue_mask_with_empirical': common.reshape(60, 5).all(axis=1).tolist(),
                                     'matched_common_issue_full_schedule': evaluate_schedule(matched, keys, bags, .5),
                                     'diagnostic_gate_passed_but_no_promotion': len(complete_days) == 5 and primary['summary']['eligible'] and bootstrap['supports_positive_gain']}
    target = REPORT / 'evaluation' / f'after-{len(complete_days)}-truth-days.json'
    return atomic_json(target, result, immutable=True)
