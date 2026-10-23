"""Small synthetic contracts. No HTTP, package dependency, models or cache."""
import copy
import datetime as dt
import importlib.util
import json
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('xinglong_feed', Path(__file__).with_name('feed_adapter.py'))
F = importlib.util.module_from_spec(spec)
spec.loader.exec_module(F)
I = dt.datetime(2026, 10, 23, 17, tzinfo=dt.timezone(dt.timedelta(hours=8)))


def fixture(seconds=0, tt=120):
    end = I+dt.timedelta(seconds=seconds)
    static = {'AuthorityCode':'HSQ','LinkVersion':'v1','ETagPairs':[]}
    live = {'AuthorityCode':'HSQ','LinkVersion':'v1','UpdateTime':end.isoformat(),
            'SrcUpdateTime':end.isoformat(),'ETagPairLives':[]}
    for pair, endpoints in F.PAIRS.items():
        static['ETagPairs'].append(dict(ETagPairID=pair,StartETagGantryID=endpoints[0],
            EndETagGantryID=endpoints[1],Description=pair,LinkIDs=[{'LinkID':pair}]))
        live['ETagPairLives'].append(dict(ETagPairID=pair,StartETagStatus=0,EndETagStatus=0,
            StartTime=(end-dt.timedelta(minutes=5)).isoformat(),EndTime=end.isoformat(),
            DataCollectTime=end.isoformat(),Flows=[dict(VehicleType=3,VehicleCount=2,
                TravelTime=tt,StandardDeviation=0,SpaceMeanSpeed=30)]))
    return static, live


def normalized(seconds=0, tt=120, seen_offset=10, receipt_offset=1):
    end = I+dt.timedelta(seconds=seconds)
    receipt={'full_body_received_at':(end+dt.timedelta(seconds=receipt_offset)).isoformat(),
             'parse_completed_at':(end+dt.timedelta(seconds=receipt_offset+1)).isoformat()}
    return F.normalize(*fixture(seconds,tt),as_of=end+dt.timedelta(seconds=seen_offset),receipt=receipt)


class FeedContracts(unittest.TestCase):
    def test_quality_missing_negative_nonfinite_and_legitimate_SD_zero(self):
        frame=normalized();self.assertTrue(all(s['usable_asof'] for s in frame['segments']))
        self.assertEqual(frame['segments'][0]['measurement']['standard_deviation_seconds'],0)
        for field,value in [('VehicleCount',0),('VehicleCount',-1),('VehicleCount',True),
                            ('TravelTime',0),('TravelTime',-99),('TravelTime',float('nan')),
                            ('StandardDeviation',float('inf')),('SpaceMeanSpeed',-99)]:
            static,live=fixture();live['ETagPairLives'][0]['Flows'][0][field]=value
            v=F.normalize(static,live,as_of=I+dt.timedelta(seconds=10))['segments'][0]
            self.assertFalse(v['usable_asof']);self.assertIsNone(v['measurement']['mean_travel_time_seconds'])
            json.dumps(F.safe(v),allow_nan=False)

    def test_no_observation_unknown_and_speed_zero_explicitly_retained(self):
        for kind in ['absent','zero_count']:
            static,live=fixture()
            if kind=='absent':live['ETagPairLives'][0]['Flows']=[]
            else:live['ETagPairLives'][0]['Flows'][0].update(VehicleCount=0,TravelTime=-99,StandardDeviation=-99,SpaceMeanSpeed=-99)
            v=F.normalize(static,live,as_of=I+dt.timedelta(seconds=10))['segments'][0]
            self.assertEqual(v['quality']['status'],'unknown',kind);self.assertFalse(v['usable_asof'])
            self.assertIsNone(v['measurement']['mean_travel_time_seconds'])
        static,live=fixture();live['ETagPairLives'][0]['Flows'][0]['SpaceMeanSpeed']=0
        v=F.normalize(static,live,as_of=I+dt.timedelta(seconds=10))['segments'][0]
        self.assertTrue(v['zero_speed_reported']);self.assertEqual(v['measurement']['space_mean_speed_kmh'],0)

    def test_receipts_not_guessed_order_and_future_window(self):
        static,live=fixture();now=I+dt.timedelta(seconds=10)
        v=F.normalize(static,live,as_of=now)['segments'][0]
        self.assertEqual(v['quality']['status'],'valid');self.assertIsNone(v['availability']['available_at_asof'])
        self.assertEqual(v['availability']['status'],'unknown_receipt')
        for full,parsed in [(I-dt.timedelta(seconds=1),I),(I+dt.timedelta(seconds=2),I+dt.timedelta(seconds=1))]:
            v=F.normalize(static,live,as_of=now,receipt={'full_body_received_at':full,'parse_completed_at':parsed})['segments'][0]
            self.assertEqual(v['receipt']['status'],'invalid');self.assertFalse(v['usable_asof'])
        v=F.normalize(static,live,as_of=I+dt.timedelta(seconds=1),receipt={
            'full_body_received_at':I+dt.timedelta(seconds=2),'parse_completed_at':I+dt.timedelta(seconds=3)})['segments'][0]
        self.assertEqual(v['availability']['status'],'not_yet_available');self.assertIsNone(v['availability']['available_at_asof'])
        v=F.normalize(static,live,as_of=I-dt.timedelta(seconds=1))['segments'][0]
        self.assertEqual(v['freshness']['status'],'future');self.assertFalse(v['usable_asof'])
        with self.assertRaises(ValueError):F.normalize(static,live,as_of=I.replace(tzinfo=None))

    def test_identity_endpoint_versions_status_and_duplicate_rejection(self):
        for kind in ['static_duplicate','live_duplicate','flow_duplicate','endpoint','version','authority','status']:
            static,live=fixture()
            if kind=='static_duplicate':static['ETagPairs'].append(copy.deepcopy(static['ETagPairs'][0]))
            if kind=='live_duplicate':live['ETagPairLives'].append(copy.deepcopy(live['ETagPairLives'][0]))
            if kind=='flow_duplicate':live['ETagPairLives'][0]['Flows']*=2
            if kind=='endpoint':static['ETagPairs'][0]['EndETagGantryID']='wrong'
            if kind=='version':live['LinkVersion']='different'
            if kind=='authority':live['AuthorityCode']='HSZ'
            if kind=='status':live['ETagPairLives'][0]['EndETagStatus']=1
            v=F.normalize(static,live,as_of=I+dt.timedelta(seconds=10))['segments'][0]
            self.assertEqual(v['quality']['status'],'invalid',kind);self.assertFalse(v['usable_asof'])

    def test_original_metadata_clock_anomaly_not_repaired(self):
        static,live=fixture();raw=(I-dt.timedelta(seconds=30)).isoformat();live['UpdateTime']=raw
        v=F.normalize(static,live,as_of=I+dt.timedelta(seconds=10),receipt={
            'full_body_received_at':I+dt.timedelta(seconds=1),'parse_completed_at':I+dt.timedelta(seconds=2)})['segments'][0]
        self.assertEqual(v['source_metadata']['UpdateTime'],raw)
        self.assertEqual(v['quality']['status'],'valid');self.assertEqual(v['clock_integrity']['status'],'metadata_anomaly')
        self.assertFalse(v['usable_asof']);self.assertFalse(v['issue_vintage_verified'])
        static,live=fixture();live['SrcUpdateTime']=(I+dt.timedelta(seconds=20)).isoformat()
        v=F.normalize(static,live,as_of=I+dt.timedelta(seconds=10),receipt={
            'full_body_received_at':I+dt.timedelta(seconds=1),'parse_completed_at':I+dt.timedelta(seconds=2)})['segments'][0]
        self.assertIn('SrcUpdateTime_after_as_of',v['clock_integrity']['anomalies'])
        self.assertIn('SrcUpdateTime_after_full_body_receipt',v['clock_integrity']['anomalies'])
        self.assertFalse(v['usable_asof'])

    def test_duplicate_revision_and_old_asof_not_rewritten(self):
        b=F.RollingBuffer();one=normalized();b.ingest(one)
        before=copy.deepcopy(b.view(as_of=I+dt.timedelta(seconds=15)))
        duplicate=copy.deepcopy(one);duplicate['as_of']=(I+dt.timedelta(seconds=11)).isoformat()
        self.assertTrue(all(x['status']=='duplicate_ignored' for x in b.ingest(duplicate)))
        two=normalized(tt=130,seen_offset=25,receipt_offset=20)
        self.assertTrue(all(x['status']=='revision_recorded' for x in b.ingest(two)))
        old=b.view(as_of=I+dt.timedelta(seconds=15));self.assertEqual(old['records'],before['records'])
        self.assertTrue(all(x['segment']['measurement']['mean_travel_time_seconds']==130 for x in b.view(as_of=I+dt.timedelta(seconds=30))['records']))
        self.assertTrue(all(x['segment']['freshness']['status']=='stale' for x in b.view(as_of=I+dt.timedelta(seconds=901))['records']))
        with self.assertRaises(ValueError):b.ingest(one)

    def test_eviction_capacity_bytes_and_no_new_version_for_past(self):
        b=F.RollingBuffer(max_records=2);b.ingest(normalized());b.ingest(normalized(tt=130,seen_offset=25,receipt_offset=20))
        old=b.view(as_of=I+dt.timedelta(seconds=15));self.assertEqual(old['records'],[]);self.assertTrue(old['history_may_be_incomplete'])
        self.assertTrue(b.view(as_of=I+dt.timedelta(seconds=100))['history_may_be_incomplete'])
        for n in range(1,55):b.ingest(normalized(seconds=n*300))
        windows={x['segment']['window']['end_epoch'] for x in b.records}
        self.assertLessEqual(len(windows),48);self.assertLessEqual(len(b.records),2);self.assertLessEqual(b.bytes_used,b.max_bytes)
        c=F.RollingBuffer(max_records=96,max_bytes=7000)
        for n in range(40):c.ingest(normalized(tt=120+n,seen_offset=10+n*3,receipt_offset=1+n*3))
        self.assertLessEqual(c.bytes_used,7000);self.assertLessEqual(len(c.records),96)
        d=F.RollingBuffer();unknown=F.normalize(*fixture(),as_of=I+dt.timedelta(seconds=10));d.ingest(unknown)
        self.assertEqual(d.records,[])


if __name__=='__main__':unittest.main()
