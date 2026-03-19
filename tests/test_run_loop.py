"""Tests for p1s_autoclear.run_loop metadata helpers."""

import json
import zipfile
from pathlib import Path

import pytest

from p1s_autoclear.run_loop import (
    get_loop_count,
    get_loop_count_for_plate,
    should_run_bed_leveling,
)


def test_get_loop_count_coerces_string_json(tmp_path: Path) -> None:
    p = tmp_path / "job.3mf"
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr(
            "Metadata/p1s_autoclear_settings.json",
            json.dumps({"loop_count": "12"}),
        )
    assert get_loop_count(p, None) == 12


def test_get_loop_count_invalid_string_defaults(tmp_path: Path) -> None:
    p = tmp_path / "bad.3mf"
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr(
            "Metadata/p1s_autoclear_settings.json",
            json.dumps({"loop_count": "nope"}),
        )
    assert get_loop_count(p, None) == 1


def test_get_loop_count_for_plate_string_in_plate_settings(tmp_path: Path) -> None:
    p = tmp_path / "multi.3mf"
    settings = {
        "loop_count": 1,
        "plate_settings": {"2": {"loop_count": "5"}},
    }
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr(
            "Metadata/p1s_autoclear_settings.json",
            json.dumps(settings),
        )
    assert get_loop_count_for_plate(settings, 2, None) == 5


@pytest.mark.parametrize(
    "run,interval,expected",
    [
        (1, 0, True),
        (2, 0, False),
        (2, 2, False),
        (3, 2, True),
        (4, 2, False),
        (5, 2, True),
    ],
)
def test_should_run_bed_leveling(run: int, interval: int, expected: bool) -> None:
    assert should_run_bed_leveling(run, interval) is expected
