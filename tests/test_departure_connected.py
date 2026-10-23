"""Synthetic UI/API contracts: no sockets, raw sources, models or truth."""
import copy
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from scripts.departure_advisor import connected_server as C
from tests import test_departure_advisor_prospective_server as fixtures


class ConnectedContracts(unittest.TestCase):
    setUp = fixtures.ProspectiveAPIContracts.setUp
    tearDown = fixtures.ProspectiveAPIContracts.tearDown
    write_state = fixtures.ProspectiveAPIContracts.write_state
    query = fixtures.ProspectiveAPIContracts.query
    seal = fixtures.ProspectiveAPIContracts.seal

    def application(self):
        return C.Application(self.report, clock=lambda: self.now)

    def source(self, dataset, **changes):
        n = 5 if dataset == 'M03A' else 10
        value = dict(dataset=dataset, interval_end_epoch=self.I,
            interval_start_epoch=self.I-300, context_available_rows=n,
            context_expected_rows=n, row_count=100, file_errors=[],
            counts={'direction_mismatch': 20, 'zero_volume_rows': 0})
        value.update(changes)
        path = self.report/(dataset+'.json')
        path.write_text(json.dumps(value))
        self.state['attempts'] = [a for a in self.state['attempts'] if a['dataset'] != dataset]
        self.state['attempts'].append(dict(dataset=dataset, status='received_parsed',
            source_epoch=self.I, available_epoch=self.I+1,
            parsed=dict(path=str(path), sha256=C.canonical.digest(path))))
        return self.state['attempts'][-1]

    def test_canonical_method_exactly_inherited_no_repository_load(self):
        self.assertIs(C.Application.prospective, C.canonical.Application.prospective)
        self.seal()
        with patch.object(C.canonical.ReplayRepository, 'load', side_effect=AssertionError('model/truth load')):
            value = self.application().route('/api/prospective', self.query())
        self.assertEqual(len(value['candidates']), 5)
        self.assertFalse(value['truth_loaded']); self.assertFalse(value['per_request_model_forward'])
        self.assertNotIn('CPU_trace', value); self.assertNotIn('source_inputs', value)
        self.assertNotIn('/home/', json.dumps(value)); self.assertFalse(value['policy_qualified'])

    def test_freshness_boundary_and_content_row_counter_gates(self):
        for dataset in C.canonical.DATASETS:
            self.source(dataset)
        self.now = self.I+900
        self.assertEqual(self.application().current(self.state, self.now)['status'], 'available')
        self.now += 1
        self.assertEqual(self.application().current(self.state, self.now)['status'], 'stale')
        self.now = self.I+10
        for changes in ({'file_errors':['bad']}, {'context_available_rows':0},
                        {'counts':{'invalid':-1}}, {'counts':{'invalid':True}},
                        {'context_available_rows':11}, {'row_count':-1}):
            for dataset in C.canonical.DATASETS:
                self.source(dataset, **changes)
            value = self.application().current(self.state, self.now)
            self.assertEqual(value['status'], 'data_insufficient')
            self.assertTrue(all(v['status']=='parsed_invalid' for v in value['sources']))

    def test_missing_partial_stopped_and_STOP_never_available(self):
        self.assertEqual(self.application().current(None,self.now)['status'],'not_ready')
        self.source('M04A',context_available_rows=9)
        self.assertEqual(self.application().current(self.state,self.now)['status'],'data_insufficient')
        (self.report/'STOP').touch()
        self.assertEqual(self.application().current(self.state,self.now)['status'],'stopped')

    def test_hash_interval_clock_and_future_publication_rejected(self):
        a = self.source('M03A'); a['parsed']['sha256']='0'*64
        with self.assertRaises(C.ApiUnavailable): self.application().current(self.state,self.now)
        a=self.source('M03A');a['available_epoch']=self.now+1
        with self.assertRaises(C.ApiUnavailable): self.application().current(self.state,self.now)
        a=self.source('M03A',interval_start_epoch=self.I-301)
        with self.assertRaises(C.ApiUnavailable): self.application().current(self.state,self.now)
        self.seal(); key='2026-10-21-N0390';self.state['issues'][key]['published_epoch']=self.now+1;self.write_state()
        with self.assertRaises(C.ApiUnavailable): self.application().route('/api/prospective',self.query())

    def test_null_predictions_preserve_unknown_gain(self):
        self.seal()
        value=self.application().route('/api/prospective',self.query())
        # Projecting is read-only; it never converts missing values or loads truth.
        value['candidates'][1].update(prediction=None,predicted_roadgain=None,predicted_netgain=None)
        projected=C.public_issue(value)
        self.assertIsNone(projected['candidates'][1]['predicted_roadgain'])

    def test_export_exact_unique_membership_and_fixed_clocks(self):
        ids=C.expected_ids();state=dict(self.state,issues={key:{'day':'2026-10-21',
            'forecast':{'sha256':'f'},'seal':{'sha256':'s'},'publication':{'sha256':'p'}} for key in ids})
        app=C.Application(self.report,clock=lambda:self.now,frozen_state=state)
        def value(_,query):
            n=C.canonical.minutes(query['nominal'][0])
            return dict(issue_id=C.canonical.issue_identity('2026-10-21',n),
                status='sealed_shadow_only',as_of=C.canonical.iso(self.now),candidates=[])
        with patch.object(C.Application,'prospective',value):
            payload,refs=C.export_snapshot(app)
            self.assertEqual(len(refs),12)
            self.assertEqual(payload['exported_at'],payload['issues'][0]['as_of'])
            self.assertEqual(payload['status']['current_observation']['status'],'stopped')
            del state['issues'][ids[0]]
            with self.assertRaisesRegex(C.ApiUnavailable,'unique_twelve'):C.export_snapshot(app)
            state['issues']['duplicate_wrong_id']={'day':'2026-10-21'}
            with self.assertRaisesRegex(C.ApiUnavailable,'unique_twelve'):C.export_snapshot(app)

    def test_handler_fixed_assets_and_integrity_error_503(self):
        def request(path,app):
            handler=object.__new__(C.Handler);handler.path=path;handler.application=app
            handler.wfile=io.BytesIO();handler.send_response=lambda status:setattr(handler,'status',status)
            handler.send_header=lambda *args:None;handler.end_headers=lambda:None
            handler.send_error=lambda status,*args:setattr(handler,'status',status)
            handler.do_GET();return handler
        self.assertEqual(request('/../README.md',self.application()).status,404)
        self.assertEqual(request('/api/outcomes',self.application()).status,404)
        self.assertEqual(request('/api/status?file=private',self.application()).status,400)
        with patch.object(C.Application,'status',side_effect=C.ApiUnavailable('bad hash')):
            self.assertEqual(request('/api/status',self.application()).status,503)


if __name__=='__main__':unittest.main()
