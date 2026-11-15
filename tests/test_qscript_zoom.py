import json
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api")

import apply_zoom as az
import qscript as qs


@pytest.fixture(autouse=True)
def _zero_offset(monkeypatch):
    monkeypatch.setattr(qs, "ZOOM_TIME_OFFSET", 0.0)


def test_first_press_is_in_second_is_out():
    events = []
    assert qs.make_zoom_event(events, 100.0, 103.0, 640, 360) == "in"
    assert qs.make_zoom_event(events, 100.0, 108.0, 640, 360) == "out"
    assert events == [
        {"t": 3.0, "x": 640.0, "y": 360.0, "type": "in"},
        {"t": 8.0, "x": 640.0, "y": 360.0, "type": "out"},
    ]


def test_types_keep_alternating():
    events = []
    types = [qs.make_zoom_event(events, 0.0, float(i), 0, 0) for i in range(5)]
    assert types == ["in", "out", "in", "out", "in"]


def test_time_is_never_negative_or_decreasing():
    events = []
    qs.make_zoom_event(events, 100.0, 99.0, 0, 0)
    assert events[0]["t"] == 0.0
    qs.make_zoom_event(events, 100.0, 105.0, 0, 0)
    qs.make_zoom_event(events, 100.0, 104.0, 0, 0)
    assert [e["t"] for e in events] == [0.0, 5.0, 5.0]


def test_bad_payload_does_not_crash_the_page_callback():
    qs._reset_zoom_clock()
    assert qs._on_zoom_event({"x": 1}) == ""
    assert qs._on_zoom_event(None) == ""
    assert qs._ZOOM_EVENTS == []


def test_sidecar_is_not_written_without_events(tmp_path):
    video = tmp_path / "a.webm"
    video.write_bytes(b"x")
    assert qs.write_zoom_sidecar(str(video), []) is None
    assert not Path(str(video) + ".zoom.json").exists()


def test_stale_sidecar_is_removed_when_there_are_no_events(tmp_path):
    video = tmp_path / "a.webm"
    video.write_bytes(b"x")
    stale = Path(str(video) + ".zoom.json")
    stale.write_text("{}")
    assert qs.write_zoom_sidecar(str(video), []) is None
    assert not stale.exists()


def test_sidecar_matches_the_format_from_the_spec(tmp_path):
    video = tmp_path / "a.webm"
    video.write_bytes(b"x")
    events = [
        {"t": 3.0, "x": 640.0, "y": 360.0, "type": "in"},
        {"t": 8.0, "x": 640.0, "y": 360.0, "type": "out"},
    ]
    path = qs.write_zoom_sidecar(str(video), events)
    assert path == str(video) + ".zoom.json"
    assert json.loads(Path(path).read_text(encoding="utf-8")) == {
        "viewport": {"width": 1280, "height": 720},
        "fps": 25,
        "zoom_factor": 2.0,
        "ramp_sec": 0.5,
        "events": events,
    }
    assert not Path(path + ".part").exists()


def test_sidecar_written_by_qscript_is_readable_by_apply_zoom(tmp_path):
    video = tmp_path / "a.webm"
    video.write_bytes(b"x")
    events = [{"t": 1.0, "x": 10.0, "y": 20.0, "type": "in"}]
    sc = az.load_sidecar(qs.write_zoom_sidecar(str(video), events))
    assert [(e.t, e.x, e.y, e.type) for e in sc.events] == [(1.0, 10.0, 20.0, "in")]
