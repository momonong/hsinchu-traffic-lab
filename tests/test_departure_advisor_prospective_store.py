import copy
import json
from pathlib import Path
import tempfile
import unittest

from scripts.departure_advisor.prospective_store import Store, BudgetStop, atomic_json


class DurableStoreContracts(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(); self.root = Path(self.temporary.name)
        self.report = self.root / 'report'; self.output = self.root / 'output'; self.now = 100
        self.program = {'prospective_final_deadline_epoch': 1000, 'limits': {
            'free_disk_reserve_bytes': 0, 'RSS_bytes_total': 8 * 1024**3, 'new_disk_bytes': 10000000,
            'HTTP_requests': 3, 'HTTP_body_bytes': 3000, 'M06_HTTP_body_bytes_per_research_day': 1500}}

    def tearDown(self): self.temporary.cleanup()

    def store(self, **options):
        return Store(self.report, self.output, self.program, 'program', 'protocol', clock=lambda: self.now, **options)

    def test_reservation_survives_crash_without_budget_reset_or_retry(self):
        with self.store() as owner:
            owner.reserve('first', 'M04A', cap=1000)
        self.now += 1
        with self.store() as resumed:
            self.assertEqual(resumed.state['HTTP_requests'], 1)
            self.assertEqual(resumed.state['HTTP_body_charged_bytes'], 1000)
            self.assertEqual(resumed.state['attempts'][0]['status'], 'abandoned_reservation_retained')
            with self.assertRaisesRegex(BudgetStop, 'no retry'): resumed.reserve('first', 'M04A', cap=1000)

    def test_completed_actual_bytes_and_requests_are_cumulative(self):
        with self.store() as owner:
            attempt = owner.reserve('first', 'M04A', cap=1000)
            owner.finish(attempt, 'received_parsed', 123)
        with self.store() as owner:
            self.assertEqual(owner.state['HTTP_requests'], 1)
            self.assertEqual(owner.state['HTTP_body_received_bytes'], 123)
            self.assertEqual(owner.state['HTTP_body_charged_bytes'], 123)

    def test_duplicate_owner_refused_and_new_owner_reads_latest_state(self):
        waiting = self.store()
        with self.store() as owner:
            with self.assertRaises(BlockingIOError): waiting.__enter__()
            attempt = owner.reserve('first', 'M05A', cap=1000); owner.finish(attempt, 'missing_404', 196)
        with waiting:
            self.assertEqual(waiting.state['HTTP_requests'], 1)
            self.assertEqual(waiting.state['HTTP_body_received_bytes'], 196)

    def test_missing_state_with_evidence_refuses_reinitialization(self):
        self.output.mkdir(); (self.output / 'old.csv.gz').write_bytes(b'evidence')
        with self.assertRaisesRegex(BudgetStop, 'refuse budget reset'):
            with self.store(): pass

    def test_deadline_STOP_and_disk_scope_are_enforced(self):
        with self.store() as owner:
            self.now = 1000
            with self.assertRaisesRegex(BudgetStop, 'terminal'): owner.check()
            self.now = 100; (self.report / 'STOP').touch()
            with self.assertRaisesRegex(BudgetStop, 'STOP'): owner.check()
            (self.report / 'STOP').unlink()
            with self.assertRaisesRegex(BudgetStop, 'disk budget'): owner.check(additional_disk=10000001)
            with self.assertRaisesRegex(BudgetStop, 'session deadline'): owner.check(phase_deadline=100)

    def test_issue_intent_blocks_recomputation_after_restart(self):
        slot = {'issue_id': 'fixed-issue', 'issue_epoch': 99}
        with self.store() as owner: owner.claim_issue(slot)
        with self.store() as owner:
            self.assertEqual(owner.state['issues']['fixed-issue']['status'], 'unresolved_after_restart_no_reissue')
            with self.assertRaises(BudgetStop): owner.claim_issue(slot)

    def test_immutable_publication_refuses_overwrite(self):
        path = self.root / 'forecast.json'
        atomic_json(path, {'forecast': 1}, immutable=True)
        with self.assertRaises(FileExistsError): atomic_json(path, {'forecast': 2}, immutable=True)
        self.assertEqual(json.loads(path.read_text()), {'forecast': 1})

    def test_M06_daily_and_global_caps_apply_before_request(self):
        with self.store() as owner:
            owner.reserve('hour0', 'M06A', day='day1', cap=1000)
            with self.assertRaisesRegex(BudgetStop, 'per-day'): owner.reserve('hour1', 'M06A', day='day1', cap=1000)
            owner.reserve('other', 'M06A', day='day2', cap=1000)
            with self.assertRaisesRegex(BudgetStop, 'body reservation'): owner.reserve('third', 'M04A', cap=1001)
            self.assertEqual(owner.state['HTTP_requests'], 2)

    def test_corrupt_or_rolled_back_counters_cannot_reset_budget(self):
        with self.store() as owner:
            attempt = owner.reserve('first', 'M04A', cap=1000); owner.finish(attempt, 'received_parsed', 123)
        path = self.report / 'state.json'; value = json.loads(path.read_text()); value['HTTP_body_charged_bytes'] = 0
        path.write_text(json.dumps(value))
        with self.assertRaisesRegex(BudgetStop, 'body counters'):
            with self.store(): pass


if __name__ == '__main__': unittest.main()
