import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import apply_zoom as az

PROBE_OUTPUT = (
    "Input #0, matroska,webm, from 'clip.webm':\n"
    "  Stream #0:0: Video: vp8, yuv420p, 1280x720, 25 fps, 25 tbr\n"
)


@pytest.fixture
def forbid_subprocess(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("ffmpeg must not be started")

    monkeypatch.setattr(az.subprocess, "run", boom)


@pytest.fixture
def no_ffmpeg_installed(monkeypatch):
    def missing(*args, **kwargs):
        raise FileNotFoundError("ffmpeg")

    monkeypatch.setattr(az.subprocess, "run", missing)


def ok_encode(cmd):
    Path(cmd[-1]).write_bytes(b"encoded")
    return SimpleNamespace(returncode=0, stdout="", stderr="")


@pytest.fixture
def fake_ffmpeg(monkeypatch):
    """Подменённый ffmpeg: probe отдаёт 1280x720, кодирование задаётся через state.encode."""
    state = SimpleNamespace(calls=[], encode=ok_encode)

    def run(cmd, **kwargs):
        state.calls.append(cmd)
        if "-vf" not in cmd:  # ffmpeg -i video (probe)
            return SimpleNamespace(returncode=1, stdout="", stderr=PROBE_OUTPUT)
        return state.encode(cmd)

    monkeypatch.setattr(az.subprocess, "run", run)
    monkeypatch.setattr(az.shutil, "which", lambda name: "/usr/bin/ffmpeg")
    return state


# ---------- no events -> video copies without changes ----------


def test_no_sidecar_copies_video_unchanged(video, tmp_path, forbid_subprocess):
    out = tmp_path / "nested" / "out.webm"
    result = az.process_video(str(video), output_path=str(out))
    assert result.copied and result.command is None
    assert out.read_bytes() == video.read_bytes()


def test_sidecar_without_events_copies_video_unchanged(
    video, make_sidecar, tmp_path, forbid_subprocess
):
    make_sidecar(video, [])
    out = tmp_path / "out.webm"
    assert az.process_video(str(video), output_path=str(out)).copied
    assert out.read_bytes() == video.read_bytes()


def test_sidecar_with_only_invalid_events_copies_video(
    video, make_sidecar, tmp_path, forbid_subprocess
):
    make_sidecar(video, [{"t": -1, "x": 0, "y": 0, "type": "in"}, "junk"])
    out = tmp_path / "out.webm"
    assert az.process_video(str(video), output_path=str(out)).copied


# ---------- --dry-run ----------


def test_dry_run_builds_command_without_ffmpeg_and_files(
    video, make_sidecar, spec_events, tmp_path, no_ffmpeg_installed
):
    make_sidecar(video, spec_events)
    out = tmp_path / "out.webm"
    result = az.process_video(
        str(video), output_path=str(out), ffmpeg="my-ffmpeg", dry_run=True
    )
    assert result.command[0] == "my-ffmpeg"
    assert result.command[-1] == str(out)
    assert "libvpx-vp9" in result.command
    assert "zoompan=" in result.command[result.command.index("-vf") + 1]
    assert not out.exists()


def test_cli_dry_run_prints_the_command(
    video, make_sidecar, spec_events, tmp_path, no_ffmpeg_installed, capsys
):
    make_sidecar(video, spec_events)
    code = az.main(["--video", str(video), "--dry-run", "--ffmpeg", "my-ffmpeg"])
    out = capsys.readouterr().out
    assert code == 0
    assert out.startswith("my-ffmpeg ") and "zoompan" in out and "libvpx-vp9" in out
    assert not (tmp_path / "clip.zoomed.webm").exists()


def test_cli_dry_run_without_events_says_video_would_be_copied(
    video, tmp_path, forbid_subprocess, capsys
):
    code = az.main(["--video", str(video), "--dry-run"])
    assert code == 0
    assert "would be copied unchanged" in capsys.readouterr().out
    assert not (tmp_path / "clip.zoomed.webm").exists()


# ---------- encoding (ffmpeg is changed) ----------


def test_encoding_goes_through_part_file_and_keeps_resolution(
    video, make_sidecar, spec_events, tmp_path, fake_ffmpeg
):
    make_sidecar(video, spec_events)
    out = tmp_path / "out.webm"
    result = az.process_video(str(video), output_path=str(out))
    cmd = fake_ffmpeg.calls[-1]
    assert not result.copied
    assert out.read_bytes() == b"encoded"
    assert not Path(str(out) + ".part").exists()
    assert cmd[-1] == str(out) + ".part"
    assert "s=1280x720" in cmd[cmd.index("-vf") + 1]


def test_ffmpeg_failure_raises_and_leaves_no_files(
    video, make_sidecar, spec_events, tmp_path, fake_ffmpeg
):
    def fail(cmd):
        Path(cmd[-1]).write_bytes(b"half")
        return SimpleNamespace(returncode=1, stdout="", stderr="boom")

    fake_ffmpeg.encode = fail
    make_sidecar(video, spec_events)
    out = tmp_path / "out.webm"
    with pytest.raises(az.FfmpegError, match="boom"):
        az.process_video(str(video), output_path=str(out))
    assert not out.exists()
    assert not Path(str(out) + ".part").exists()


def test_ffmpeg_timeout_is_reported(
    video, make_sidecar, spec_events, tmp_path, fake_ffmpeg
):
    def hang(cmd):
        raise subprocess.TimeoutExpired(cmd, 1)

    fake_ffmpeg.encode = hang
    make_sidecar(video, spec_events)
    with pytest.raises(az.FfmpegError, match="timed out"):
        az.process_video(str(video), output_path=str(tmp_path / "out.webm"), timeout=1)


def test_empty_ffmpeg_output_is_an_error(
    video, make_sidecar, spec_events, tmp_path, fake_ffmpeg
):
    def empty(cmd):
        Path(cmd[-1]).write_bytes(b"")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    fake_ffmpeg.encode = empty
    make_sidecar(video, spec_events)
    with pytest.raises(az.FfmpegError, match="empty"):
        az.process_video(str(video), output_path=str(tmp_path / "out.webm"))


def test_probe_size_parses_ffmpeg_output(fake_ffmpeg):
    assert az.probe_size("ffmpeg", "clip.webm") == (1280, 720)


def test_probe_size_returns_none_for_unknown_output(monkeypatch):
    monkeypatch.setattr(
        az.subprocess, "run", lambda *a, **k: SimpleNamespace(stderr="nothing")
    )
    assert az.probe_size("ffmpeg", "clip.webm") is None


# ---------- errors and params ----------


def test_missing_ffmpeg_gives_a_hint(video, make_sidecar, spec_events, tmp_path):
    make_sidecar(video, spec_events)
    with pytest.raises(az.FfmpegError, match="--ffmpeg"):
        az.process_video(
            str(video),
            output_path=str(tmp_path / "o.webm"),
            ffmpeg="definitely-not-ffmpeg",
        )


def test_cli_reports_errors_with_exit_code_1(video, make_sidecar, spec_events, capsys):
    make_sidecar(video, spec_events)
    assert az.main(["--video", str(video), "--ffmpeg", "definitely-not-ffmpeg"]) == 1
    assert "ERROR" in capsys.readouterr().err


def test_cli_missing_video_exits_with_1(tmp_path, capsys):
    assert az.main(["--video", str(tmp_path / "nope.webm")]) == 1
    assert "ERROR" in capsys.readouterr().err


def test_cli_requires_video_option():
    with pytest.raises(SystemExit):
        az.main([])


def test_broken_sidecar_raises_sidecar_error(video):
    Path(str(video) + ".zoom.json").write_text("{bad", encoding="utf-8")
    with pytest.raises(az.SidecarError):
        az.process_video(str(video))


def test_output_must_differ_from_input(video):
    with pytest.raises(ValueError):
        az.process_video(str(video), output_path=str(video))


def test_missing_video_and_missing_explicit_sidecar(video, tmp_path):
    with pytest.raises(FileNotFoundError):
        az.process_video(str(tmp_path / "nope.webm"))
    with pytest.raises(FileNotFoundError):
        az.process_video(str(video), sidecar_path=str(tmp_path / "nope.json"))


def test_default_output_path_is_zoomed_next_to_video():
    assert az.default_output_path("a/b/clip.webm") == "a/b/clip.zoomed.webm"


def test_ffmpeg_path_comes_from_env_by_default(monkeypatch):
    monkeypatch.setenv("FFMPEG_BIN", "/opt/ff")
    assert az.build_parser().parse_args(["--video", "x"]).ffmpeg == "/opt/ff"


def test_command_uses_vp9_webm_and_constant_frame_rate():
    cmd = az.build_command(
        "ff", "in.webm", "out.webm", "zoompan=z='1'", 25.0, (1280, 720)
    )
    vf = cmd[cmd.index("-vf") + 1]
    assert cmd[cmd.index("-c:v") + 1] == "libvpx-vp9"
    assert cmd[cmd.index("-f") + 1] == "webm"
    assert "-an" in cmd
    assert vf.split(",")[0] == "fps=25"
    assert vf.endswith(",format=yuv420p")


def test_format_command_quotes_awkward_arguments():
    assert (
        az.format_command(["ffmpeg", "-i", "my video.webm"])
        == 'ffmpeg -i "my video.webm"'
    )
