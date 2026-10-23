"""New-stage read-only API contracts. No sockets, source HTTP, fits or model calls."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from scripts.departure_advisor import prospective_server as S
from scripts.departure_advisor.prospective_store import atomic_json, digest


class ProspectiveAPIContracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.report = self.root / 'reports/prospective-departure-2026-10-20'; self.report.mkdir(parents=True)
        self.output = self.root / 'outputs/prospective-departure-2026-10-20'
        for name in ('program.json', 'protocol.json', 'input-manifest.json'): (self.report/name).write_text('{}')
        self.I = S.local_epoch('2026-10-21', 345); self.now = self.I+10
        self.repo = Mock(); self.repo.available_dates.return_value = ['2026-09-01']
        self.repo.qualification.return_value = {'forecast_display_method':'empirical','policy_qualified':False}
        self.app = S.Application(self.report, self.repo, clock=lambda:self.now)
        self.patches = [patch.object(S,'ROOT',self.root),patch.object(S,'alive',return_value=True)]
        for p in self.patches:p.start()
        self.state = {'attempts':[], 'issues':{}, 'HTTP_requests':0,
                      'process':{'status':'running','mode':'morning','process':{'pid':123}}}
        self.write_state()

    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.temp.cleanup()

    def write_state(self): atomic_json(self.report/'state.json',self.state)
    def query(self, **changes):
        q={'day':['2026-10-21'],'nominal':['06:30'],'deadline':['07:15'],'cost':['0.5']};q.update(changes);return q

    def seal(self, *, late=False, future_source=False):
        identity='2026-10-21-N0390'
        values=[[20.,25.,25.,30.] for _ in range(5)]
        forecast={'issue_id':identity,'day':'2026-10-21','nominal':390,'issue_epoch':self.I,'input_cutoff_epoch':self.I,
                  'finished_epoch':self.I+1,'forecast_display_method':'empirical','recommendation_qualified':False,
                  'input_manifest_sha256':digest(self.report/'input-manifest.json'),
                  'source_inputs':[{'full_body_received_epoch':self.I-1,'parse_finished_epoch':self.I+1 if future_source else self.I}],
                  'methods':{'empirical':{'final':values}},'input_coverage':{'paired_rows':120}}
        f=atomic_json(self.output/'forecasts/f.json',forecast,immutable=True)
        seal={'issue_id':identity,'forecast':f,'issue_epoch':self.I,'sealed_epoch':self.I+2,
              'program_sha256':digest(self.report/'program.json'),'protocol_sha256':digest(self.report/'protocol.json')}
        s=atomic_json(self.output/'seals/s.json',seal,immutable=True)
        publication={'issue_id':identity,'forecast':f,'seal':s,'issue_epoch':self.I,
                     'seal_completed_epoch':self.I+(61 if late else 3),'earliest_candidate_epoch':self.I+900,
                     'primary_evaluation_valid':True,'program_sha256':seal['program_sha256'],'protocol_sha256':seal['protocol_sha256']}
        p=atomic_json(self.output/'publications/p.json',publication,immutable=True)
        self.state['issues'][identity]={'status':'sealed_shadow_only','forecast':f,'seal':s,'publication':p,
                                       'seal_completed_epoch':publication['seal_completed_epoch'],'published_epoch':self.I+4}
        self.write_state();return forecast

    def test_sealed_five_rows_read_only_no_truth_or_forward(self):
        self.seal()
        with patch.object(self.repo,'outcomes',side_effect=AssertionError('truth read')):
            value=self.app.prospective(self.query())
        self.assertEqual(len(value['candidates']),5);self.assertFalse(value['truth_loaded'])
        self.assertFalse(value['per_request_model_forward']);self.assertFalse(value['policy_qualified'])
        self.assertEqual(value['candidates'][0]['predicted_netgain'],-15.)
        self.assertEqual(value['candidates'][2]['prediction']['Aq90'],30.)
        self.assertNotIn('observed',json.dumps(value));self.assertNotIn('truth_days',json.dumps(value))

    def test_missing_unresolved_insufficient_and_future_are_explicit(self):
        self.now=self.I-1;self.assertEqual(self.app.prospective(self.query())['status'],'not_due')
        self.now=self.I+1;self.assertEqual(self.app.prospective(self.query())['status'],'not_published')
        identity='2026-10-21-N0390';self.state['issues'][identity]={'status':'unresolved_after_restart'};self.write_state()
        self.assertEqual(self.app.prospective(self.query())['candidates'],[])
        rec=atomic_json(self.report/'issues/i.json',{'issue_id':identity,'input_cutoff_epoch':self.I,'finished_epoch':self.I,
                    'input_manifest_sha256':digest(self.report/'input-manifest.json'),
                    'input_coverage':{'valid':0},'source_inputs':[]},immutable=True)
        self.state['issues'][identity]={'status':'insufficient_input','receipt':rec};self.write_state()
        self.assertEqual(self.app.prospective(self.query())['input_coverage'],{'valid':0})

    def test_late_seal_and_future_source_rejected(self):
        self.seal(late=True)
        with self.assertRaisesRegex(ValueError,'clock contract'):self.app.prospective(self.query())
        # Separate fixture: immutable files are not replaced.
    def test_future_source_rejected(self):
        self.seal(future_source=True)
        with self.assertRaisesRegex(ValueError,'future source'):self.app.prospective(self.query())

    def test_hash_and_index_identity_and_future_publication_rejected(self):
        self.seal(); identity='2026-10-21-N0390'
        self.state['issues'][identity]['seal_completed_epoch']=self.I+2;self.write_state()
        with self.assertRaisesRegex(S.ApiUnavailable,'index_binding'):self.app.prospective(self.query())
        self.state['issues'][identity]['seal_completed_epoch']=self.I+3
        self.state['issues'][identity]['published_epoch']=self.now+1;self.write_state()
        with self.assertRaisesRegex(S.ApiUnavailable,'after_render'):self.app.prospective(self.query())
        self.state['issues'][identity]['published_epoch']=self.I+4;self.write_state()
        (self.output/'forecasts/f.json').write_text('{}')
        with self.assertRaisesRegex(ValueError,'hash mismatch'):self.app.prospective(self.query())

    def test_historical_query_is_normalized_without_implicit_truth(self):
        q={key:[str(value)]for key,value in {'day':'2026-09-01','nominal':'06:45','deadline':'07:30','advance':30,'delay':30,'cost':.5}.items()}
        self.app.route('/api/options',q)
        self.assertEqual(self.repo.options.call_args.args[0]['advance'],30)
        self.repo.outcomes.assert_not_called()
        self.app.route('/api/outcomes',q);self.repo.outcomes.assert_called_once()

    def test_query_contract(self):
        for changes in ({'day':['2026-10-24']},{'nominal':['05:00']},{'cost':['NaN']},{'cost':['bad']},{'path':['/etc/passwd']}):
            with self.assertRaises(S.BadQuery):self.app.prospective(self.query(**changes))
        with self.assertRaises(S.BadQuery):self.app.route('/api/status',{'x':['1']})

    def source(self, *, bad_hash=False, future=False):
        source=self.I-600
        record=atomic_json(self.report/'parsed/a.json',{'dataset':'M04A','interval_end_epoch':source,'interval_start_epoch':source-300,
              'context_available_rows':10,'context_expected_rows':10,'counts':{},'timestamp_semantics':'[t-5,t)'},immutable=True)
        if bad_hash:record['sha256']='bad'
        self.state['attempts']=[{'dataset':'M04A','status':'received_parsed','sequence':1,'source_epoch':source,
                                 'available_epoch':self.now+1 if future else self.I-1,'parsed':record}];self.write_state()

    def test_current_is_same_snapshot_and_requires_health_clocks_hashes(self):
        self.source();value=self.app.status();self.assertEqual(value['current_observation']['status'],'data_insufficient')
        self.assertEqual(value['current_observation']['sources'][1]['available_rows'],10)
        self.now+=1000;self.assertNotEqual(self.app.status()['current_observation']['status'],'available')
        self.now=self.I+10;self.state['attempts'][0]['available_epoch']=self.now+1;self.write_state()
        self.assertEqual(self.app.status()['current_observation']['status'],'not_ready')
        self.state['attempts'][0]['available_epoch']=self.I-1;self.state['attempts'][0]['parsed']['sha256']='bad';self.write_state()
        self.assertEqual(self.app.status()['current_observation']['status'],'not_ready')
        (self.report/'STOP').touch();self.assertEqual(self.app.status()['current_observation']['status'],'stopped')

    def test_source_and_truth_session_are_not_conflated(self):
        self.source();self.state['process']['mode']='truth';self.write_state()
        self.assertEqual(self.app.status()['current_observation']['status'],'stopped')
        self.assertEqual(self.app.status()['source_requests'],0)


if __name__=='__main__':unittest.main()
