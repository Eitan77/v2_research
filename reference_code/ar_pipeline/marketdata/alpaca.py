"""Rate-limited, resumable Alpaca historical market-data access.

The client stores provider responses before interpretation.  A failed page or a
repeated pagination token is an error, never a silently shortened data set.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import random
import threading
import time
from typing import Any, Iterable

import pandas as pd
import requests
from dotenv import load_dotenv

from ar_pipeline.contracts import as_utc, canonical_json


class MarketDataError(RuntimeError):
    pass


@dataclass(frozen=True)
class QuoteRequest:
    symbol: str
    start: pd.Timestamp
    end: pd.Timestamp
    feed: str = "sip"

    def __post_init__(self) -> None:
        symbol = self.symbol.strip().upper()
        if not symbol:
            raise MarketDataError("quote request symbol must not be empty")
        start = as_utc(self.start)
        end = as_utc(self.end)
        if end <= start:
            raise MarketDataError("quote request end must be after start")
        if self.feed.lower() not in {"sip", "iex", "otc"}:
            raise MarketDataError("quote request feed must be explicit (sip, iex, or otc)")
        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "end", end)
        object.__setattr__(self, "feed", self.feed.lower())

    def key(self) -> dict[str, str]:
        return {
            "endpoint": "stocks/quotes",
            "symbol": self.symbol,
            "start": iso_z(self.start),
            "end": iso_z(self.end),
            "feed": self.feed,
        }


class CachedResponseStore:
    """Content-addressed raw response cache with sidecar provenance metadata."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def path_for(self, request_key: dict[str, Any]) -> Path:
        digest = sha256(canonical_json(request_key).encode("utf-8")).hexdigest()
        symbol = str(request_key.get("symbol", "unknown")).upper()
        return self.root / "alpaca" / "quotes" / str(request_key.get("feed", "unknown")) / symbol / f"{digest}.json"

    def read(self, request_key: dict[str, Any]) -> list[dict[str, Any]] | None:
        path = self.path_for(request_key)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            rows = payload.get("rows") if isinstance(payload, dict) else None
            if not isinstance(rows, list):
                raise ValueError("cache rows missing")
            return [dict(row) for row in rows if isinstance(row, dict)]
        except Exception as exc:
            raise MarketDataError(f"corrupt quote cache {path}: {exc}") from exc

    def write(self, request_key: dict[str, Any], rows: list[dict[str, Any]], metadata: dict[str, Any]) -> Path:
        path = self.path_for(request_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "request": request_key,
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "row_count": len(rows),
            "rows_sha256": sha256(canonical_json(rows).encode("utf-8")).hexdigest(),
            "metadata": metadata,
            "rows": rows,
        }
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, separators=(",", ":"), default=str), encoding="utf-8")
        tmp.replace(path)
        return path


class AlpacaHistoricalClient:
    """Thread-safe-at-the-call-level historical client with bounded retries."""

    def __init__(
        self,
        api_key_id: str,
        api_secret_key: str,
        *,
        base_url: str = "https://data.alpaca.markets",
        timeout_seconds: float = 45.0,
        max_retries: int = 6,
        requests_per_minute: int | None = None,
    ) -> None:
        if not api_key_id or not api_secret_key:
            raise MarketDataError("Alpaca API credentials are required")
        self.api_key_id = api_key_id
        self.api_secret_key = api_secret_key
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = float(timeout_seconds)
        self.max_retries = int(max_retries)
        self.requests_per_minute = int(requests_per_minute) if requests_per_minute else None
        self._local = threading.local()
        self._rate_lock = threading.Lock()
        self._next_request_at = 0.0

    @classmethod
    def from_env(cls, env_path: str | Path = "D:/AlgoResearch/.env") -> "AlpacaHistoricalClient":
        load_dotenv(Path(env_path))
        return cls(
            os.getenv("ALPACA_API_KEY_ID", ""),
            os.getenv("ALPACA_API_SECRET_KEY", ""),
            base_url=os.getenv("ALPACA_DATA_BASE_URL", "https://data.alpaca.markets"),
            requests_per_minute=int(os.getenv("ALPACA_REQUESTS_PER_MINUTE", "0")) or None,
        )

    def quote_rows(self, request: QuoteRequest) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        params: dict[str, Any] = {
            "symbols": request.symbol,
            "start": iso_z(request.start),
            "end": iso_z(request.end),
            "feed": request.feed,
            "limit": 10_000,
            "sort": "asc",
        }
        pages, metadata = self._paged_json("/v2/stocks/quotes", params)
        rows: list[dict[str, Any]] = []
        for payload in pages:
            body = payload.get("quotes", {}) if isinstance(payload, dict) else {}
            if not isinstance(body, dict):
                raise MarketDataError("unexpected Alpaca quote payload: quotes is not an object")
            for symbol, values in body.items():
                if not isinstance(values, list):
                    raise MarketDataError("unexpected Alpaca quote payload: symbol quote rows are not a list")
                for value in values:
                    if isinstance(value, dict):
                        rows.append({"S": str(symbol).upper(), **value})
        return rows, metadata

    def _paged_json(self, path: str, params: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        token: str | None = None
        seen_tokens: set[str] = set()
        pages: list[dict[str, Any]] = []
        attempts = 0
        while True:
            query = dict(params)
            if token:
                query["page_token"] = token
            payload = self._get_json(path, query)
            if not isinstance(payload, dict):
                raise MarketDataError(f"unexpected non-object response for {path}")
            pages.append(payload)
            next_token = payload.get("next_page_token")
            if not next_token:
                break
            if not isinstance(next_token, str) or next_token in seen_tokens:
                raise MarketDataError(f"pagination did not advance for {path}")
            seen_tokens.add(next_token)
            token = next_token
            attempts += 1
            if attempts > 1_000_000:
                raise MarketDataError(f"pagination safety limit exceeded for {path}")
        return pages, {"endpoint": path, "pages": len(pages), "complete": True, "params": params}

    def _get_json(self, path: str, params: dict[str, Any]) -> dict[str, Any] | list[Any]:
        for attempt in range(self.max_retries + 1):
            self._wait_for_rate_slot()
            response = self._session().get(f"{self.base_url}{path}", params=params, timeout=self.timeout_seconds)
            if response.status_code == 429 or response.status_code >= 500:
                if attempt >= self.max_retries:
                    response.raise_for_status()
                time.sleep(_retry_delay(response, attempt))
                continue
            response.raise_for_status()
            try:
                return response.json()
            except ValueError as exc:
                raise MarketDataError(f"invalid JSON from {path}") from exc
        raise AssertionError("retry loop exhausted")

    def _wait_for_rate_slot(self) -> None:
        """Smooth requests across the minute while allowing concurrent I/O."""
        if not self.requests_per_minute:
            return
        interval = 60.0 / self.requests_per_minute
        with self._rate_lock:
            now = time.monotonic()
            scheduled = max(now, self._next_request_at)
            self._next_request_at = scheduled + interval
        delay = scheduled - now
        if delay > 0:
            time.sleep(delay)

    def _session(self) -> requests.Session:
        session = getattr(self._local, "session", None)
        if session is None:
            session = requests.Session()
            session.headers.update(
                {
                    "APCA-API-KEY-ID": self.api_key_id,
                    "APCA-API-SECRET-KEY": self.api_secret_key,
                    "Accept": "application/json",
                }
            )
            self._local.session = session
        return session


def fetch_quote_requests(
    requests_: Iterable[QuoteRequest],
    *,
    client: AlpacaHistoricalClient,
    cache: CachedResponseStore,
    workers: int | str = "auto",
) -> dict[QuoteRequest, pd.DataFrame]:
    """Fetch each unique request concurrently while preserving complete caches."""

    unique: dict[str, QuoteRequest] = {}
    for request in requests_:
        unique[canonical_json(request.key())] = request
    if not unique:
        return {}
    count = _resolve_workers(workers)
    results: dict[QuoteRequest, pd.DataFrame] = {}

    def fetch_one(request: QuoteRequest) -> tuple[QuoteRequest, pd.DataFrame]:
        key = request.key()
        rows = cache.read(key)
        if rows is None:
            rows, metadata = client.quote_rows(request)
            cache.write(key, rows, metadata)
        return request, normalize_quote_rows(rows, request.symbol)

    with ThreadPoolExecutor(max_workers=count, thread_name_prefix="alpaca-quotes") as executor:
        futures = {executor.submit(fetch_one, request): request for request in unique.values()}
        for future in as_completed(futures):
            request = futures[future]
            try:
                completed_request, frame = future.result()
            except Exception as exc:
                raise MarketDataError(f"quote fetch failed for {request.symbol} {iso_z(request.start)}..{iso_z(request.end)}: {exc}") from exc
            results[completed_request] = frame
    return results


def normalize_quote_rows(rows: list[dict[str, Any]], symbol: str) -> pd.DataFrame:
    columns = ["symbol", "timestamp", "bid_price", "ask_price", "bid_size", "ask_size", "bid_exchange", "ask_exchange", "conditions", "tape"]
    if not rows:
        return pd.DataFrame(columns=columns)
    mapped: list[dict[str, Any]] = []
    for row in rows:
        mapped.append(
            {
                "symbol": str(row.get("S") or symbol).upper(),
                "timestamp": row.get("t"),
                "bid_price": row.get("bp"),
                "ask_price": row.get("ap"),
                "bid_size": row.get("bs"),
                "ask_size": row.get("as"),
                "bid_exchange": row.get("bx"),
                "ask_exchange": row.get("ax"),
                "conditions": row.get("c"),
                "tape": row.get("z"),
            }
        )
    out = pd.DataFrame(mapped)
    out["timestamp"] = pd.to_datetime(out["timestamp"], utc=True, errors="coerce", format="mixed")
    for column in ("bid_price", "ask_price", "bid_size", "ask_size"):
        out[column] = pd.to_numeric(out[column], errors="coerce")
    valid = (
        out["timestamp"].notna()
        & (out["bid_price"] > 0)
        & (out["ask_price"] > 0)
        & (out["ask_price"] >= out["bid_price"])
    )
    return out.loc[valid].sort_values("timestamp", kind="stable").reset_index(drop=True)


def iso_z(value: Any) -> str:
    return as_utc(value).isoformat().replace("+00:00", "Z")


def _resolve_workers(value: int | str) -> int:
    if str(value).lower() == "auto":
        # API rate limits and connection pools make unlimited CPU-count workers
        # slower and less reliable.  This remains configurable for paid tiers.
        return min(16, max(2, (os.cpu_count() or 2) * 2))
    workers = int(value)
    if workers < 1:
        raise MarketDataError("workers must be >= 1")
    return workers


def _retry_delay(response: requests.Response, attempt: int) -> float:
    retry_after = response.headers.get("Retry-After")
    if retry_after:
        try:
            return min(120.0, max(0.1, float(retry_after)))
        except ValueError:
            pass
    reset = response.headers.get("X-RateLimit-Reset")
    if reset:
        try:
            return min(120.0, max(0.1, float(reset) - time.time()))
        except ValueError:
            pass
    return min(60.0, 0.5 * (2**attempt)) + random.uniform(0.0, 0.25)
