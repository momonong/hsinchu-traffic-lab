"""Synthetic transport only: no credentials, network, dependencies or cache."""
import datetime as dt
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
import urllib.error

HERE = Path(__file__).resolve().parent


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, HERE/filename)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


P = module('once_probe', 'once_probe.py')
F = module('feed_tests', 'test_feed_adapter.py')


class Clock:
    def __init__(self):
        self.seconds = 0.

    def monotonic(self):
        return self.seconds

    def sleep(self, seconds):
        self.seconds += seconds

    def now(self):
        return (F.I+dt.timedelta(seconds=10+self.seconds)).isoformat()


class Response:
    def __init__(self, value, *, status=200, headers=None):
        data = value if isinstance(value, bytes) else json.dumps(value).encode()
        self.stream = io.BytesIO(data)
        self.status, self.headers = status, headers or {}

    def read(self, count):
        return self.stream.read(count)

    def close(self):
        self.stream.close()


class Opener:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def open(self, request, timeout):
        self.calls.append((request, timeout))
        value = self.responses.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value


def happy():
    static, live = F.fixture()
    return [Response({'access_token': 'FAKE_TOKEN_NEVER_PERSIST', 'token_type': 'Bearer'}),
            Response(static), Response(live)]


def fake_credentials():
    return {'client_id': 'FAKE_ID_NEVER_PERSIST', 'client_secret': 'FAKE_SECRET_NEVER_PERSIST'}


class ProbeContracts(unittest.TestCase):
    def execute(self, path, opener, clock=None):
        clock = clock or Clock()
        return P.run_once(path/'once', opener=opener, load_credentials=fake_credentials,
            now=clock.now, monotonic=clock.monotonic, sleep=clock.sleep, stage=path)

    def test_three_fixed_requests_success_and_secret_free_receipts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            responses = happy()
            responses[2].headers.update({'Date': 'Fri, 23 Oct 2026 09:00:00 GMT',
                'RateLimit-Remaining': '2', 'Authorization': 'UNSAFE_HEADER', 'Set-Cookie': 'UNSAFE_COOKIE'})
            opener = Opener(responses)
            result = self.execute(root, opener)
            self.assertEqual(result['status'], 'single_shot_complete')
            self.assertTrue(result['transport_connected'])
            self.assertTrue(result['pair_mapping_verified'])
            self.assertTrue(result['measurement_usable'])
            self.assertIsNone(result['remaining_quota'])
            self.assertEqual(len(opener.calls), 3)
            self.assertEqual([x[0].full_url for x in opener.calls], [P.AUTH_URL, *P.URLS.values()])
            self.assertEqual([x[0].get_method() for x in opener.calls], ['POST', 'GET', 'GET'])
            self.assertTrue(all(0 < x[1] <= 20 for x in opener.calls))
            starts = [dt.datetime.fromisoformat(x['request_started_at']) for x in result['requests']]
            self.assertTrue(all((b-a).total_seconds() >= 1 for a, b in zip(starts, starts[1:])))
            self.assertEqual(result['requests'][2]['headers']['RateLimit-Remaining'], '2')
            all_bytes = b''.join(p.read_bytes() for p in root.rglob('*') if p.is_file())
            for secret in (b'FAKE_TOKEN_NEVER_PERSIST', b'FAKE_ID_NEVER_PERSIST',
                           b'FAKE_SECRET_NEVER_PERSIST', b'UNSAFE_HEADER', b'UNSAFE_COOKIE'):
                self.assertNotIn(secret, all_bytes)
            self.assertNotIn('body_sha256', result['requests'][0])
            diagnostic = json.loads((root/'once/live-diagnostic.json').read_bytes())
            self.assertEqual(diagnostic['bounded_buffer_view']['record_count'], 2)
            self.assertFalse(diagnostic['normalized_snapshot']['source_connected'])
            self.assertIsNone(diagnostic['whole_route_ETA'])
            self.assertIsNone(diagnostic['recommendation'])

    def test_every_non200_redirect_quota_or_timeout_stops_without_next_get(self):
        for code in (301, 400, 401, 402, 403, 429, 500):
            with self.subTest(code=code), tempfile.TemporaryDirectory() as temporary:
                error = urllib.error.HTTPError(P.URLS['static'], code, 'PROVIDER_ERROR_SECRET',
                    {'Retry-After': '60'}, io.BytesIO(b'NEVER_READ_ERROR_BODY'))
                opener = Opener([happy()[0], error, happy()[2]])
                result = self.execute(Path(temporary), opener)
                self.assertEqual(result['status'], 'stopped_no_retry')
                self.assertEqual(len(opener.calls), 2)
                self.assertEqual(result['requests'][1]['http_status'], code)
                outputs = (Path(temporary)/'once/receipt.json').read_bytes()
                self.assertNotIn(b'PROVIDER_ERROR_SECRET', outputs)
                self.assertNotIn(b'NEVER_READ_ERROR_BODY', outputs)
        with tempfile.TemporaryDirectory() as temporary:
            opener = Opener([TimeoutError('PRIVATE_ERROR_DO_NOT_LOG')])
            result = self.execute(Path(temporary), opener)
            self.assertEqual(len(opener.calls), 1)
            self.assertNotIn('PRIVATE_ERROR', json.dumps(result))

    def test_intent_and_request_reservation_survive_failure_prevent_resend(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            opener = Opener([TimeoutError()])
            self.execute(root, opener)
            self.assertTrue((root/'once/intent.json').exists())
            self.assertTrue((root/'once/request-1-intent.json').exists())
            with self.assertRaisesRegex(P.Halt, 'intent_exists'):
                self.execute(root, Opener(happy()))
            self.assertEqual(len(opener.calls), 1)
            # Simulate a killed process with an intent but no final receipt.
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root/'once').mkdir()
            (root/'once/intent.json').write_text('{}')
            opener = Opener(happy())
            with self.assertRaises(P.Halt):
                self.execute(root, opener)
            self.assertFalse(opener.calls)

    def test_caps_deadline_invalid_json_and_scope_fail_closed(self):
        cases = [Response(b'x'*P.CAP), Response(b'{}', headers={'Content-Length': str(P.CAP)}),
                 Response(b'invalid json'), Response({'ETagPairs': [{'ETagPairID': 'OTHER'}]})]
        for response in cases:
            with tempfile.TemporaryDirectory() as temporary:
                opener = Opener([happy()[0], response, happy()[2]])
                result = self.execute(Path(temporary), opener)
                self.assertEqual(result['status'], 'stopped_no_retry')
                self.assertEqual(len(opener.calls), 2)
                self.assertLessEqual(result['data_body_bytes_received'], P.CAP)
        with tempfile.TemporaryDirectory() as temporary:
            class Late(Opener):
                def open(self, request, timeout):
                    result = super().open(request, timeout)
                    clock.seconds = 89
                    return result
            clock = Clock()
            opener = Late(happy())
            result = self.execute(Path(temporary), opener, clock)
            self.assertEqual(len(opener.calls), 1)
            self.assertEqual(result['stop_reason'], 'absolute_work_deadline')

    def test_parseable_but_truncated_content_length_is_not_full_receipt(self):
        with tempfile.TemporaryDirectory() as temporary:
            static, _ = F.fixture()
            body = json.dumps(static).encode()
            opener = Opener([happy()[0], Response(body, headers={'Content-Length': str(len(body)+5)}), happy()[2]])
            result = self.execute(Path(temporary), opener)
            self.assertEqual(len(opener.calls), 2)
            self.assertEqual(result['stop_reason'], 'content_length_mismatch_incomplete_body')
            self.assertNotIn('full_body_received_at', result['requests'][1])
            self.assertNotIn('parse_completed_at', result['requests'][1])
            self.assertFalse((Path(temporary)/'once/HsinchuCounty_static.json').exists())

    def test_http_success_not_measurement_quality_or_mapping_success(self):
        for kind in ('clock', 'zero_count', 'wrong_endpoint', 'TT_zero'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temporary:
                static, live = F.fixture()
                if kind == 'clock':
                    live['UpdateTime'] = (F.I-dt.timedelta(seconds=1)).isoformat()
                if kind == 'zero_count':
                    live['ETagPairLives'][0]['Flows'][0].update(VehicleCount=0, TravelTime=-99)
                if kind == 'wrong_endpoint':
                    static['ETagPairs'][0]['EndETagGantryID'] = 'WRONG'
                if kind == 'TT_zero':
                    live['ETagPairLives'][0]['Flows'][0]['TravelTime'] = 0
                opener = Opener([happy()[0], Response(static), Response(live)])
                result = self.execute(Path(temporary), opener)
                self.assertTrue(result['transport_connected'])
                self.assertFalse(result['measurement_usable'])
                self.assertEqual(result['pair_mapping_verified'], kind != 'wrong_endpoint')

    def test_no_redirect_handler_and_lock_or_stage_cap_prevents_network(self):
        self.assertIsNone(P.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://elsewhere'))
        import fcntl
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root/'once').mkdir()
            with (root/'once/lock').open('wb') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                opener = Opener(happy())
                with self.assertRaisesRegex(P.Halt, 'owned_lock_busy'):
                    self.execute(root, opener)
                self.assertFalse(opener.calls)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root/'existing').write_bytes(b'x'*150000)
            opener = Opener(happy())
            with self.assertRaisesRegex(P.Halt, 'stage_budget'):
                self.execute(root, opener)
            self.assertFalse(opener.calls)


if __name__ == '__main__':
    unittest.main()
