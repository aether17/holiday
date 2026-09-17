from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from generate import (
    GenerationError,
    announced_cn_years,
    build_distribution,
    calendar_is_rest_day,
    compare_cn_with_nate,
    country_calendar,
    region_data,
    write_distribution,
)
from verify import verify


def nate_payload(year: int) -> dict:
    calendar = country_calendar("CN", [year])
    days = []
    current = date(year, 1, 1)
    while current.year == year:
        default_rest = current.weekday() in calendar.weekend
        actual_rest = calendar_is_rest_day(calendar, current)
        if default_rest != actual_rest:
            days.append(
                {
                    "name": "test",
                    "date": current.isoformat(),
                    "isOffDay": actual_rest,
                }
            )
        current = date.fromordinal(current.toordinal() + 1)
    return {"year": year, "papers": ["https://example.test/notice"], "days": days}


def fake_nate(year: int) -> dict:
    if year in (2025, 2026):
        return nate_payload(year)
    return {"year": year, "papers": [], "days": []}


class GenerateTests(unittest.TestCase):
    def test_known_cn_and_jp_facts(self) -> None:
        cn = country_calendar("CN", [2026])
        jp = country_calendar("JP", [2026])
        self.assertTrue(cn.is_working_day(date(2026, 2, 14)))
        self.assertIn(date(2026, 10, 1), cn)
        self.assertIn(date(2026, 1, 1), jp)

    def test_hong_kong_uses_upstream_weekend(self) -> None:
        hk = country_calendar("HK", [2026])
        data = region_data("HK", [2026])
        self.assertEqual(data["weekend"], [7])
        self.assertTrue(hk.is_working_day(date(2026, 1, 3)))
        self.assertFalse(hk.is_working_day(date(2026, 1, 4)))
        self.assertIn("2026-10-01", data["years"]["2026"]["off_days"])
        self.assertNotIn("2026-01-03", data["years"]["2026"]["weekend_workdays"])

    def test_cn_weekend_workday_is_an_explicit_exception(self) -> None:
        data = region_data("CN", [2026])
        self.assertEqual(data["weekend"], [6, 7])
        self.assertIn("2026-02-14", data["years"]["2026"]["weekend_workdays"])

    def test_unannounced_cn_future_years_are_omitted(self) -> None:
        years, _ = announced_cn_years(range(2025, 2029), 2026, fake_nate)
        self.assertEqual(years, [2025, 2026])
        with self.assertRaises(GenerationError):
            announced_cn_years([2026], 2026, lambda _: None)

    def test_cn_comparison_detects_different_day_kind(self) -> None:
        calendar = country_calendar("CN", [2026])
        payload = nate_payload(2026)
        compare_cn_with_nate(calendar, 2026, payload)
        payload["days"].append(
            {"name": "wrong", "date": "2026-01-05", "isOffDay": True}
        )
        with self.assertRaises(GenerationError):
            compare_cn_with_nate(calendar, 2026, payload)

    def test_same_content_keeps_bytes_and_generated_at(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "first"
            second = root / "second"
            write_distribution(
                first,
                build_distribution(2026, "2026-09-17T01:00:00Z", None, fake_nate),
            )
            write_distribution(
                second,
                build_distribution(2026, "2026-09-18T01:00:00Z", first, fake_nate),
            )
            verify(first, 2026)
            verify(second, 2026)
            self.assertEqual(
                {path.name: path.read_bytes() for path in first.iterdir()},
                {path.name: path.read_bytes() for path in second.iterdir()},
            )
            index = json.loads((second / "index.json").read_text())
            self.assertEqual(index["generated_at"], "2026-09-17T01:00:00Z")


if __name__ == "__main__":
    unittest.main()
