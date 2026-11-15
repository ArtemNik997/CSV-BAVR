import os
import re
import subprocess
import time
import uuid
from pathlib import Path
from urllib.parse import unquote

import pytest
import requests

from video_client import GetJobStatus, ProcessVideo, UploadVideoEx, WaitForJob

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_DOCKER_TESTS") != "1",
        reason="set RUN_DOCKER_TESTS=1 (requires Docker)",
    ),
]

ROOT = Path(__file__).resolve().parents[1]
BASE = os.environ.get("SERVER_URL", "http://localhost:8000")
PROCESS_TIMEOUT = float(os.environ.get("PROCESS_TIMEOUT", "300"))
DB_USER = os.environ.get("POSTGRES_USER", "video_user")
DB_NAME = os.environ.get("POSTGRES_DB", "videos")


def dc(*args, check=True):
    return subprocess.run(
        ["docker", "compose", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=check,
    )


def wait_for_api(timeout=180):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if requests.get(f"{BASE}/videos", timeout=3).status_code == 200:
                return
        except requests.RequestException:
            pass
        time.sleep(2)
    pytest.fail(f"service did not become ready at {BASE}")


def company():
    return "it-" + uuid.uuid4().hex[:8]


@pytest.fixture(scope="module")
def stack():
    started = False
    try:
        requests.get(f"{BASE}/videos", timeout=3).raise_for_status()
    except requests.RequestException:
        dc("up", "-d", "--build")
        started = True
        wait_for_api()
    yield
    if started:
        dc("down")


def make_clip(path):
    dc(
        "exec",
        "-T",
        "web",
        "ffmpeg",
        "-y",
        "-v",
        "error",
        "-f",
        "lavfi",
        "-i",
        "testsrc=size=1280x720:rate=25:duration=3",
        "-c:v",
        "libvpx-vp9",
        "-crf",
        "40",
        "-b:v",
        "0",
        "-f",
        "webm",
        "/tmp/it_clip.webm",
    )
    dc("cp", "web:/tmp/it_clip.webm", str(path))


@pytest.fixture(scope="module")
def processed_job(stack, tmp_path_factory):
    folder = tmp_path_factory.mktemp("it")
    clip = folder / "clip.webm"
    make_clip(clip)
    Path(str(clip) + ".zoom.json").write_text(
        '{"viewport":{"width":1280,"height":720},"fps":25,"zoom_factor":2.0,"ramp_sec":0.5,'
        '"events":[{"t":0.5,"x":640,"y":360,"type":"in"},{"t":2.0,"x":640,"y":360,"type":"out"}]}',
        encoding="utf-8",
    )
    info = UploadVideoEx(BASE, str(clip), company())
    if info["process_status"] == "none":  # AUTO_PROCESS=0
        ProcessVideo(BASE, info["id"])
    job = WaitForJob(BASE, info["id"], timeout=PROCESS_TIMEOUT, interval=1.0)
    assert job["status"] == "done", job.get("error")
    return {"id": info["id"], "job": job}


def test_web_image_has_ffmpeg_with_vp9(stack):
    out = dc("exec", "-T", "web", "ffmpeg", "-hide_banner", "-encoders").stdout
    assert "libvpx-vp9" in out


def test_processed_video_is_served(processed_job):
    r = requests.get(processed_job["job"]["processed_video_url"], timeout=30)
    assert r.status_code == 200
    assert len(r.content) > 0


def test_result_is_stored_in_postgres(processed_job):
    sql = f"select processed_video_url from videos where id={int(processed_job['id'])}"
    out = dc(
        "exec", "-T", "db", "psql", "-U", DB_USER, "-d", DB_NAME, "-tAc", sql
    ).stdout
    assert out.strip() == processed_job["job"]["processed_video_url"]


def test_processed_video_keeps_the_input_resolution(processed_job):
    name = unquote(processed_job["job"]["processed_video_url"].rsplit("/", 1)[1])
    probe = dc(
        "exec",
        "-T",
        "web",
        "ffmpeg",
        "-hide_banner",
        "-i",
        f"/app/uploads/processed/{name}",
        check=False,
    )
    m = re.search(r"Video:.*?,\s*(\d{2,5})x(\d{2,5})", probe.stderr)
    assert m and (int(m.group(1)), int(m.group(2))) == (1280, 720)


def test_video_without_zoom_is_copied_unchanged(stack, tmp_path):
    clip = tmp_path / "plain.webm"
    clip.write_bytes(b"not a real video, copy path never starts ffmpeg")
    info = UploadVideoEx(BASE, str(clip), company())
    assert info["process_status"] == "none"
    ProcessVideo(BASE, info["id"])
    job = WaitForJob(BASE, info["id"], timeout=60, interval=1.0)
    assert job["status"] == "done", job.get("error")
    assert (
        requests.get(job["processed_video_url"], timeout=30).content
        == clip.read_bytes()
    )


def test_invalid_zoom_sidecar_is_rejected(stack):
    r = requests.post(
        f"{BASE}/upload",
        files={
            "file": ("x.webm", b"123", "video/webm"),
            "zoom": ("bad.json", b"{not json", "application/json"),
        },
        data={"company_name": company()},
        timeout=30,
    )
    assert r.status_code == 400
    assert "zoom" in r.json()["error"].lower()


def test_unknown_job_returns_404(stack):
    assert requests.get(f"{BASE}/jobs/99999999", timeout=10).status_code == 404
    assert requests.post(f"{BASE}/jobs/99999999/process", timeout=10).status_code == 404


def test_data_survives_down_and_up(processed_job):
    dc("down")
    dc("up", "-d")
    wait_for_api()
    job = GetJobStatus(BASE, processed_job["id"])
    assert job["status"] == "done"
    assert requests.get(job["processed_video_url"], timeout=30).status_code == 200
