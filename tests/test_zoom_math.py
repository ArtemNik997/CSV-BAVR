import pytest

import apply_zoom as az

R = 13


def ev(t, x, y, typ):
    return {"t": t, "x": x, "y": y, "type": typ}


def segs(events, fps=25.0, ramp=0.5, viewport=(1280, 720), size=None):
    return az.build_segments(az.normalize_events(events), fps, ramp, viewport, size)


def zoom(s, n, factor=2.0):
    return az.zoom_at_frame(s, n, factor, R)


SPEC_PAIR = [ev(3.0, 640, 360, "in"), ev(8.0, 640, 360, "out")]


# ---------- ramp and easing ----------


def test_ramp_frames_rounds_half_up():
    assert az.ramp_frames(0.5, 25) == 13  # round() дал бы 12


def test_ramp_is_at_least_one_frame():
    assert az.ramp_frames(0, 25) == 1
    assert az.ramp_frames(0.01, 25) == 1


def test_seconds_to_frames():
    assert az.to_frame(3.0, 25) == 75
    assert az.to_frame(0.5, 25) == 13


def test_ease_endpoints_and_midpoint():
    assert az.ease(0) == 0
    assert az.ease(1) == 1
    assert az.ease(0.5) == pytest.approx(0.5)


def test_ease_clamps_input():
    assert az.ease(-3) == 0
    assert az.ease(7) == 1


def test_ease_is_flat_at_both_ends():
    assert az.ease(0.01) < 0.001
    assert 1 - az.ease(0.99) < 0.001


# ---------- cursor coords -> video pixels ----------


def test_map_is_identity_when_sizes_match():
    assert az.map_to_video(640, 360, (1280, 720), (1280, 720)) == (640, 360)


def test_map_scales_to_other_video_size():
    assert az.map_to_video(640, 360, (1280, 720), (1920, 1080)) == (960, 540)


def test_map_clamps_to_frame():
    assert az.map_to_video(-5, 9999, (1280, 720), (1280, 720)) == (0.0, 720.0)


def test_center_is_a_fraction_of_the_frame():
    s = segs([ev(1, 320, 180, "in")])
    assert (s[0].cx, s[0].cy) == (0.25, 0.25)


def test_center_fraction_does_not_depend_on_video_size():
    s = segs([ev(1, 320, 180, "in")], size=(1920, 1080))
    assert (s[0].cx, s[0].cy) == (0.25, 0.25)


# ---------- segments ----------


def test_single_pair_from_spec():
    assert segs(SPEC_PAIR) == [az.Segment(start=75, end=200, cx=0.5, cy=0.5)]


def test_two_pairs_keep_their_order():
    s = segs(
        [ev(1, 0, 0, "in"), ev(4, 0, 0, "out"), ev(6, 0, 0, "in"), ev(9, 0, 0, "out")]
    )
    assert [(x.start, x.end) for x in s] == [(25, 100), (150, 225)]


def test_hold_shorter_than_ramp_is_extended_to_ramp():
    s = segs([ev(1.0, 0, 0, "in"), ev(1.1, 0, 0, "out")])
    assert s[0].end == s[0].start + R


def test_next_zoom_waits_until_previous_one_has_finished():
    s = segs(
        [ev(1, 0, 0, "in"), ev(2, 0, 0, "out"), ev(2.1, 0, 0, "in"), ev(6, 0, 0, "out")]
    )
    assert s[0].end == 50
    assert s[1].start == s[0].end + R  # 63, а не 53


def test_in_without_out_is_open_ended():
    assert segs([ev(3, 640, 360, "in")])[0].end is None


# ---------- zoom ramps ----------


def test_zoom_ramps_in_holds_and_ramps_out():
    s = segs(SPEC_PAIR)
    assert zoom(s, 74) == 1.0
    assert zoom(s, 75) == 1.0
    assert 1.0 < zoom(s, 80) < 2.0
    assert zoom(s, 88) == pytest.approx(2.0)
    assert zoom(s, 150) == pytest.approx(2.0)
    assert zoom(s, 200) == pytest.approx(2.0)
    assert 1.0 < zoom(s, 205) < 2.0
    assert zoom(s, 213) == pytest.approx(1.0)
    assert zoom(s, 500) == pytest.approx(1.0)


def test_ramp_is_eased_not_linear():
    s = segs(SPEC_PAIR)
    values = [zoom(s, n) for n in range(75, 89)]
    steps = [b - a for a, b in zip(values, values[1:])]
    assert values == sorted(values)
    assert steps[0] < max(steps) and steps[-1] < max(steps)


def test_open_ended_zoom_holds_to_the_end_of_the_video():
    s = segs([ev(3, 640, 360, "in")])
    for n in (88, 200, 10**6):
        assert zoom(s, n) == pytest.approx(2.0)


def test_zoom_stays_between_one_and_factor_for_many_pairs():
    s = segs(
        [
            ev(1, 0, 0, "in"),
            ev(4, 0, 0, "out"),
            ev(5, 0, 0, "in"),
            ev(7, 0, 0, "out"),
            ev(9, 0, 0, "in"),
        ]
    )
    values = [zoom(s, n) for n in range(0, 500)]
    assert min(values) >= 1.0
    assert max(values) <= 2.0 + 1e-12


def test_zero_ramp_is_valid_and_instant():
    r = az.ramp_frames(0, 25)
    s = az.build_segments(az.normalize_events(SPEC_PAIR), 25.0, 0, (1280, 720))
    assert az.zoom_at_frame(s, 74, 2.0, r) == 1.0
    assert az.zoom_at_frame(s, 76, 2.0, r) == pytest.approx(2.0)


# ---------- crop: center on the point and clamp ----------


def test_crop_is_centered_on_the_point():
    s = segs([ev(1, 900, 500, "in"), ev(4, 900, 500, "out")])
    z, x, y = az.crop_at_frame(s, 50, 2.0, R, 1280, 720)
    assert z == pytest.approx(2.0)
    assert x + 1280 / z / 2 == pytest.approx(900)
    assert y + 720 / z / 2 == pytest.approx(500)


def test_crop_is_clamped_in_the_corners():
    top_left = segs([ev(1, 0, 0, "in")])
    bottom_right = segs([ev(1, 1280, 720, "in")])
    assert az.crop_at_frame(top_left, 100, 2.0, R, 1280, 720)[1:] == (0.0, 0.0)
    assert az.crop_at_frame(bottom_right, 100, 2.0, R, 1280, 720)[1:] == (640.0, 360.0)


@pytest.mark.parametrize(
    "px,py",
    [
        (0, 0),
        (1280, 0),
        (0, 720),
        (1280, 720),
        (5, 5),
        (1275, 715),
        (640, 360),
        (900, 500),
    ],
)
def test_crop_never_leaves_the_frame(px, py):
    s = segs([ev(1, px, py, "in"), ev(4, px, py, "out")])
    for n in range(0, 200):
        z, x, y = az.crop_at_frame(s, n, 2.0, R, 1280, 720)
        assert -1e-9 <= x <= 1280 - 1280 / z + 1e-9
        assert -1e-9 <= y <= 720 - 720 / z + 1e-9


def test_each_pair_zooms_on_its_own_point():
    s = segs(
        [
            ev(1, 500, 300, "in"),
            ev(3, 500, 300, "out"),
            ev(6, 900, 500, "in"),
            ev(9, 900, 500, "out"),
        ]
    )
    _, x1, y1 = az.crop_at_frame(s, 50, 2.0, R, 1280, 720)
    _, x2, y2 = az.crop_at_frame(s, 200, 2.0, R, 1280, 720)
    assert (x1, y1) == pytest.approx((180, 120))
    assert (x2, y2) == pytest.approx((580, 320))
    z_between, x_between, _ = az.crop_at_frame(s, 120, 2.0, R, 1280, 720)
    assert z_between == pytest.approx(1.0) and x_between == 0


# ---------- filter string zoompan ----------


def test_filter_contains_required_zoompan_options():
    f = az.build_filter(az.parse_sidecar({"events": SPEC_PAIR}))
    assert f.startswith("zoompan=z='")
    assert ":d=1:" in f
    assert "s=1280x720" in f
    assert ":fps=25" in f
    assert "iw-iw/zoom" in f and "ih-ih/zoom" in f  # зажим кропа


def test_filter_uses_actual_video_size():
    f = az.build_filter(az.parse_sidecar({"events": SPEC_PAIR}), (1920, 1080))
    assert "s=1920x1080" in f


def test_no_events_means_no_filter():
    assert az.build_filter(az.parse_sidecar({"events": []})) is None


@pytest.mark.parametrize(
    "events",
    [
        SPEC_PAIR,
        [
            ev(1, 100, 100, "in"),
            ev(4, 5, 5, "out"),
            ev(5, 1200, 700, "in"),
            ev(7, 0, 0, "out"),
            ev(9, 640, 360, "in"),
        ],
        [ev(0, 0, 0, "in")],
    ],
    ids=["spec-pair", "three-segments-open-end", "event-at-t0"],
)
def test_ffmpeg_expressions_match_the_reference_implementation(events, ffeval):
    sc = az.parse_sidecar({"events": events})
    s = az.build_segments(sc.events, sc.fps, sc.ramp_sec, sc.viewport)
    r = az.ramp_frames(sc.ramp_sec, sc.fps)
    z_expr = az.zoom_expr(s, sc.zoom_factor, r)
    x_expr = az.position_expr(s, r, "x")
    y_expr = az.position_expr(s, r, "y")
    iw, ih = 2560, 1440  # апскейл перед zoompan
    for n in range(0, 400):
        z_ref, x_ref, y_ref = az.crop_at_frame(s, n, sc.zoom_factor, r, iw, ih)
        z = ffeval(z_expr, on=n)
        assert z == pytest.approx(z_ref, abs=1e-9)
        assert ffeval(x_expr, on=n, zoom=z, iw=iw, ih=ih) == pytest.approx(
            x_ref, abs=1e-9
        )
        assert ffeval(y_expr, on=n, zoom=z, iw=iw, ih=ih) == pytest.approx(
            y_ref, abs=1e-9
        )
