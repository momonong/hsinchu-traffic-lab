#!/usr/bin/env node
'use strict';

// Read only the public browser adapter. No research imports, fetch, or timers.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { createHash } = require('node:crypto');

const ADAPTER = 'prototype/departure-connected-2026-10-22/adapter.js';
const SCENARIOS = ['undisclosed', 'disclosed', 'changed-deadline'];
const args = process.argv.slice(2);
if (args.length !== 2 || args[0] !== '--scenario' || !SCENARIOS.includes(args[1])) {
  process.stderr.write('Usage: node scripts/contest_replay.cjs --scenario undisclosed|disclosed|changed-deadline\n');
  process.exitCode = 2;
} else {
  replay(args[1]).then(record => {
    process.stdout.write(JSON.stringify(record, null, 2) + '\n');
  }).catch(error => {
    const reason = error.name === 'ApiError' ? error.message : (error.code || 'invalid_adapter_response');
    process.stderr.write(`Replay failed: ${reason}\n`);
    process.exitCode = 1;
  });
}

async function replay(scenario) {
  const source = fs.readFileSync(path.resolve(__dirname, '..', ADAPTER));
  // The browser ES module has no imports. Evaluate the same bytes with exports
  // removed, following the existing Node UI contract harness. The context has
  // no network, filesystem, process, or timer capabilities.
  const DepartureApi = vm.runInNewContext(
    source.toString('utf8').replace(/^export /gm, '') + '\nDepartureApi;',
    { URLSearchParams }, { filename: ADAPTER, timeout: 5000 },
  );
  const api = new DepartureApi();
  const minimumGain = 5;
  const options = await api.options({
    day: '2026-08-24', nominal: '06:45',
    deadline: scenario === 'changed-deadline' ? '07:31' : '07:30',
    advance: 15, delay: 30, cost: .5,
  });
  const revealed = scenario !== 'undisclosed';
  const outcomes = revealed ? await api.outcomes(options) : null;
  const nominal = options.candidates.find(row => row.shift_minutes === 0).prediction;
  const deadline = Date.parse(`${options.request.day}T${options.request.deadline}:00+08:00`);
  const candidates = options.candidates.map(row => {
    const p = row.prediction;
    const gain = p === null || nominal === null ? null : nominal.Tq50 - p.Tq50;
    const cost = options.request.cost * Math.abs(row.shift_minutes);
    // Recompute the user's numeric conditions independently of adapter flags.
    const bounds = row.shift_minutes >= -options.request.advance && row.shift_minutes <= options.request.delay;
    const minimum = gain === null ? null : gain >= minimumGain;
    const q90 = p === null ? null : Date.parse(row.window_start) + p.Aq90 * 60000 <= deadline;
    const outcome = outcomes?.candidates.find(value => value.id === row.id);
    return {
      ...row,
      predicted_roadgain: gain,
      shift_cost: cost,
      predicted_netgain: gain === null ? null : gain - cost,
      checks: {
        within_bounds: bounds,
        gain_meets_minimum: minimum,
        q90_meets_gantry_deadline: q90,
        matches_numeric_conditions: bounds && minimum === true && q90 === true,
        policy_qualified: false,
        complete_lateness_risk_delta: null,
        not_a_recommendation: true,
      },
      observed: outcome?.observed ?? null,
      outcome_reason_codes: outcome?.reason_codes ?? [],
    };
  });
  return {
    schema_version: 1,
    record_version: 'contest-replay-v1',
    scenario,
    source: { adapter_path: ADAPTER, adapter_sha256: createHash('sha256').update(source).digest('hex') },
    scope: {
      mode: 'historical_replay', timezone: 'Asia/Taipei', vehicle_class: '31',
      gates: ['01F0750S', '01F0880S', '01F0928S', '01F0950S', '01F0980S'],
    },
    inputs: { ...options.request, minimum_predicted_road_reduction_minutes: minimumGain },
    issue_time: options.issue_time,
    data_as_of: options.data_as_of,
    model_id: options.model_id,
    qualification: options.qualification,
    decision: options.decision,
    disclosure: {
      state: revealed ? 'disclosed' : 'undisclosed',
      outcome_reveal_available: options.outcome_reveal_available,
      outcome_status: outcomes?.status ?? null,
      observed_deadline: revealed ? options.request.deadline : null,
    },
    candidates,
    summary: {
      candidate_count: candidates.length,
      matching_numeric_conditions: candidates.filter(row => row.checks.matches_numeric_conditions).length,
      formal_recommendations: 0,
      complete_lateness_risk_delta: null,
    },
    limitations: [
      'exposed_calibration_not_independent_validation',
      'different_cohorts_not_individual_causal_savings',
      'quantiles_not_confidence_intervals_or_personal_guarantees',
      'gantry_windows_not_home_to_work_journeys',
      'only_D45_observed_deadline_shares_available_other_deadlines_unknown',
      'partial_observed_quantiles_and_calibration_support_remain_unknown',
      'numeric_matches_do_not_qualify_policy_or_recommend_a_change',
      'complete_lateness_risk_difference_unknown',
      'no_new_source_request_training_or_model_forward',
      'no_UGen_measurement_or_validated_commuter_benefit',
    ],
  };
}
