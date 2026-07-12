"""Small direct-connection HTTP client with bounded retries."""

import threading
import time
import warnings

import requests
from urllib3.exceptions import InsecureRequestWarning


class HttpRequestError(RuntimeError):
    pass


class DirectHttpClient:
    def __init__(self, retries: int = 3, backoff_seconds: float = 1.0):
        self.retries = retries
        self.backoff_seconds = backoff_seconds
        self._local = threading.local()

    def _session(self) -> requests.Session:
        session = getattr(self._local, "session", None)
        if session is None:
            session = requests.Session()
            session.trust_env = False
            session.proxies = {"http": None, "https": None}
            session.headers.update({"User-Agent": "Mozilla/5.0"})
            self._local.session = session
        return session

    def get(self, url: str, *, verify: bool = True, timeout: int = 30, **kwargs):
        last_error: Exception | None = None
        for attempt in range(self.retries):
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", InsecureRequestWarning)
                    response = self._session().get(
                        url, verify=verify, timeout=timeout, **kwargs
                    )
                response.raise_for_status()
                return response
            except (requests.RequestException, ValueError) as exc:
                last_error = exc
                if attempt < self.retries - 1:
                    time.sleep(self.backoff_seconds * (2**attempt))
        raise HttpRequestError(
            f"GET {url} failed after {self.retries} attempts: {last_error}"
        )

    def get_json(self, url: str, **kwargs):
        response = self.get(url, **kwargs)
        try:
            return response.json()
        except ValueError as exc:
            raise HttpRequestError(f"GET {url} returned invalid JSON") from exc

    def post_json(self, url: str, payload: dict, *, timeout: int = 15) -> dict:
        """POST once so a timeout cannot produce duplicate notifications."""
        try:
            response = self._session().post(url, json=payload, timeout=timeout)
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError) as exc:
            raise HttpRequestError(f"POST {url} failed: {exc}") from exc
