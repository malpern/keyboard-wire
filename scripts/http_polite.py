"""HTTP politeness helpers shared by ingestors.

Two concerns, two helpers:

1. `HostThrottle` — enforces a minimum interval between requests to
   the same host. Constructed once per ingest run; callers invoke
   `wait(url)` immediately before issuing the request.

2. `conditional_get` — wraps `urllib.request` with
   `If-Modified-Since` / `If-None-Match` headers, persisted between
   runs in a tiny JSON state file. Quiet days return `(304, None)`
   so we don't re-download the full RSS XML.

Both helpers are stateful but easy to test: HostThrottle's clock is
injectable, conditional_get takes the state-file path as an argument
so tests use a tmp path.
"""
import datetime
import email.utils
import json
import pathlib
import time
import urllib.parse
import urllib.request

USER_AGENT = "keyboard-wire/1.0 (+https://keyboard-newswire.com)"


# ── per-host inter-request throttle ─────────────────────────────


class HostThrottle:
    """Sleep just long enough between requests to the same hostname.

    Defaults to 1.0s between same-host requests — well under Imgur's
    documented unauthenticated limit (~1250/hr ≈ 21/min) and
    comfortable for kbd.news, postimg.cc, geekhack.org.

    Per-host: bursting across distinct hosts is fine and doesn't
    accumulate sleeps. The clock is injectable for unit tests.
    """

    def __init__(self, min_interval: float = 1.0, *,
                 clock=time.monotonic, sleeper=time.sleep):
        self.min_interval = float(min_interval)
        self._last: dict[str, float] = {}
        self._clock = clock
        self._sleeper = sleeper

    @staticmethod
    def _host(url: str) -> str:
        try:
            return (urllib.parse.urlparse(url).hostname or "").lower()
        except Exception:
            return ""

    def wait(self, url: str) -> float:
        """Block until at least `min_interval` has elapsed since the
        last request to this host. Returns the number of seconds slept
        (0 if no wait was needed). Records this request as the new
        last-fetch time for the host."""
        host = self._host(url)
        if not host:
            return 0.0
        now = self._clock()
        last = self._last.get(host)
        slept = 0.0
        if last is not None:
            elapsed = now - last
            wait_for = self.min_interval - elapsed
            if wait_for > 0:
                self._sleeper(wait_for)
                slept = wait_for
                now = self._clock()
        self._last[host] = now
        return slept


# ── conditional GET (If-Modified-Since / ETag) ─────────────────


def _load_cache(state_path: pathlib.Path, url: str) -> dict:
    """Return {'etag': ..., 'last_modified': ...} for `url` or {}."""
    if not state_path.exists():
        return {}
    try:
        data = json.loads(state_path.read_text())
    except Exception:
        return {}
    return data.get(url) or {}


def _save_cache(state_path: pathlib.Path, url: str,
                etag: str | None, last_modified: str | None) -> None:
    """Update `url`'s entry in the state file. No-op if both values
    are None (nothing worth caching)."""
    if not etag and not last_modified:
        return
    data: dict = {}
    if state_path.exists():
        try:
            data = json.loads(state_path.read_text()) or {}
        except Exception:
            data = {}
    entry = data.get(url) or {}
    if etag:
        entry["etag"] = etag
    if last_modified:
        entry["last_modified"] = last_modified
    entry["fetched_at"] = datetime.datetime.now(
        datetime.timezone.utc,
    ).isoformat()
    data[url] = entry
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def conditional_get(url: str, state_path: pathlib.Path,
                    *, timeout: float = 20,
                    user_agent: str = USER_AGENT,
                    opener=None) -> tuple[int, bytes | None]:
    """Issue an HTTP GET with `If-Modified-Since` / `If-None-Match`
    headers populated from `state_path`. Returns `(status, body)`:

      - `(200, <bytes>)`  — fresh content, body returned. State file
                            updated with the new ETag / Last-Modified.
      - `(304, None)`     — server says cached copy still valid.
      - `(<other>, None)` — non-2xx/304 failure. State not updated.

    The state file is a single JSON dict keyed by URL. One state file
    can back many URLs.

    `opener` injects a custom `urllib.request` opener for testing.
    """
    cache = _load_cache(state_path, url)
    req = urllib.request.Request(url)
    req.add_header("User-Agent", user_agent)
    if cache.get("etag"):
        req.add_header("If-None-Match", cache["etag"])
    if cache.get("last_modified"):
        req.add_header("If-Modified-Since", cache["last_modified"])

    do_open = (opener or urllib.request.urlopen)
    try:
        resp = do_open(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        if e.code == 304:
            return 304, None
        return e.code, None
    except Exception:
        return 0, None

    try:
        body = resp.read()
        status = getattr(resp, "status", 200)
        etag = resp.headers.get("ETag")
        last_mod = resp.headers.get("Last-Modified")
        _save_cache(state_path, url, etag, last_mod)
        return status, body
    finally:
        resp.close()


# ── small helper: format an HTTP date for tests/debug ─────────


def http_date(dt: datetime.datetime) -> str:
    """RFC 7231 date — what servers send back in Last-Modified."""
    return email.utils.format_datetime(
        dt.astimezone(datetime.timezone.utc),
    )
