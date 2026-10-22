import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from curl_cffi import requests

from .config import BASE_URL, CONCURRENCY, IMPERSONATE, REQUEST_DELAY


class Fetcher:
    """Chrome-impersonating HTTP client with one session per thread, global pacing and retries."""

    def __init__(self, concurrency=CONCURRENCY, delay=REQUEST_DELAY):
        self.concurrency = concurrency
        self.delay = delay
        self._local = threading.local()
        self._lock = threading.Lock()
        self._next = 0.0

    @property
    def session(self):
        s = getattr(self._local, "session", None)
        if s is None:
            s = requests.Session(impersonate=IMPERSONATE)
            self._local.session = s
        return s

    def _pace(self):
        with self._lock:
            now = time.monotonic()
            wait = self._next - now
            self._next = max(now, self._next) + self.delay
        if wait > 0:
            time.sleep(wait)

    def get(self, url, retries=3, timeout=45):
        if url.startswith("/"):
            url = BASE_URL + url
        last = None
        for attempt in range(retries):
            self._pace()
            r = None
            try:
                r = self.session.get(url, timeout=timeout)
            except Exception as e:  # network / TLS hiccup
                last = e
            if r is not None:
                last = r
                if r.status_code == 404:
                    return r
                if r.status_code == 200 and "Just a moment..." not in r.text[:4000]:
                    return r
            if attempt < retries - 1:
                time.sleep(1.5 * (attempt + 1) + random.random())
        if isinstance(last, Exception):
            raise last
        if last is None or last.status_code != 200 or "Just a moment..." in last.text[:4000]:
            raise RuntimeError("fetch failed for %s: status %s (Cloudflare block or site error)"
                               % (url, getattr(last, "status_code", None)))
        return last

    def _safe_get(self, url):
        try:
            return self.get(url)
        except Exception as e:
            return e

    def get_many(self, urls):
        """Fetch in parallel. Returns a Response, or an Exception, per url (same order)."""
        with ThreadPoolExecutor(max_workers=self.concurrency) as ex:
            return list(ex.map(self._safe_get, urls))
