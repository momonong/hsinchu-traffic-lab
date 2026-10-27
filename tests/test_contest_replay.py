"""Offline CLI contracts against the public adapter; no research data or services."""
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = Path('scripts/contest_replay.cjs')
ADAPTER = Path('prototype/departure-connected-2026-10-22/adapter.js')
SCENARIOS = ('undisclosed', 'disclosed', 'changed-deadline')


def invoke(scenario, root=ROOT, cwd=None):
    return subprocess.run(
        ['node', str(root / SCRIPT), '--scenario', scenario],
        cwd=cwd or root, capture_output=True, timeout=10,
    )


class ContestReplayContracts(unittest.TestCase):
    def replay(self, scenario='undisclosed', root=ROOT, cwd=None):
        result = invoke(scenario, root, cwd)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(result.stderr, b'')
        return json.loads(result.stdout)

    def test_canonical_inputs_keep_five_windows_including_out_of_bounds(self):
        # Catches a wrong default, omitted candidate, or accidental bounds filter.
        value = self.replay()
        self.assertEqual(value['schema_version'], 1)
        self.assertEqual(value['record_version'], 'contest-replay-v1')
        self.assertEqual(value['inputs'], {
            'day': '2026-08-24', 'nominal': '06:45', 'deadline': '07:30',
            'advance': 15, 'delay': 30, 'cost': .5,
            'minimum_predicted_road_reduction_minutes': 5,
        })
        rows = value['candidates']
        self.assertEqual([r['shift_minutes'] for r in rows], [-30, -15, 0, 15, 30])
        self.assertEqual([r['window_start'][11:16] for r in rows],
                         ['06:15', '06:30', '06:45', '07:00', '07:15'])
        self.assertEqual([r['window_end'][11:16] for r in rows],
                         ['06:30', '06:45', '07:00', '07:15', '07:30'])
        self.assertEqual([r['checks']['within_bounds'] for r in rows],
                         [False, True, True, True, True])
        self.assertEqual(value['issue_time'], '2026-08-24T06:00:00+08:00')
        self.assertEqual(value['data_as_of'], '2026-08-24T05:50:00+08:00')

    def test_gains_costs_and_deadline_checks_match_independent_arithmetic(self):
        # Baseline T50 is 1118/60 minutes; candidate T50 seconds are below.
        rows = self.replay()['candidates']
        seconds = [839, 931, 1118, 1195, 1210]
        gains = [4.65, 3.1166666666666667, 0, -1.2833333333333334, -1.5333333333333334]
        costs = [15, 7.5, 0, 7.5, 15]
        nets = [-10.35, -4.383333333333333, 0, -8.783333333333333, -16.533333333333335]
        for row, seconds50, gain, cost, net in zip(rows, seconds, gains, costs, nets):
            self.assertAlmostEqual(row['prediction']['Tq50'], seconds50 / 60)
            self.assertAlmostEqual(row['predicted_roadgain'], gain)
            self.assertAlmostEqual(row['shift_cost'], cost)
            self.assertAlmostEqual(row['predicted_netgain'], net)
            self.assertFalse(row['checks']['gain_meets_minimum'])
        # q90 finish times are ~06:43:47,07:01:56,07:19:25,07:35:38,07:51:05.
        self.assertEqual([r['checks']['q90_meets_gantry_deadline'] for r in rows],
                         [True, True, True, False, False])

    def test_numeric_conditions_never_promote_an_unqualified_policy(self):
        # Catches policy promotion, nominal selection, or unknown risk turned to zero.
        for scenario in SCENARIOS:
            with self.subTest(scenario=scenario):
                value = self.replay(scenario)
                self.assertEqual(value['summary'], {
                    'candidate_count': 5, 'matching_numeric_conditions': 0,
                    'formal_recommendations': 0, 'complete_lateness_risk_delta': None,
                })
                self.assertFalse(value['qualification']['policy_qualified'])
                self.assertEqual(value['qualification']['forecast_display_method'], 'empirical')
                self.assertEqual(value['decision']['status'], 'strategy_unqualified')
                self.assertIsNone(value['decision']['selected_shift_minutes'])
                for row in value['candidates']:
                    self.assertFalse(row['checks']['matches_numeric_conditions'])
                    self.assertFalse(row['checks']['policy_qualified'])
                    self.assertTrue(row['checks']['not_a_recommendation'])
                    self.assertIsNone(row['checks']['complete_lateness_risk_delta'])

    def test_undisclosed_record_contains_no_later_cohort_outcomes(self):
        # Catches outcomes leaking before the separate disclosure action.
        value = self.replay()
        self.assertEqual(value['disclosure']['state'], 'undisclosed')
        for row in value['candidates']:
            self.assertIsNone(row['observed'])
            self.assertEqual(row['outcome_reason_codes'], [])
        self.assertNotIn('ontime_share', json.dumps(value))

    def test_disclosed_record_preserves_partial_measurements_and_real_zero(self):
        # Success counts independently recover the published shares; zero is known.
        value = self.replay('disclosed')
        self.assertEqual(value['disclosure']['state'], 'disclosed')
        medians = [14.55, 16.683333333333334, 20.533333333333335,
                   22.366666666666667, 22.566666666666666]
        successes = [268, 265, 258, 110, 0]
        trips = [269, 268, 267, 220, 186]
        for row, median, success, count in zip(value['candidates'], medians, successes, trips):
            observed = row['observed']
            self.assertAlmostEqual(observed['Tq50'], median)
            self.assertEqual(observed['trips'], count)
            self.assertAlmostEqual(observed['ontime_share'], success / count)
            for key in ('Tq90', 'Aq50', 'Aq90'):
                self.assertIsNone(observed[key])
            self.assertIsNone(row['calibration_support']['days'])
            self.assertIsNone(row['calibration_support']['trips'])
        self.assertAlmostEqual(value['candidates'][2]['observed']['Tq50'] -
                               value['candidates'][1]['observed']['Tq50'], 3.85)

    def test_changed_deadline_keeps_forecasts_but_invalidates_d45_shares(self):
        # Catches using the old D45 probability for an unsupported 07:31 deadline.
        initial = self.replay()
        disclosed = self.replay('disclosed')
        changed = self.replay('changed-deadline')
        self.assertEqual(changed['inputs']['deadline'], '07:31')
        self.assertEqual(changed['disclosure']['state'], 'disclosed')
        for before, after, hidden in zip(disclosed['candidates'], changed['candidates'], initial['candidates']):
            self.assertEqual(before['prediction'], after['prediction'])
            self.assertEqual(before['prediction'], hidden['prediction'])
            self.assertEqual(before['predicted_roadgain'], after['predicted_roadgain'])
            self.assertEqual(before['observed']['Tq50'], after['observed']['Tq50'])
            self.assertEqual(before['observed']['trips'], after['observed']['trips'])
            self.assertIsNone(after['observed']['ontime_share'])

    def test_missing_prediction_stays_unknown_instead_of_becoming_zero(self):
        # Real adapter + a copied public fixture with one missing prediction.
        # Catches coercing either the candidate or nominal null into a numeric gain.
        self.assertTrue((ROOT / SCRIPT).is_file(), 'offline replay command is absent')
        source = (ROOT / ADAPTER).read_text()
        for shift in (-15, 0):
            with self.subTest(shift=shift), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / SCRIPT).parent.mkdir(parents=True)
                (root / ADAPTER).parent.mkdir(parents=True)
                shutil.copyfile(ROOT / SCRIPT, root / SCRIPT)
                changed, count = re.subn(
                    rf'("2026-08-24/405/{shift}":\{{"prediction":)\{{[^}}]*\}}',
                    r'\1null', source, count=1,
                )
                self.assertEqual(count, 1, 'public fixture row must be unique')
                (root / ADAPTER).write_text(changed)
                value = self.replay(root=root)
                affected = value['candidates'] if shift == 0 else [value['candidates'][1]]
                for row in affected:
                    self.assertIsNone(row['predicted_roadgain'])
                    self.assertIsNone(row['predicted_netgain'])
                    self.assertIsNone(row['checks']['gain_meets_minimum'])
                    self.assertFalse(row['checks']['matches_numeric_conditions'])
                missing = next(r for r in value['candidates'] if r['shift_minutes'] == shift)
                self.assertIsNone(missing['prediction'])
                self.assertIsNone(missing['checks']['q90_meets_gantry_deadline'])

    def test_records_identify_actual_public_source_and_explicit_limitations(self):
        # Catches stale provenance and loss of the interpretation boundaries.
        value = self.replay()
        self.assertEqual(value['source'], {
            'adapter_path': ADAPTER.as_posix(),
            'adapter_sha256': hashlib.sha256((ROOT / ADAPTER).read_bytes()).hexdigest(),
        })
        self.assertEqual(value['scope']['timezone'], 'Asia/Taipei')
        self.assertEqual(value['scope']['gates'],
                         ['01F0750S', '01F0880S', '01F0928S', '01F0950S', '01F0980S'])
        self.assertIn('exposed_calibration_not_independent_validation', value['limitations'])
        self.assertIn('different_cohorts_not_individual_causal_savings', value['limitations'])
        self.assertIn('quantiles_not_confidence_intervals_or_personal_guarantees', value['limitations'])
        self.assertNotIn(str(ROOT), json.dumps(value))

    def test_repeated_bytes_match_fixed_records_from_any_working_directory(self):
        # Catches timestamps, local paths, unstable ordering, or stale checked-in records.
        with tempfile.TemporaryDirectory() as directory:
            for scenario in SCENARIOS:
                with self.subTest(scenario=scenario):
                    first = invoke(scenario)
                    second = invoke(scenario, cwd=directory)
                    self.assertEqual(first.returncode, 0, first.stderr.decode())
                    self.assertEqual(second.returncode, 0, second.stderr.decode())
                    self.assertEqual(first.stdout, second.stdout)
                    fixed = ROOT / 'docs/contest/replay' / (scenario + '.json')
                    self.assertTrue(fixed.is_file(), 'fixed replay record is absent')
                    self.assertEqual(first.stdout, fixed.read_bytes())
                    self.assertTrue(first.stdout.endswith(b'\n'))

    def test_unknown_scenarios_and_extra_arguments_are_rejected_without_json(self):
        # Catches silently accepting typos or ambiguous scenario selection.
        for args in ([], ['--scenario', 'typo'], ['--scenario'],
                     ['--scenario', 'disclosed', 'extra'], ['--scenario=disclosed']):
            with self.subTest(args=args):
                result = subprocess.run(['node', str(ROOT / SCRIPT), *args],
                                        capture_output=True, timeout=10)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, b'')
                self.assertIn(b'Usage:', result.stderr)
                self.assertNotIn(str(ROOT).encode(), result.stderr)


if __name__ == '__main__':
    unittest.main()
