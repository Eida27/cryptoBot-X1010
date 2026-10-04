import csv
import gzip
import hashlib
import heapq
import io
import json
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal as D
from itertools import groupby
from pathlib import Path
from typing import Any

from crypto_bot.config import Settings
from crypto_bot.domain.clock import SystemClock
from crypto_bot.domain.models import Candle, CandleEvent, FundingEvent, MarketEvent
from crypto_bot.exchange.binance_adapter import BinanceAdapter
from crypto_bot.exchange.normalization import normalize_candle
from crypto_bot.market.validation import validate_candle
from crypto_bot.storage.repository import digest, encode, plain


@dataclass(frozen=True)
class DatasetRequest:
    symbols: tuple[str, ...]
    start_ms: int
    end_ms: int
    warmup: int = 1000

    @property
    def first_ms(self) -> int:
        return self.start_ms - self.warmup * 3600000


@dataclass(frozen=True)
class DataFile:
    path: Path
    symbol: str
    role: str
    checksum: str
    first_ms: int
    end_ms: int


@dataclass(frozen=True)
class DatasetManifest:
    root: Path
    request: DatasetRequest
    files: tuple[DataFile, ...]
    content_hash: str
    split_boundaries: tuple[int, int, int, int]
    retrieved_ms: int
    filters: dict[str, Any]
    source: str = "Binance USD-M Futures contract/mark/funding REST"
    filter_assumption: str = "Current tradability snapshot; historical minima not reconstructed"


@dataclass(frozen=True)
class DataQualityReport:
    valid: bool
    issues: tuple[str, ...]


def checked_rows(rows: Iterable[Candle], symbol: str, interval_ms: int) -> Iterator[Candle]:
    previous = None
    for candle in rows:
        validate_candle(candle)
        if candle.symbol != symbol or not candle.closed or candle.close_ms - candle.open_ms != interval_ms:
            raise ValueError(f"{symbol}:{candle.open_ms}: INVALID_INTERVAL")
        if previous is not None and candle.open_ms < previous.close_ms:
            raise ValueError(f"{symbol}:{candle.open_ms}: DUPLICATE_OR_REORDERED")
        if previous is not None and candle.open_ms != previous.close_ms:
            raise ValueError(f"{symbol}:{candle.open_ms}: GAP")
        previous = candle
        yield candle


def validate_rows(rows: Iterable[Candle], symbol: str, interval_ms: int) -> int:
    return sum(1 for _ in checked_rows(rows, symbol, interval_ms))


def checksum(path: Path) -> str:
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def write_daily(rows: Iterable[Candle], symbol: str, role: str, destination: Path,
                request: DatasetRequest) -> list[DataFile]:
    files = []
    first, end = None, None
    for day, values in groupby(checked_rows(rows, symbol, 60000), lambda c: c.open_ms // 86400000):
        path = destination / f"{symbol}-{role}-{day}.csv.gz"
        with path.open("wb") as binary, gzip.GzipFile(filename="", fileobj=binary, mode="wb", mtime=0) as compressed, io.TextIOWrapper(compressed, newline="") as text:
            writer = csv.writer(text, lineterminator="\n")
            day_first, day_end = None, None
            for candle in values:
                if first is None:
                    first = candle.open_ms
                day_first = candle.open_ms if day_first is None else day_first
                day_end = end = candle.close_ms
                writer.writerow([candle.open_ms, candle.close_ms, candle.open, candle.high,
                                 candle.low, candle.close, candle.volume, candle.quote_volume])
        assert day_first is not None and day_end is not None
        files.append(DataFile(path, symbol, role, checksum(path), day_first, day_end))
    if first != request.first_ms or end != request.end_ms:
        raise ValueError(f"{symbol}:{first}: GAP_AT_BOUNDARY")
    return files


def write_dataset(request: DatasetRequest, destination: Path,
                  trades: dict[str, Iterable[Candle]], marks: dict[str, Iterable[Candle]],
                  funding: dict[str, Iterable[FundingEvent]], filters: dict[str, Any]) -> DatasetManifest:
    if request.start_ms >= request.end_ms or request.warmup < 0:
        raise ValueError("Invalid dataset range")
    destination.mkdir(parents=True, exist_ok=True)
    if (destination / "manifest.json").exists():
        raise ValueError("Preserve existing manifest; download into a new directory")
    files = []
    for symbol in request.symbols:
        if request.first_ms < int(filters.get(symbol, {}).get("onboardDate", request.first_ms)):
            raise ValueError(f"{symbol}:{request.first_ms}: BEFORE_LISTING")
        files.extend(write_daily(trades[symbol], symbol, "trade", destination, request))
        files.extend(write_daily(marks[symbol], symbol, "mark", destination, request))
        events = sorted(funding.get(symbol, ()), key=lambda event: event.at_ms)
        if not events:
            raise ValueError(f"{symbol}:{request.first_ms}: MISSING_ACTUAL_FUNDING")
        if len({event.at_ms for event in events}) != len(events):
            raise ValueError(f"{symbol}: DUPLICATE_FUNDING")
        for event in events:
            if not event.rate.is_finite() or not event.mark.is_finite() or event.mark <= 0:
                raise ValueError(f"{symbol}:{event.at_ms}: INVALID_FUNDING")
        path = destination / f"{symbol}-funding.csv.gz"
        with path.open("wb") as binary, gzip.GzipFile(filename="", fileobj=binary, mode="wb", mtime=0) as compressed, io.TextIOWrapper(compressed, newline="") as text:
            writer = csv.writer(text, lineterminator="\n")
            for event in events:
                writer.writerow([event.at_ms, event.rate, event.mark, event.transaction_id])
        files.append(DataFile(path, symbol, "funding", checksum(path), request.first_ms, request.end_ms))
    duration = request.end_ms - request.start_ms
    split = (request.start_ms, request.start_ms + (duration * 60 // 100 // 60000) * 60000,
             request.start_ms + (duration * 80 // 100 // 60000) * 60000, request.end_ms)
    # Tiny fixtures must still contain three nonoverlapping chronological partitions.
    if duration >= 180000:
        split = (split[0], max(split[0] + 60000, split[1]), max(split[1] + 60000, split[2]), split[3])
    content_hash = digest({"files": [(f.symbol, f.role, f.checksum) for f in files],
                           "request": request, "filters": filters, "split": split})
    manifest = DatasetManifest(destination.resolve(), request, tuple(files), content_hash, split,
                               SystemClock().now_ms(), filters)
    (destination / "manifest.json").write_text(json.dumps({
        "request": plain(request), "files": [{**plain(f), "path": f.path.name} for f in files],
        "content_hash": content_hash, "split_boundaries": split, "retrieved_ms": manifest.retrieved_ms,
        "filters": filters, "source": manifest.source, "filter_assumption": manifest.filter_assumption,
    }, indent=2), encoding="utf-8")
    return manifest


def load_manifest(path: Path) -> DatasetManifest:
    manifest_path = path / "manifest.json" if path.is_dir() else path
    value = json.loads(manifest_path.read_text(encoding="utf-8"))
    root = manifest_path.parent.resolve()
    request = DatasetRequest(tuple(value["request"]["symbols"]), value["request"]["start_ms"],
                             value["request"]["end_ms"], value["request"]["warmup"])
    files = []
    for file in value["files"]:
        target = (root / file["path"]).resolve()
        if target.parent != root:
            raise ValueError("Dataset path escapes manifest directory")
        files.append(DataFile(target, file["symbol"], file["role"], file["checksum"], file["first_ms"], file["end_ms"]))
    return DatasetManifest(root, request, tuple(files), value["content_hash"], tuple(value["split_boundaries"]),
                           value["retrieved_ms"], value["filters"])


def iter_candles(manifest: DatasetManifest, symbol: str, role: str) -> Iterator[Candle]:
    for file in manifest.files:
        if file.symbol != symbol or file.role != role:
            continue
        with gzip.open(file.path, "rt", newline="") as source:
            for row in csv.reader(source):
                yield Candle(symbol, int(row[0]), int(row[1]), D(row[2]), D(row[3]), D(row[4]),
                             D(row[5]), D(row[6]), D(row[7]))


def iter_funding(manifest: DatasetManifest, symbol: str) -> Iterator[FundingEvent]:
    for file in manifest.files:
        if file.symbol == symbol and file.role == "funding":
            with gzip.open(file.path, "rt", newline="") as source:
                for row in csv.reader(source):
                    yield FundingEvent(symbol, int(row[0]), D(row[1]), D(row[2]), row[3])


def validate_dataset(manifest: DatasetManifest) -> DataQualityReport:
    issues = []
    for file in manifest.files:
        if not file.path.exists() or checksum(file.path) != file.checksum:
            issues.append(f"{file.symbol}:{file.first_ms}: CHECKSUM_MISMATCH")
    if not issues:
        try:
            for symbol in manifest.request.symbols:
                for role in ("trade", "mark"):
                    validate_rows(iter_candles(manifest, symbol, role), symbol, 60000)
                if not list(iter_funding(manifest, symbol)):
                    issues.append(f"{symbol}: MISSING_ACTUAL_FUNDING")
        except (ValueError, OSError) as exc:
            issues.append(str(exc))
    return DataQualityReport(not issues, tuple(issues))


def iter_events(manifest: DatasetManifest) -> Iterator[MarketEvent]:
    streams: list[Iterator[Any]] = []
    for symbol in manifest.request.symbols:
        streams.append((CandleEvent(c) for c in iter_candles(manifest, symbol, "trade")))
        streams.append(iter_funding(manifest, symbol))
    def sort_key(event: MarketEvent) -> tuple[int, int]:
        if isinstance(event, FundingEvent):
            return event.at_ms, 0
        assert isinstance(event, CandleEvent)
        return event.candle.open_ms, 1
    yield from heapq.merge(*streams, key=sort_key)


async def download_dataset(request: DatasetRequest, destination: Path,
                           adapter: BinanceAdapter | None = None) -> DatasetManifest:
    public = adapter or BinanceAdapter(Settings(mode="BACKTEST"))
    clock = await public.read("check_server_time")
    if request.end_ms > int(clock["serverTime"]):
        raise ValueError("Historical range contains incomplete candles")
    information = await public.read("exchange_information")
    filters = {v["symbol"]: v for v in information["symbols"] if v["symbol"] in request.symbols}
    # Stage only one UTC day in memory; compressed files are streamed thereafter.
    destination.mkdir(parents=True, exist_ok=True)
    staged = destination / "staging"
    staged.mkdir(exist_ok=True)
    trades: dict[str, Iterable[Candle]] = {}
    marks: dict[str, Iterable[Candle]] = {}
    funding: dict[str, list[FundingEvent]] = {}
    for symbol in request.symbols:
        for role, method in (("trade", "klines"), ("mark", "mark_klines")):
            path = staged / f"{symbol}-{role}.csv.gz"
            with gzip.open(path, "wt", newline="") as output:
                writer = csv.writer(output)
                cursor = request.first_ms
                while cursor < request.end_ms:
                    rows = await public.read(method, symbol=symbol, interval="1m", startTime=cursor,
                                             endTime=request.end_ms - 1, limit=1000)
                    if not rows:
                        raise ValueError(f"{symbol}:{cursor}: MISSING_PUBLIC_HISTORY")
                    for row in rows:
                        candle = normalize_candle(symbol, row, int(clock["serverTime"]))
                        if candle.open_ms < cursor or candle.close_ms > request.end_ms:
                            raise ValueError(f"{symbol}:{cursor}: INVALID_DOWNLOAD_BOUNDARY")
                        writer.writerow(row)
                    next_cursor = int(rows[-1][6]) + 1
                    if next_cursor <= cursor:
                        raise ValueError("Nonadvancing historical download")
                    cursor = next_cursor
            def read_stage(target: Path = path, name: str = symbol) -> Iterator[Candle]:
                with gzip.open(target, "rt", newline="") as source:
                    for row in csv.reader(source):
                        yield normalize_candle(name, row, int(clock["serverTime"]))
            (trades if role == "trade" else marks)[symbol] = read_stage()
        events = []
        cursor = request.first_ms
        while cursor < request.end_ms:
            rows = await public.read("funding_history", symbol=symbol, startTime=cursor,
                                     endTime=request.end_ms - 1, limit=1000)
            if not rows:
                break
            events.extend(FundingEvent(symbol, int(v["fundingTime"]), D(v["fundingRate"]),
                                       D(v["markPrice"]), f"{symbol}:{v['fundingTime']}") for v in rows)
            cursor = int(rows[-1]["fundingTime"]) + 1
        funding[symbol] = events
    return write_dataset(request, destination, trades, marks, funding, filters)
