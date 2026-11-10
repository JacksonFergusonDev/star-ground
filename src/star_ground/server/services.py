"""What every route shares: the instance's settings, limits and cache.

`create_app` builds one `Services` per application and keeps it on
`app.state`; routes ask for it, and for the rate-limited client, as FastAPI
dependencies (`ServicesDep`, `Limit`).
"""

import ipaddress
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

from fastapi import Depends, Request

from star_ground.server.limits import OcrQueue, RateLimiter, ReadingCache
from star_ground.server.models import Problem


@dataclass(frozen=True, slots=True)
class Settings:
    """How a server instance is configured.

    Attributes:
        cors_origins: Browser origins allowed to call the API (the GitHub
            Pages site); none when the site is served from the same origin.
        max_body_bytes: The largest request body accepted.
        requests_per_minute: Each client's limit on the API's working routes.
        ocr: Whether OCR runs at all; off, image parts lists are reported
            and never offered.
        ocr_per_hour: Each client's limit on documents read by OCR, which
            take an instance's cores for tens of seconds each.
        trust_proxy: Take the client's address from `X-Forwarded-For`, as
            set by the platform's proxy (Cloud Run).
        cache_bytes: The reading cache's budget (see `ReadingCache`).
        site_dir: The built website (`web/dist`), served at `/` beside the
            API; none when the site is hosted elsewhere (GitHub Pages).
    """

    cors_origins: tuple[str, ...] = ()
    max_body_bytes: int = 50_000_000
    requests_per_minute: int = 30
    ocr: bool = True
    ocr_per_hour: int = 10
    trust_proxy: bool = False
    cache_bytes: int = 200_000_000
    site_dir: Path | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> Settings:
        """Reads the settings from `STAR_GROUND_*` environment variables.

        Args:
            env: The environment.

        Returns:
            The settings, with defaults for anything unset.
        """
        defaults = cls()
        origins = env.get("STAR_GROUND_CORS_ORIGINS", "")
        site = env.get("STAR_GROUND_SITE_DIR")
        return cls(
            cors_origins=tuple(o.strip() for o in origins.split(",") if o.strip()),
            requests_per_minute=int(
                env.get("STAR_GROUND_REQUESTS_PER_MINUTE", defaults.requests_per_minute)
            ),
            ocr=env.get("STAR_GROUND_OCR", "1") != "0",
            ocr_per_hour=int(
                env.get("STAR_GROUND_OCR_PER_HOUR", defaults.ocr_per_hour)
            ),
            trust_proxy=env.get("STAR_GROUND_TRUST_PROXY", "") == "1",
            site_dir=Path(site) if site else None,
        )


@dataclass(frozen=True, slots=True)
class Services:
    """One application's settings, limits, OCR queue and reading cache.

    Attributes:
        settings: The instance's configuration.
        limiter: Each client's limit on the working routes.
        ocr_limiter: Each client's hourly limit on documents read by OCR.
        ocr_queue: The one OCR slot, and who waits for it.
        cache: Readings of uploads and links, by document.
    """

    settings: Settings
    limiter: RateLimiter
    ocr_limiter: RateLimiter
    ocr_queue: OcrQueue
    cache: ReadingCache

    @classmethod
    def for_settings(cls, settings: Settings) -> Services:
        """Fresh limits, queue and cache for an instance.

        Args:
            settings: The instance's configuration.

        Returns:
            The services.
        """
        return cls(
            settings=settings,
            limiter=RateLimiter(settings.requests_per_minute),
            ocr_limiter=RateLimiter(settings.ocr_per_hour, window=3600),
            ocr_queue=OcrQueue(1),
            cache=ReadingCache(settings.cache_bytes),
        )

    @property
    def ocr(self) -> bool:
        """Whether this instance runs OCR at all."""
        return self.settings.ocr


class RefusedError(Exception):
    """A request the API refuses, with the body to answer it with.

    Args:
        status: The HTTP status.
        body: The response body.
    """

    def __init__(self, status: int, body: Problem) -> None:
        super().__init__(body.message)
        self.status = status
        self.body = body


def services(request: Request) -> Services:
    """The application's services, for a route that depends on them."""
    found: Services = request.app.state.services
    return found


ServicesDep = Annotated[Services, Depends(services)]
"""A route parameter receiving the application's `Services`."""


def client_key(request: Request, trust_proxy: bool) -> str:
    """Who the limits count a request against.

    The client's address (behind a proxy, the last hop it appended). An IPv6
    client counts by its /64: a home or a server is handed a whole /64 and
    can take a fresh address from it for every request.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if trust_proxy and forwarded:
        address = forwarded.split(",")[-1].strip()
    else:
        address = request.client.host if request.client else "unknown"
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return address
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return str(ip.ipv4_mapped)
        return str(ipaddress.IPv6Network((ip, 64), strict=False))
    return address


@dataclass(frozen=True, slots=True)
class RateLimit:
    """A request's place in its client's limit, counted when called.

    A route calls it first thing in its body. As a dependency that counted
    on its own it would run before FastAPI checks the request, so a request
    turned away as invalid (a 422) would use up one of the client's.

    Attributes:
        request: The request being counted.
        services: The application's services, holding the limiter.
    """

    request: Request
    services: Services

    def __call__(self) -> str:
        """Counts the request against its client's limit; returns the client.

        Raises:
            RefusedError: The client has used its requests for this minute.
        """
        client = client_key(self.request, self.services.settings.trust_proxy)
        if not self.services.limiter.allow(client):
            raise RefusedError(
                429, Problem(message="Too many requests. Try again in a minute.")
            )
        return client


def rate_limit(request: Request, services: ServicesDep) -> RateLimit:
    """The request's `RateLimit`, for its route to count."""
    return RateLimit(request, services)


Limit = Annotated[RateLimit, Depends(rate_limit)]
"""A route parameter receiving the request's `RateLimit`, to call first."""
