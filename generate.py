#!/usr/bin/env python3
"""Generate release-ready holiday tables from pinned upstream data."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import holidays

HOLIDAYS_VERSION = "0.104"
REGIONS = ("CN", "TW", "HK", "JP", "KR", "SG", "US", "GB", "DE", "AU")
NATE_BASE_URL = "https://raw.githubusercontent.com/NateScarlet/holiday-cn/master"
NateFetcher = Callable[[int], dict[str, Any] | None]


class GenerationError(RuntimeError):
    pass


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def encoded_json(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch_nate_year(year: int, base_url: str = NATE_BASE_URL) -> dict[str, Any] | None:
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/{year}.json",
        headers={"User-Agent": "aether17/holiday generator"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        raise GenerationError(f"NateScarlet {year}: HTTP {error.code}") from error
    except (OSError, json.JSONDecodeError) as error:
        raise GenerationError(f"NateScarlet {year}: {error}") from error


def country_calendar(region: str, years: Iterable[int]):
    return holidays.country_holidays(region, years=years, categories="public")


def dates_in_year(year: int) -> Iterable[date]:
    current = date(year, 1, 1)
    end = date(year + 1, 1, 1)
    while current < end:
        yield current
        current += timedelta(days=1)


def validate_nate_payload(year: int, payload: dict[str, Any] | None, current_year: int) -> bool:
    if payload is None:
        if year <= current_year:
            raise GenerationError(f"NateScarlet has no file for announced year {year}")
        return False
    if payload.get("year") != year:
        raise GenerationError(f"NateScarlet {year}: wrong year")
    papers = payload.get("papers")
    days = payload.get("days")
    if not isinstance(papers, list) or not isinstance(days, list):
        raise GenerationError(f"NateScarlet {year}: papers/days must be arrays")
    if not papers and not days:
        if year <= current_year:
            raise GenerationError(f"NateScarlet {year}: official schedule is empty")
        return False
    if not papers or not days:
        raise GenerationError(f"NateScarlet {year}: incomplete official schedule")
    return True


def nate_day_kinds(year: int, payload: dict[str, Any]) -> dict[date, bool]:
    result: dict[date, bool] = {}
    for entry in payload["days"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("isOffDay"), bool):
            raise GenerationError(f"NateScarlet {year}: invalid day entry")
        try:
            day = date.fromisoformat(entry["date"])
        except (KeyError, TypeError, ValueError) as error:
            raise GenerationError(f"NateScarlet {year}: invalid date entry") from error
        if day.year != year or day in result:
            raise GenerationError(f"NateScarlet {year}: duplicate or out-of-year date {day}")
        result[day] = entry["isOffDay"]
    return result


def calendar_is_rest_day(calendar, day: date) -> bool:
    return day in calendar or (day.weekday() in calendar.weekend and day not in calendar.weekend_workdays)


def compare_cn_with_nate(calendar, year: int, payload: dict[str, Any]) -> None:
    overrides = nate_day_kinds(year, payload)
    mismatches: list[str] = []
    for day in dates_in_year(year):
        nate_is_rest = overrides.get(day, day.weekday() in calendar.weekend)
        if calendar_is_rest_day(calendar, day) != nate_is_rest:
            mismatches.append(day.isoformat())
    if mismatches:
        preview = ", ".join(mismatches[:8])
        raise GenerationError(f"CN {year} differs from NateScarlet on {preview}")


def announced_cn_years(
    years: Iterable[int], current_year: int, fetcher: NateFetcher
) -> tuple[list[int], dict[int, dict[str, Any]]]:
    announced: list[int] = []
    payloads: dict[int, dict[str, Any]] = {}
    for year in years:
        payload = fetcher(year)
        if validate_nate_payload(year, payload, current_year):
            announced.append(year)
            payloads[year] = payload  # type: ignore[assignment]
    return announced, payloads


def run_known_fact_checks() -> None:
    cn = country_calendar("CN", [2026])
    jp = country_calendar("JP", [2026])
    facts = (
        (cn.is_working_day(date(2026, 2, 14)), "2026-02-14 must be a CN working day"),
        (date(2026, 10, 1) in cn, "2026-10-01 must be a CN holiday"),
        (date(2026, 1, 1) in jp, "2026-01-01 must be a JP holiday"),
    )
    for actual, message in facts:
        if not actual:
            raise GenerationError(message)


def region_data(region: str, years: Iterable[int]) -> dict[str, Any]:
    years = list(years)
    calendar = country_calendar(region, years)
    weekend = sorted(day + 1 for day in calendar.weekend)
    year_data: dict[str, Any] = {}
    for year in years:
        off_days = sorted(day.isoformat() for day in calendar if day.year == year)
        weekend_workdays = sorted(
            day.isoformat()
            for day in calendar.weekend_workdays
            if day.year == year and day.weekday() in calendar.weekend
        )
        for day_text in weekend_workdays:
            if not calendar.is_working_day(date.fromisoformat(day_text)):
                raise GenerationError(f"{region} {day_text} is not a working-day exception")
        year_data[str(year)] = {
            "off_days": off_days,
            "weekend_workdays": weekend_workdays,
        }
    return {"region": region, "weekend": weekend, "years": year_data}


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text())
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def with_generated_at(
    content: dict[str, Any], previous: dict[str, Any] | None, generated_at: str
) -> dict[str, Any]:
    if previous:
        previous_content = {key: value for key, value in previous.items() if key != "generated_at"}
        previous_time = previous.get("generated_at")
        if previous_content == content and isinstance(previous_time, str):
            generated_at = previous_time
    if "region" in content:
        return {
            "region": content["region"],
            "generated_at": generated_at,
            "weekend": content["weekend"],
            "years": content["years"],
        }
    return {"generated_at": generated_at, **content}


def build_distribution(
    base_year: int,
    generated_at: str,
    previous_dir: Path | None,
    fetcher: NateFetcher,
) -> dict[str, bytes]:
    if holidays.__version__ != HOLIDAYS_VERSION:
        raise GenerationError(
            f"holidays=={HOLIDAYS_VERSION} required, found {holidays.__version__}"
        )
    run_known_fact_checks()
    years = list(range(base_year - 1, base_year + 3))
    cn_years, nate_payloads = announced_cn_years(years, base_year, fetcher)
    cn_calendar = country_calendar("CN", cn_years)
    for year in cn_years:
        compare_cn_with_nate(cn_calendar, year, nate_payloads[year])

    files: dict[str, bytes] = {}
    region_hashes: dict[str, dict[str, str]] = {}
    for region in REGIONS:
        included_years = cn_years if region == "CN" else years
        content = region_data(region, included_years)
        previous = read_json(previous_dir / f"{region}.json") if previous_dir else None
        payload = with_generated_at(content, previous, generated_at)
        data = encoded_json(payload)
        files[f"{region}.json"] = data
        region_hashes[region] = {"sha256": sha256(data)}

    index_content = {"regions": region_hashes}
    previous_index = read_json(previous_dir / "index.json") if previous_dir else None
    files["index.json"] = encoded_json(
        with_generated_at(index_content, previous_index, generated_at)
    )
    return files


def write_distribution(output: Path, files: dict[str, bytes]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}-", dir=output.parent))
    try:
        for name, data in files.items():
            (temporary / name).write_bytes(data)
        if output.exists():
            shutil.rmtree(output)
        temporary.replace(output)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("dist"))
    parser.add_argument("--previous-dir", type=Path)
    parser.add_argument("--base-year", type=int, default=datetime.now(timezone.utc).year)
    parser.add_argument("--generated-at", default=utc_timestamp())
    parser.add_argument("--nate-base-url", default=NATE_BASE_URL)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    fetcher = lambda year: fetch_nate_year(year, args.nate_base_url)
    files = build_distribution(
        args.base_year, args.generated_at, args.previous_dir, fetcher
    )
    write_distribution(args.output, files)
    print(f"generated {len(files)} files in {args.output}")


if __name__ == "__main__":
    try:
        main()
    except GenerationError as error:
        raise SystemExit(str(error)) from error
