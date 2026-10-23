import copy
import unittest

import numpy as np

from scripts.departure_advisor import prospective_features as P


def fixtures(day='2026-10-21', nominal=390):
    issue = P.local_epoch(day, nominal - 45)
    result = []
    for lag in range(5, 61, 5):
        start = issue - 600 - lag * 60
        for dataset in ('M04A', 'M05A'):
            rows = [dict(gate_from=a, gate_to=b, vehicle_class='31', healthy=True,
                         measurement_available=True, reasons=[], volume=10,
                         raw_measurement=600 if dataset == 'M04A' else 80)
                    for a, b in P.MODEL_PAIRS]
            result.append(dict(sequence=len(result) + 1, dataset=dataset,
                               source_epoch=start + 300, full_body_received_epoch=start + 841,
                               parse_finished_epoch=start + 842, status='received_parsed',
                               payload_sha256=str(len(result)), parsed_sha256=str(len(result)),
                               parsed=dict(dataset=dataset, interval_start_epoch=start,
                                           interval_end_epoch=start + 300, file_errors=[], model_context=rows)))
    return result


class FeatureContracts(unittest.TestCase):
    def test_late_source_maps_exactly_and_latest_lag_stays_missing(self):
        result = P.build_live_features(fixtures(), '2026-10-21', 390)
        self.assertTrue(result['input_gate_passed'])
        self.assertEqual(result['coverage']['valid_link_slots'], 120)
        matrix = result['raw_features']
        self.assertEqual(matrix.shape, (5, 547))
        self.assertTrue(np.isnan(matrix[:, :40].reshape(5, 10, 4)[:, :, :3]).all())
        self.assertTrue((matrix[:, :40].reshape(5, 10, 4)[:, :, 3] == 1).all())
        np.testing.assert_array_equal(matrix[0, 40:44], [10, 80, 10, 0])
        self.assertFalse(result['feature_grid_shifted'])
        self.assertFalse(result['policy_qualified'])

    def test_full_receipt_or_parse_after_issue_is_excluded(self):
        for key in ('full_body_received_epoch', 'parse_finished_epoch'):
            records = fixtures()
            records[0][key] = P.local_epoch('2026-10-21', 345) + 0.001
            if key == 'full_body_received_epoch': records[0]['parse_finished_epoch'] = records[0][key]
            result = P.build_live_features(records, '2026-10-21', 390)
            self.assertFalse(result['input_gate_passed'])
            self.assertEqual(result['coverage']['valid_link_slots'], 110)

    def test_future_exact_latest_slot_is_not_shifted_or_used(self):
        records = fixtures()
        future = copy.deepcopy(records[0])
        future.update(sequence=25, source_epoch=P.local_epoch('2026-10-21', 340),
                      full_body_received_epoch=P.local_epoch('2026-10-21', 349),
                      parse_finished_epoch=P.local_epoch('2026-10-21', 349))
        future['parsed'].update(interval_start_epoch=future['source_epoch']-300,
                                interval_end_epoch=future['source_epoch'])
        result = P.build_live_features(records + [future], '2026-10-21', 390)
        self.assertEqual(result['coverage']['valid_link_slots'], 120)
        self.assertTrue(np.isnan(result['raw_features'][0, 0]))

    def test_zero_negative_duplicate_and_count_mismatch_are_missing(self):
        for mutation in ('zero', 'negative', 'duplicate', 'mismatched_count'):
            records = fixtures()
            row = records[0]['parsed']['model_context'][0]
            if mutation == 'zero': row['volume'] = 0
            if mutation == 'negative': row['raw_measurement'] = -1
            if mutation == 'duplicate': records[0]['parsed']['model_context'].append(copy.deepcopy(row))
            if mutation == 'mismatched_count': row['volume'] = 11
            result = P.build_live_features(records, '2026-10-21', 390)
            self.assertFalse(result['input_gate_passed'])
            self.assertEqual(result['coverage']['valid_link_slots'], 119)

    def test_first_bad_version_not_replaced_by_later_good_version(self):
        records = fixtures()
        corrected = copy.deepcopy(records[0]); corrected['sequence'] = 25
        records[0]['parsed']['file_errors'] = ['bad_first_version']
        result = P.build_live_features(records + [corrected], '2026-10-21', 390)
        self.assertFalse(result['input_gate_passed'])
        self.assertEqual(result['coverage']['valid_link_slots'], 110)
        self.assertIn('later_source_version_never_substituted', [r['reason'] for r in result['rejected']])

    def test_missing_cold_start_does_not_invent_zero_traffic(self):
        result = P.build_live_features([], '2026-10-21', 390)
        self.assertFalse(result['input_gate_passed'])
        self.assertEqual(result['coverage']['valid_link_slots'], 0)
        self.assertTrue(np.isnan(result['raw_features'][0, :520].reshape(130, 4)[:, :3]).all())

    def test_source_and_parse_identity_must_match(self):
        records = fixtures(); records[0]['parsed']['interval_end_epoch'] += 300
        with self.assertRaisesRegex(ValueError, 'interval mismatch'):
            P.build_live_features(records, '2026-10-21', 390)

    def test_other_day_cannot_warm_current_day(self):
        result = P.build_live_features(fixtures('2026-10-22'), '2026-10-21', 390)
        self.assertFalse(result['input_gate_passed'])
        self.assertEqual(result['coverage']['valid_link_slots'], 0)

    def test_explicit_taipei_schedule_and_arrival_deadline(self):
        slots = P.issue_slots()
        self.assertEqual(len(slots), 60)
        self.assertEqual(slots[0]['issue_epoch'], P.local_epoch('2026-10-21', 345))
        self.assertEqual(slots[0]['deadline_epoch'], P.local_epoch('2026-10-21', 435))
        self.assertEqual(slots[-1]['day'], '2026-10-27')
        self.assertEqual(len(P.source_slots('2026-10-21')), 53)
        self.assertEqual(P.source_slots('2026-10-21')[0]['due_epoch'], P.local_epoch('2026-10-21', 269))
        self.assertEqual(P.local_epoch('2026-10-22', 0) - P.local_epoch('2026-10-21', 1435), 300)

    def test_restart_never_backfills_even_exact_issue_boundary(self):
        first = P.issue_slots()[0]
        self.assertEqual(P.restart_missed_slots(first['issue_epoch'] - .001, []), [])
        self.assertEqual(len(P.restart_missed_slots(first['issue_epoch'], [])), 1)
        self.assertEqual(P.restart_missed_slots(first['issue_epoch'], [first['issue_id']]), [])

    def test_unadmitted_weekend_and_issue_fail_closed(self):
        for day, nominal in [('2026-10-24', 390), ('2026-10-21', 389)]:
            with self.assertRaises(ValueError): P.issue_identity(day, nominal)


if __name__ == '__main__': unittest.main()
