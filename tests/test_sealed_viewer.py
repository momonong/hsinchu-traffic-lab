"""Only synthetic publication artifacts; no source, weights or truth reads."""
import json
import io
from pathlib import Path
import unittest
from contextlib import redirect_stderr
from unittest.mock import patch

from tests import test_departure_advisor_prospective_server as fixtures
from scripts.departure_advisor import prospective_server as canonical
from scripts.departure_advisor import sealed_viewer as V


class ViewerContracts(unittest.TestCase):
    setUp = fixtures.ProspectiveAPIContracts.setUp
    tearDown = fixtures.ProspectiveAPIContracts.tearDown
    write_state = fixtures.ProspectiveAPIContracts.write_state
    seal = fixtures.ProspectiveAPIContracts.seal

    def viewer(self, clock=None):
        return V.Viewer(self.report, clock=clock or (lambda: self.now))

    def test_fresh_state_exact_twelve_no_other_day_fallback(self):
        viewer = self.viewer()
        self.assertEqual(viewer.day('2026-10-21')['verified_count'], 0)
        self.seal()
        value = viewer.day('2026-10-21')
        self.assertEqual(len(value['slots']), 12)
        self.assertEqual(len({s['issue_id'] for s in value['slots']}), 12)
        self.assertEqual(value['verified_count'], 1)
        self.assertEqual(value['slots'][0]['status'], 'sealed_shadow_only')
        self.assertEqual(len(value['slots'][0]['candidates']), 5)
        self.assertEqual(viewer.day('2026-10-22')['verified_count'], 0)
        self.assertEqual(viewer.day('2026-10-24')['status'], 'not_scheduled')

    def test_one_snapshot_and_clock_even_if_publication_arrives_mid_response(self):
        self.seal()
        clock_calls = []
        def clock():
            clock_calls.append(1)
            return self.now
        reader = V.read_state
        with patch.object(V, 'read_state', wraps=reader) as reads:
            original = V.connected.Application.prospective
            def concurrent(app, query):
                result = original(app, query)
                self.state['issues'] = {}; self.write_state()
                return result
            with patch.object(V.connected.Application, 'prospective', concurrent):
                value = self.viewer(clock).day('2026-10-21')
        self.assertEqual(reads.call_count, 1)
        self.assertEqual(len(clock_calls), 1)
        self.assertEqual(value['verified_count'], 1)
        self.assertEqual(len({s['as_of'] for s in value['slots']}), 1)

    def test_missing_bad_state_is_unavailable_not_healthy_empty(self):
        (self.report/'state.json').unlink()
        for payload in (None, 'broken', '[]', '{"issues":[]}'):
            if payload is not None: (self.report/'state.json').write_text(payload)
            value = self.viewer().day('2026-10-21')
            self.assertEqual(value['status'], 'unavailable')
            self.assertIsNone(value['verified_count'])
            self.assertEqual(len(value['slots']), 12)
            self.assertTrue(all(not s['candidates'] for s in value['slots']))

    def test_unknown_inflight_and_insufficient_do_not_become_missing_success(self):
        for status in ('inflight', 'unresolved_after_restart', 'unknown', 'unexpected_private_path',
                       'missed_dispatch_window', 'missed_restart_no_backfill', 'missed_model_warmload'):
            self.state['issues']['2026-10-21-N0390'] = {'status': status}; self.write_state()
            slot = self.viewer().day('2026-10-21')['slots'][0]
            self.assertEqual(slot['status'], 'unknown' if status == 'unexpected_private_path' else status)
            self.assertEqual(slot['candidates'], [])
        self.state['issues']['2026-10-21-N0390'] = {'status': 'insufficient_input'}; self.write_state()
        self.assertEqual(self.viewer().day('2026-10-21')['slots'][0]['status'], 'integrity_error')

    def test_valid_insufficient_input_receipt_keeps_coverage_without_candidates(self):
        receipt = fixtures.atomic_json(self.report/'issues/insufficient.json', {
            'issue_id':'2026-10-21-N0390', 'input_cutoff_epoch':self.I, 'finished_epoch':self.I+1,
            'input_manifest_sha256':fixtures.digest(self.report/'input-manifest.json'),
            'input_coverage':{'valid_link_slots':0,'fixed_link_slots':130,'link_gates':[]},
            'source_inputs':[]}, immutable=True)
        self.state['issues']['2026-10-21-N0390']={'status':'insufficient_input','receipt':receipt};self.write_state()
        slot=self.viewer().day('2026-10-21')['slots'][0]
        self.assertEqual(slot['status'],'insufficient_input')
        self.assertEqual(slot['input_coverage']['valid_link_slots'],0)
        self.assertEqual(slot['candidates'],[])

    def test_bad_hash_isolated_to_one_slot_no_exception_or_path_leak(self):
        self.seal(); (self.output/'forecasts/f.json').write_text('{}')
        value = self.viewer().day('2026-10-21')
        self.assertEqual(value['slots'][0]['status'], 'integrity_error')
        self.assertEqual(value['slots'][1]['status'], 'not_due')
        self.assertNotIn(str(self.root), json.dumps(value))
        self.assertEqual(value['slots'][0]['candidates'], [])

    def test_late_seal_rejected_by_unchanged_canonical(self):
        self.seal(late=True)
        self.assertEqual(self.viewer().day('2026-10-21')['slots'][0]['status'], 'integrity_error')

    def test_future_source_rejected_by_unchanged_canonical(self):
        self.seal(future_source=True)
        self.assertEqual(self.viewer().day('2026-10-21')['slots'][0]['status'], 'integrity_error')

    def test_public_projection_actual_coverage_shape_and_source_clocks(self):
        raw = {'valid_link_slots': 120, 'fixed_link_slots': 130, 'link_gates': [
            {'valid_paired_slots': 12, 'positive_slots': {'M04A': 12, 'M05A': 12},
             'latest_interval_end_age_seconds': 600, 'eligible': True,
             'reasons': [], 'pair': ['private'], 'link': '/private/file'} for _ in range(10)]}
        coverage = V.public_coverage(raw)
        self.assertEqual(coverage['valid_link_slots'], 120)
        self.assertEqual(coverage['eligible_links'], 10)
        self.assertNotIn('/private', json.dumps(coverage))
        sources = V.public_sources([{'dataset': 'M04A', 'source_epoch': self.I-600,
             'full_body_received_epoch': self.I-2, 'parse_finished_epoch': self.I-1,
             'parsed_sha256': 'not-public', 'sequence': 42, 'path': '/private'}])
        self.assertEqual(sources[0]['receipts'], 1)
        self.assertEqual(sources[0]['latest_source_time'], canonical.iso(self.I-600))
        self.assertNotIn('sha256', json.dumps(sources))
        with self.assertRaises(ValueError): V.public_coverage({'valid_link_slots': True})

    def test_no_current_status_truth_or_repository_and_public_flags(self):
        self.seal()
        with patch.object(V.connected.Application, 'current', side_effect=AssertionError('source read')), \
             patch.object(V.connected.Application, 'status', side_effect=AssertionError('status read')), \
             patch.object(canonical, 'ReplayRepository', side_effect=AssertionError('history read')):
            value = self.viewer().day('2026-10-21')
        self.assertFalse(value['truth_loaded']); self.assertFalse(value['policy_qualified'])
        self.assertIsNone(value['recommendation'])
        self.assertEqual(value['viewer_operations'], {'source_requests': 0, 'fits': 0, 'model_forwards': 0})
        text = json.dumps(value)
        for key in ('CPU_trace', 'model_diagnostics', 'parsed_sha256', 'selection_sha256', 'path'):
            self.assertNotIn('"'+key+'"', text)

    def test_routes_queries_and_origin_allowlist(self):
        for path in ('/api/status', '/api/day?day=2026-10-21&day=2026-10-22',
                     '/api/day?day=2026-10-21&cost=0', '/api/day?day=bad',
                     '/../README.md', '/%2e%2e/README.md', '/style.css?x=1'):
            self.assertIn(V.dispatch(path, self.viewer())[0], (400, 404))
        self.assertEqual(V.dispatch('/api/day?day=2026-10-21', self.viewer())[0], 200)
        self.assertEqual(V.dispatch('/?lang=en', self.viewer())[0], 200)
        self.assertEqual(V.dispatch('/?lang=fr', self.viewer())[0], 400)
        self.assertTrue(V.same_origin('127.0.0.1:8768', 'http://127.0.0.1:8768'))
        self.assertFalse(V.same_origin('evil.example', None))
        self.assertFalse(V.same_origin('127.0.0.1:8768', 'https://other.example'))

    def test_lifecycle_limits(self):
        self.assertEqual(V.arguments([]).duration_seconds, 3600)
        for value in ('0', '3601', '-1'):
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                V.arguments(['--duration-seconds', value])
        with patch.object(V, 'BoundedServer') as server, patch.object(V.time,'monotonic',side_effect=[0,2]), \
             patch.object(V.signal,'signal',return_value='old') as signals:
            V.main(['--duration-seconds','1'])
        server.assert_called_once_with(('127.0.0.1',8768),V.Handler)
        server.return_value.__enter__.return_value.handle_request.assert_not_called()
        self.assertEqual(signals.call_count,4)

    def test_http_headers_and_same_origin_without_real_socket(self):
        from email.message import Message
        handler=object.__new__(V.Handler)
        handler.headers=Message();handler.headers['Host']='127.0.0.1:8768'
        handler.path='/api/day?day=2026-10-21';handler.application=self.viewer()
        with patch.object(handler,'respond') as response: handler.do_GET()
        self.assertEqual(response.call_args.args[0],200)
        handler.headers['Origin']='https://other.example'
        with patch.object(handler,'respond') as response: handler.do_GET()
        self.assertEqual(response.call_args.args[0],403)
        handler.wfile=io.BytesIO()
        with patch.object(handler,'send_response'), patch.object(handler,'send_header') as headers, \
             patch.object(handler,'end_headers'):
            handler.respond(200,'application/json',b'{}')
        values=dict(call.args for call in headers.call_args_list)
        self.assertEqual(values['Cache-Control'],'no-store')
        self.assertEqual(values['X-Content-Type-Options'],'nosniff')
        self.assertNotIn('Access-Control-Allow-Origin',values)


if __name__ == '__main__': unittest.main()
