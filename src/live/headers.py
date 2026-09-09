"""Browser-style request headers for the NBA live CDN endpoints.

``nba_api`` ships a default header set whose user-agent is a 2020-era Chrome
build and which omits ``Origin``/``Referer``. The Akamai edge in front of
``cdn.nba.com`` now answers that set with ``HTTP 403 Access Denied``. The
headers below were verified from inside the deployed container: the full set
returns ``HTTP 200`` where the library defaults return ``403``, measured
interleaved against the same edge so the two differ only by headers.

The set is verified as a group. Individual members are not independently
load-bearing, so prefer passing it whole over trimming it.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Mapping

#: Verified working header set for ``cdn.nba.com`` live endpoints.
LIVE_REQUEST_HEADERS: Mapping[str, str] = MappingProxyType(
    {
        "Host": "cdn.nba.com",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Origin": "https://www.nba.com",
        "Referer": "https://www.nba.com/",
        "Connection": "keep-alive",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-site",
    }
)


def live_request_headers() -> dict[str, str]:
    """Return a fresh mutable copy so endpoint calls cannot mutate the constant."""
    return dict(LIVE_REQUEST_HEADERS)
