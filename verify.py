#!/usr/bin/env python3
"""Validate a generated holiday release directory."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

from generate import REGIONS


class VerificationError(RuntimeError):
    pass


def read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise VerificationError(f"{path}: {error}") from error
    if not isinstance(value, dict):
        raise VerificationError(f"{path}: root must be an object")
    return value


def validate_timestamp(value: Any, source: str) -> None:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise VerificationError(f"{source}: generated_at must be UTC with Z suffix")
    try:
        datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError as error:
        raise VerificationError(f"{source}: invalid generated_at") from error


def validate_dates(values: Any, year: int, source: str) -> list[date]:
    if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
        raise VerificationError(f"{source}: dates must be a string array")
    if values != sorted(set(values)):
        raise VerificationError(f"{source}: dates must be sorted and unique")
    try:
        parsed = [date.fromisoformat(value) for value in values]
    except ValueError as error:
        raise VerificationError(f"{source}: invalid ISO date") from error
    if any(day.year != year for day in parsed):
        raise VerificationError(f"{source}: date outside year")
    return parsed


def validate_region(path: Path, region: str, base_year: int) -> None:
    payload = read_object(path)
    if set(payload) != {"region", "generated_at", "weekend", "years"}:
        raise VerificationError(f"{path}: unexpected keys")
    if payload["region"] != region:
        raise VerificationError(f"{path}: wrong region")
    validate_timestamp(payload["generated_at"], str(path))

    weekend = payload["weekend"]
    if (
        not isinstance(weekend, list)
        or weekend != sorted(set(weekend))
        or any(not isinstance(day, int) or not 1 <= day <= 7 for day in weekend)
    ):
        raise VerificationError(f"{path}: invalid weekend")

    years = payload["years"]
    if not isinstance(years, dict):
        raise VerificationError(f"{path}: years must be an object")
    expected = {str(year) for year in range(base_year - 1, base_year + 3)}
    if region != "CN" and set(years) != expected:
        raise VerificationError(f"{path}: expected years {sorted(expected)}")
    if region == "CN" and (not set(years).issubset(expected) or str(base_year) not in years):
        raise VerificationError(f"{path}: invalid announced-year subset")

    for year_text, value in years.items():
        if not isinstance(value, dict) or set(value) != {"off_days", "weekend_workdays"}:
            raise VerificationError(f"{path}: invalid year {year_text}")
        year = int(year_text)
        off_days = validate_dates(value["off_days"], year, f"{path}:{year}:off_days")
        workdays = validate_dates(
            value["weekend_workdays"], year, f"{path}:{year}:weekend_workdays"
        )
        if set(off_days) & set(workdays):
            raise VerificationError(f"{path}:{year}: off/work days overlap")
        if any(day.isoweekday() not in weekend for day in workdays):
            raise VerificationError(f"{path}:{year}: workday is not a weekend exception")


def verify(directory: Path, base_year: int) -> None:
    expected_files = {"index.json", *(f"{region}.json" for region in REGIONS)}
    actual_files = {path.name for path in directory.iterdir() if path.is_file()}
    if actual_files != expected_files:
        raise VerificationError(
            f"{directory}: expected {sorted(expected_files)}, found {sorted(actual_files)}"
        )

    index = read_object(directory / "index.json")
    if set(index) != {"generated_at", "regions"}:
        raise VerificationError("index.json: unexpected keys")
    validate_timestamp(index["generated_at"], "index.json")
    regions = index["regions"]
    if not isinstance(regions, dict) or set(regions) != set(REGIONS):
        raise VerificationError("index.json: wrong region set")

    for region in REGIONS:
        validate_region(directory / f"{region}.json", region, base_year)
        entry = regions[region]
        if not isinstance(entry, dict) or set(entry) != {"sha256"}:
            raise VerificationError(f"index.json: invalid {region} entry")
        digest = entry["sha256"]
        actual = hashlib.sha256((directory / f"{region}.json").read_bytes()).hexdigest()
        if digest != actual:
            raise VerificationError(f"index.json: {region} sha256 mismatch")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("--base-year", type=int, default=datetime.now().year)
    args = parser.parse_args()
    verify(args.directory, args.base_year)
    print(f"verified {args.directory}")


if __name__ == "__main__":
    try:
        main()
    except VerificationError as error:
        raise SystemExit(str(error)) from error
