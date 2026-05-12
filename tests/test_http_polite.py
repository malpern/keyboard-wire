"""Unit tests for scripts/http_polite.py (Step 1c).

Run from repo root: python3 -m unittest tests.test_http_polite
"""
import io
import json
import pathlib
import sys
import tempfile
import unittest
import urllib.error

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import http_polite as hp  # noqa: E402


# ────────────────── HostThrottle ──────────────────


class FakeClock:
    def __init__(self, start=0.0):
        self.t = start

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


class FakeSleeper:
    def __init__(self, clock: FakeClock):
        self.clock = clock
        self.calls: list[float] = []

    def __call__(self, dt):
        self.calls.append(dt)
        self.clock.advance(dt)


class HostThrottleTests(unittest.TestCase):
    def _throttle(self, min_interval=1.0, start=0.0):
        clock = FakeClock(start)
        sleeper = FakeSleeper(clock)
        t = hp.HostThrottle(min_interval, clock=clock, sleeper=sleeper)
        return t, clock, sleeper

    def test_first_request_no_sleep(self):
        t, c, s = self._throttle()
        self.assertEqual(t.wait("https://imgur.com/x.jpg"), 0.0)
        self.assertEqual(s.calls, [])

    def test_second_request_same_host_sleeps(self):
        t, c, s = self._throttle(min_interval=1.0)
        t.wait("https://imgur.com/x.jpg")
        c.advance(0.3)  # 0.3s elapses naturally
        slept = t.wait("https://imgur.com/y.jpg")
        self.assertAlmostEqual(slept, 0.7, places=2)

    def test_request_after_min_interval_no_sleep(self):
        t, c, s = self._throttle(min_interval=1.0)
        t.wait("https://imgur.com/x.jpg")
        c.advance(2.0)
        slept = t.wait("https://imgur.com/y.jpg")
        self.assertEqual(slept, 0.0)

    def test_different_hosts_independent(self):
        t, c, s = self._throttle(min_interval=1.0)
        t.wait("https://imgur.com/x.jpg")
        slept = t.wait("https://postimg.cc/y.jpg")
        # Different host — no enforced wait.
        self.assertEqual(slept, 0.0)

    def test_subdomains_treated_distinctly(self):
        # `i.imgur.com` and `imgur.com` are separate per Python's hostname
        # comparison. Acceptable today — imgur uses i. subdomain for CDN.
        t, c, s = self._throttle(min_interval=1.0)
        t.wait("https://i.imgur.com/x.jpg")
        slept = t.wait("https://imgur.com/y.jpg")
        self.assertEqual(slept, 0.0)

    def test_empty_url_no_op(self):
        t, c, s = self._throttle()
        self.assertEqual(t.wait(""), 0.0)
        self.assertEqual(t.wait(None), 0.0)  # type: ignore[arg-type]

    def test_throttle_state_per_host(self):
        # Three hosts. Second call per host should sleep, but cross-
        # host bursts should not accumulate.
        t, c, s = self._throttle(min_interval=1.0)
        t.wait("https://a/")
        t.wait("https://b/")  # different host — no sleep
        t.wait("https://c/")  # different host — no sleep
        self.assertEqual(s.calls, [])
        slept = t.wait("https://a/2")
        self.assertGreater(slept, 0)


# ────────────────── conditional_get ──────────────────


class FakeHTTPResponse:
    def __init__(self, status=200, body=b"hi", headers=None):
        self.status = status
        self._body = body
        self.headers = headers or {}

    def read(self):
        return self._body

    def close(self):
        pass


def _fake_opener(response_or_exc):
    """Return a callable that yields the given response or raises."""
    def _open(req, timeout=None):
        if isinstance(response_or_exc, Exception):
            raise response_or_exc
        # Capture the headers the caller set on the Request for assertion.
        _open.last_request = req
        return response_or_exc
    _open.last_request = None
    return _open


class ConditionalGetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.state = self.tmp / "cache.json"
        self.url = "https://geekhack.org/feed.xml"

    def test_200_caches_etag_and_last_modified(self):
        opener = _fake_opener(FakeHTTPResponse(
            status=200, body=b"<rss/>",
            headers={"ETag": '"abc"',
                     "Last-Modified": "Tue, 12 May 2026 07:48:16 GMT"},
        ))
        status, body = hp.conditional_get(
            self.url, self.state, opener=opener,
        )
        self.assertEqual(status, 200)
        self.assertEqual(body, b"<rss/>")
        cached = json.loads(self.state.read_text())[self.url]
        self.assertEqual(cached["etag"], '"abc"')
        self.assertEqual(cached["last_modified"],
                         "Tue, 12 May 2026 07:48:16 GMT")
        self.assertIn("fetched_at", cached)

    def test_second_call_sends_if_none_match_header(self):
        # First call seeds the cache.
        opener1 = _fake_opener(FakeHTTPResponse(
            status=200, body=b"x",
            headers={"ETag": '"abc"'},
        ))
        hp.conditional_get(self.url, self.state, opener=opener1)
        # Second call: state file has etag, so request should carry it.
        opener2 = _fake_opener(FakeHTTPResponse(status=200, body=b"y",
                                                headers={"ETag": '"def"'}))
        hp.conditional_get(self.url, self.state, opener=opener2)
        self.assertEqual(
            opener2.last_request.get_header("If-none-match"), '"abc"',
        )

    def test_second_call_sends_if_modified_since_header(self):
        opener1 = _fake_opener(FakeHTTPResponse(
            status=200, body=b"x",
            headers={"Last-Modified": "Mon, 11 May 2026 00:00:00 GMT"},
        ))
        hp.conditional_get(self.url, self.state, opener=opener1)
        opener2 = _fake_opener(FakeHTTPResponse(status=200, body=b"y"))
        hp.conditional_get(self.url, self.state, opener=opener2)
        self.assertEqual(
            opener2.last_request.get_header("If-modified-since"),
            "Mon, 11 May 2026 00:00:00 GMT",
        )

    def test_304_returns_none_body(self):
        # 304 surfaces as HTTPError in urllib; conditional_get translates.
        err = urllib.error.HTTPError(
            self.url, 304, "Not Modified",
            None, io.BytesIO(b""),  # type: ignore[arg-type]
        )
        opener = _fake_opener(err)
        status, body = hp.conditional_get(
            self.url, self.state, opener=opener,
        )
        self.assertEqual(status, 304)
        self.assertIsNone(body)

    def test_no_cache_yet_no_conditional_headers(self):
        opener = _fake_opener(FakeHTTPResponse(
            status=200, body=b"x", headers={},
        ))
        hp.conditional_get(self.url, self.state, opener=opener)
        self.assertIsNone(
            opener.last_request.get_header("If-none-match"),
        )
        self.assertIsNone(
            opener.last_request.get_header("If-modified-since"),
        )

    def test_user_agent_sent(self):
        opener = _fake_opener(FakeHTTPResponse(status=200, body=b"x"))
        hp.conditional_get(self.url, self.state, opener=opener,
                           user_agent="custom-ua/1.0")
        self.assertEqual(
            opener.last_request.get_header("User-agent"),
            "custom-ua/1.0",
        )

    def test_unrelated_http_error_returns_status_code(self):
        err = urllib.error.HTTPError(
            self.url, 503, "Service Unavailable",
            None, io.BytesIO(b""),  # type: ignore[arg-type]
        )
        opener = _fake_opener(err)
        status, body = hp.conditional_get(
            self.url, self.state, opener=opener,
        )
        self.assertEqual(status, 503)
        self.assertIsNone(body)

    def test_state_file_persists_across_urls(self):
        # Two URLs share the same state file.
        url2 = "https://kbd.news/rss.xml"
        opener_a = _fake_opener(FakeHTTPResponse(
            status=200, body=b"a", headers={"ETag": '"AAA"'},
        ))
        opener_b = _fake_opener(FakeHTTPResponse(
            status=200, body=b"b", headers={"ETag": '"BBB"'},
        ))
        hp.conditional_get(self.url, self.state, opener=opener_a)
        hp.conditional_get(url2, self.state, opener=opener_b)
        data = json.loads(self.state.read_text())
        self.assertEqual(data[self.url]["etag"], '"AAA"')
        self.assertEqual(data[url2]["etag"], '"BBB"')

    def test_response_without_cache_headers_no_state_change(self):
        opener = _fake_opener(FakeHTTPResponse(
            status=200, body=b"x", headers={},  # no ETag, no Last-Mod
        ))
        status, body = hp.conditional_get(
            self.url, self.state, opener=opener,
        )
        self.assertEqual(status, 200)
        self.assertFalse(self.state.exists())


if __name__ == "__main__":
    unittest.main()
