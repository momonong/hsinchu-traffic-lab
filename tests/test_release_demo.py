"""Public release wrapper contracts; no source request, archive, model or service."""
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
if (ROOT/'scripts/departure_advisor/release_demo.py').is_file():
 from scripts.departure_advisor import release_demo as D
else:D=None
class ReleaseDemoContracts(unittest.TestCase):
 def app(self):
  self.assertIsNotNone(D,'Read-only release demo wrapper is absent')
  return D.Application(clock=lambda:1792800000.)
 def test_status_and_empty_prospective_never_read_archive_or_source(self):
  app=self.app()
  with (patch.object(D.C.canonical,'read',side_effect=AssertionError('archive read')),
        patch.object(D.C.canonical.ReplayRepository,'load',side_effect=AssertionError('model archive')),
        patch('urllib.request.OpenerDirector.open',side_effect=AssertionError('source request'))):
   status,kind,body=app.dispatch('/api/status');value=json.loads(body)
   self.assertEqual(status,200);self.assertEqual(value['delivery_mode'],'offline_release_demo')
   self.assertEqual(value['current_observation']['status'],'stopped');self.assertEqual(len(value['replay']['available_dates']),10)
   self.assertFalse(value['qualification']['policy_qualified']);self.assertEqual(value['model_forwards'],0)
   query='/api/prospective?day=2026-10-21&nominal=06%3A30&deadline=07%3A15&cost=0.5'
   status,kind,body=app.dispatch(query);forecast=json.loads(body)
   self.assertEqual(status,200);self.assertEqual(forecast['status'],'not_published');self.assertEqual(forecast['candidates'],[])
   self.assertFalse(forecast['truth_loaded']);self.assertFalse(forecast['per_request_model_forward'])
 def test_original_assets_unchanged_and_html_has_bilingual_release_notice(self):
  app=self.app();status,kind,page=app.dispatch('/?lang=en');text=page.decode()
  self.assertEqual(status,200);self.assertIn('release-demo-banner',text)
  self.assertIn('600',text);self.assertIn('10',text);self.assertIn('已曝光',text);self.assertIn('exposed calibration',text)
  self.assertIn('DOMContentLoaded',text);self.assertIn('data-mode="replay"',text)
  for route,name in [('/app.js','app.js'),('/adapter.js','adapter.js'),('/style.css','style.css')]:
   self.assertEqual(app.dispatch(route)[2],(ROOT/'prototype/departure-connected-2026-10-22'/name).read_bytes())
 def test_exact_route_allowlist_and_bad_query(self):
  app=self.app()
  for route in ('/../../.state/tdx-credentials.json','/reports/anything','/api/outcomes','/api/options','/api/source','/app.js?token=x'):
   self.assertEqual(app.dispatch(route)[0],404,route)
  for route in ('/?lang=xx','/api/status?x=1','/api/prospective?day=today'):
   self.assertEqual(app.dispatch(route)[0],400,route)
 def test_duration_and_fixed_loopback_port_are_bounded(self):
  self.app()
  self.assertEqual(D.arguments([]).port,8767);self.assertEqual(D.arguments([]).duration_seconds,3600)
  for args in (['--duration-seconds','0'],['--duration-seconds','3601'],['--port','8766']):
   with self.assertRaises(SystemExit):D.arguments(args)
 def test_default_boot_clicks_existing_replay_control_without_fetch(self):
  self.app()
  code="let clicked=0;const document={addEventListener:(e,f)=>{if(e!=='DOMContentLoaded')throw Error('event');f();},querySelector:q=>({click:()=>clicked++})};\n"+D.BOOT+"\nif(clicked!==1)throw Error('Historical tab not selected');"
  result=subprocess.run(['node','-e',code],capture_output=True,text=True,timeout=5)
  self.assertEqual(result.returncode,0,result.stderr)
if __name__=='__main__':unittest.main()
