"""Custom access logging middleware for FastAPI/Uvicorn."""

import logging
import sys
import time
from ipaddress import IPv6Address

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger("mediahive.access")

_RESET = "\033[0m"
_STATUS_INFO = "\033[32m"  # 1xx (green)
_STATUS_OK = "\033[1;92m"  # 2xx (bright green)
_STATUS_REDIRECT = "\033[32m"  # 3xx (green)
_STATUS_CLIENT_ERR = "\033[0;31m"  # 4xx (red)
_STATUS_SERVER_ERR = "\033[1;91m"  # 5xx (bold bright red)
_METHOD_READ = "\033[0;34m"  # GET, HEAD, OPTIONS (blue)
_METHOD_WRITE = "\033[1;94m"  # POST, PUT, DELETE, PATCH (bold bright blue)
_HOST = "\033[38;5;242m"  # hostname (dark grey)
_PATH = "\033[38;5;250m"  # path (white)
_TIMING = "\033[38;5;242m"  # timing/devmode (dark grey)


def format_ipv6_network(ip: str) -> str:
    """Format IPv6 address to show only network part (first 64 bits).

    Special addresses are returned as-is for clarity:
    - ::1 (loopback)
    - :: (unspecified)
    - ::ffff:x.x.x.x (IPv4-mapped, returns just the IPv4 part)
    - fe80:: (link-local, returned as-is since interface-specific)
    """
    try:
        # Strip brackets that some proxies add around IPv6
        ip = ip.strip("[]")
        # Strip zone ID (e.g., fe80::1%eth0)
        if "%" in ip:
            ip = ip.split("%")[0]
        addr = IPv6Address(ip)

        # Special cases - return as-is or with minimal processing
        if addr.is_loopback:  # ::1
            return "::1"
        if addr.is_unspecified:  # ::
            return "::"
        if addr.ipv4_mapped:  # ::ffff:x.x.x.x
            return str(addr.ipv4_mapped)
        if addr.is_link_local:  # fe80::/10 - interface-specific, keep full
            return str(addr)

        # Regular addresses: truncate to /64 network prefix
        network_int = int(addr) >> 64
        # Format as IPv6 with trailing ::
        # Split into 4 groups of 16 bits
        groups = []
        for _ in range(4):
            groups.insert(0, format(network_int & 0xFFFF, "x"))
            network_int >>= 16
        # Compress consecutive zero groups
        result = ":".join(groups) + "::"
        # Simplify leading zeros in groups and compress, then strip trailing ::
        return str(IPv6Address(result + "0")).removesuffix("::")
    except ValueError:
        return ip


def format_client_ip(ip: str) -> str:
    """Format client IP, compressing IPv6 to network part only."""
    if not ip or ip == "-":
        return "-"
    # Strip brackets for detection (some proxies add them)
    stripped = ip.strip("[]")
    if ":" in stripped:
        return format_ipv6_network(ip)
    return ip


def status_color(status: int) -> str:
    """Return color code based on HTTP status."""
    if status < 200:
        return _STATUS_INFO
    if status < 300:
        return _STATUS_OK
    if status < 400:
        return _STATUS_REDIRECT
    if status < 500:
        return _STATUS_CLIENT_ERR
    return _STATUS_SERVER_ERR


def method_color(method: str) -> str:
    """Return color code based on HTTP method."""
    if method in ("GET", "HEAD", "OPTIONS"):
        return _METHOD_READ
    return _METHOD_WRITE


def format_access_log(
    client: str,
    status: int,
    method: str,
    host: str,
    path: str,
    duration_ms: float,
    extra: str = "",
) -> str:
    """Format access log line with colors and aligned fields."""
    # Format components with fixed widths for alignment
    ip = format_client_ip(client).ljust(19)  # IPv6 network max 19 chars
    timing = f"{duration_ms:.0f}ms"
    method_padded = method.ljust(7)  # Longest method is OPTIONS (7)

    status_str = f"{status_color(status)}{status}{_RESET}"
    timing_str = f"{_TIMING}{timing}{_RESET}"
    method_str = f"{method_color(method)}{method_padded}{_RESET}"
    host_str = f"{_HOST}{host}{_RESET}"
    path_str = f"{_PATH}{path}{_RESET}"

    # Format: "IP STATUS METHOD host path [extra] TIMING"
    extra_str = f" {_TIMING}{extra}{_RESET}" if extra else ""
    return (
        f"{ip} {status_str} {method_str} {host_str}{path_str}{extra_str} {timing_str}"
    )


class AccessLogMiddleware(BaseHTTPMiddleware):
    """Middleware that logs HTTP requests with custom format."""

    async def dispatch(self, request: Request, call_next) -> Response:
        start = time.perf_counter()
        response = await call_next(request)
        duration_ms = (time.perf_counter() - start) * 1000

        client = request.client.host if request.client else "-"
        host = request.headers.get("host", "-")
        method = request.method
        path = request.url.path
        if request.url.query:
            path = f"{path}?{request.url.query}"
        status = response.status_code

        extra = getattr(request.state, "log_extra", "")

        line = format_access_log(
            client, status, method, host, path, duration_ms, extra=extra
        )
        logger.info(line)

        return response


def configure_access_logging() -> None:
    """Configure the access logger to output to stderr."""
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    # Avoid duplicate request lines from uvicorn when custom middleware is active.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    # Suppress watchfiles "X changes detected" INFO messages.
    logging.getLogger("watchfiles.main").setLevel(logging.WARNING)
