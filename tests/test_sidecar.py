import json
import logging

import pytest

import apply_zoom as az

SPEC = {
    "viewport": {"width": 1280, "height": 720},
    "fps": 25,
    "zoom_factor": 2.0,
    "ramp_sec": 0.5,
    "events": [
        {"t": 3.0, "x": 640, "y": 360, "type": "in"},
        {"t": 8.0, "x": 640, "y": 360, "type": "out"},
    ],
}


def pairs(sc):
    return [(e.t, e.type) for e in sc.events]


def test_parse_sidecar_from_spec():
    sc = az.parse_sidecar(SPEC)
    assert sc.viewport == (1280, 720)
    assert (sc.fps, sc.zoom_factor, sc.ramp_sec) == (25, 2.0, 0.5)
    assert [(e.t, e.x, e.y, e.type) for e in sc.events] == [
        (3.0, 640.0, 360.0, "in"),
        (8.0, 640.0, 360.0, "out"),
    ]


def test_defaults_for_missing_keys():
    sc = az.parse_sidecar({})
    assert sc.viewport == (1280, 720)
    assert (sc.fps, sc.zoom_factor, sc.ramp_sec) == (25.0, 2.0, 0.5)
    assert sc.events == ()


# ---------- old format without type ----------


def test_old_sidecar_without_type_alternates_in_out():
    sc = az.parse_sidecar(
        {
            "events": [
                {"t": 3, "x": 1, "y": 1},
                {"t": 8, "x": 2, "y": 2},
                {"t": 12, "x": 3, "y": 3},
            ]
        }
    )
    assert pairs(sc) == [(3.0, "in"), (8.0, "out"), (12.0, "in")]


def test_old_sidecar_events_are_sorted_by_time():
    sc = az.parse_sidecar(
        {
            "events": [
                {"t": 8, "x": 1, "y": 1},
                {"t": 3, "x": 2, "y": 2},
                {"t": 4, "x": 3, "y": 3},
            ]
        }
    )
    assert pairs(sc) == [(3.0, "in"), (4.0, "out"), (8.0, "in")]


def test_equal_timestamps_keep_their_original_order():
    sc = az.parse_sidecar(
        {
            "events": [
                {"t": 1, "x": 0, "y": 0, "type": "in"},
                {"t": 1, "x": 0, "y": 0, "type": "out"},
            ]
        }
    )
    assert pairs(sc) == [(1.0, "in"), (1.0, "out")]


# ---------- odd and incorrect events ----------


def test_redundant_same_type_event_is_ignored():
    sc = az.parse_sidecar(
        {
            "events": [
                {"t": 1, "x": 0, "y": 0, "type": "in"},
                {"t": 2, "x": 0, "y": 0, "type": "in"},
                {"t": 3, "x": 0, "y": 0, "type": "out"},
            ]
        }
    )
    assert pairs(sc) == [(1.0, "in"), (3.0, "out")]


def test_leading_out_is_ignored():
    sc = az.parse_sidecar(
        {
            "events": [
                {"t": 1, "x": 0, "y": 0, "type": "out"},
                {"t": 2, "x": 0, "y": 0, "type": "in"},
            ]
        }
    )
    assert pairs(sc) == [(2.0, "in")]


def test_invalid_events_are_skipped_with_a_warning(caplog):
    raw = [
        {"t": -1, "x": 1, "y": 1, "type": "in"},
        {"t": "x", "x": 1, "y": 1},
        {"t": 1, "x": 1},
        "junk",
        {"t": 2, "x": 1, "y": 1, "type": "bad"},
        {"t": 3, "x": 10, "y": 20, "type": "in"},
    ]
    with caplog.at_level(logging.WARNING, logger="apply_zoom"):
        sc = az.parse_sidecar({"events": raw})
    assert pairs(sc) == [(3.0, "in")]
    assert "skipped" in caplog.text


# ---------- format errors ----------


@pytest.mark.parametrize(
    "bad",
    [
        {"fps": 0},
        {"fps": "25"},
        {"fps": True},
        {"zoom_factor": 11},
        {"zoom_factor": 0.5},
        {"ramp_sec": -1},
        {"viewport": []},
        {"viewport": {"width": 0, "height": 720}},
        {"viewport": {"width": "a", "height": 720}},
        {"events": 5},
        [],
        "text",
    ],
)
def test_invalid_sidecar_raises(bad):
    with pytest.raises(az.SidecarError):
        az.parse_sidecar(bad)


# ---------- reading file ----------


def test_load_sidecar_reads_utf8_with_bom(tmp_path):
    path = tmp_path / "a.zoom.json"
    path.write_bytes(b"\xef\xbb\xbf" + json.dumps(SPEC).encode("utf-8"))
    assert len(az.load_sidecar(str(path)).events) == 2


def test_load_sidecar_rejects_broken_json(tmp_path):
    path = tmp_path / "a.zoom.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(az.SidecarError, match="invalid JSON"):
        az.load_sidecar(str(path))


def test_find_sidecar_prefers_video_dot_webm_dot_zoom_json(tmp_path):
    video = tmp_path / "clip.webm"
    (tmp_path / "clip.zoom.json").write_text("{}")
    assert az.find_sidecar(str(video)) == str(tmp_path / "clip.zoom.json")
    (tmp_path / "clip.webm.zoom.json").write_text("{}")
    assert az.find_sidecar(str(video)) == str(tmp_path / "clip.webm.zoom.json")


def test_find_sidecar_returns_none_when_missing(tmp_path):
    assert az.find_sidecar(str(tmp_path / "clip.webm")) is None
