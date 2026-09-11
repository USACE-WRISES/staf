"""One paced, retrying HTTP session for every acquisition stage."""
from __future__ import annotations

import threading
import time
from typing import Any, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from . import config

USER_AGENT = "STAF-EASI-national-builder/0.1 (+https://github.com/USACE-WRISES/staf)"

_session: Optional[requests.Session] = None
_session_lock = threading.Lock()
_pace_lock = threading.Lock()
_last_call: dict[str, float] = {}


def session() -> requests.Session:
    global _session
    with _session_lock:
        if _session is None:
            retry = Retry(total=4, connect=4, read=3, status=4,
                          status_forcelist=(429, 500, 502, 503, 504),
                          backoff_factor=2.0, allowed_methods=("GET", "POST", "HEAD"),
                          respect_retry_after_header=True)
            adapter = HTTPAdapter(max_retries=retry, pool_maxsize=8)
            s = requests.Session()
            s.mount("https://", adapter)
            s.mount("http://", adapter)
            s.headers["User-Agent"] = USER_AGENT
            _session = s
        return _session


def pace(host: str, min_interval_s: float) -> None:
    """Sleep so consecutive calls to ``host`` are at least ``min_interval_s`` apart."""
    with _pace_lock:
        last = _last_call.get(host, 0.0)
        wait = min_interval_s - (time.monotonic() - last)
        if wait > 0:
            time.sleep(wait)
        _last_call[host] = time.monotonic()


def get_json(url: str, params: Optional[dict] = None, *, timeout: float = config.HTTP_TIMEOUT_S,
             min_interval_s: float = 0.0) -> Any:
    if min_interval_s:
        pace(url.split("/")[2], min_interval_s)
    response = session().get(url, params=params, timeout=timeout)
    response.raise_for_status()
    return response.json()


def post_json(url: str, payload: dict, *, timeout: float = config.HTTP_TIMEOUT_S) -> Any:
    response = session().post(url, json=payload, timeout=timeout)
    response.raise_for_status()
    return response.json()


def get_bytes(url: str, params: Optional[dict] = None, *,
              timeout: float = config.HTTP_TIMEOUT_S) -> bytes:
    response = session().get(url, params=params, timeout=timeout)
    response.raise_for_status()
    return response.content
