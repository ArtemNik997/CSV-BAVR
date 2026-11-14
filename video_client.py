import os
import time
from contextlib import ExitStack

import requests

ZOOM_SUFFIX = ".zoom.json"
FINAL_STATUSES = ("done", "failed")


def find_zoom_sidecar(video_path: str):
    path = video_path + ZOOM_SUFFIX
    return path if os.path.isfile(path) else None


def _http_error(response, action: str) -> Exception:
    return Exception(
        f"{action} failed with status {response.status_code}: {response.text}"
    )


def UploadVideoEx(server_url: str, video_path: str, company_name: str) -> dict:
    base_url = server_url.rstrip("/")

    if not os.path.isfile(video_path):
        raise FileNotFoundError(f"Video file not found: {video_path}")

    sidecar_path = find_zoom_sidecar(video_path)

    with ExitStack() as stack:
        files = {
            "file": (
                os.path.basename(video_path),
                stack.enter_context(open(video_path, "rb")),
                "video/webm",
            )
        }
        if sidecar_path:
            files["zoom"] = (
                os.path.basename(sidecar_path),
                stack.enter_context(open(sidecar_path, "rb")),
                "application/json",
            )
        response = requests.post(
            f"{base_url}/upload",
            files=files,
            data={"company_name": company_name},
            timeout=(10, 300),
        )

    if response.status_code != 200:
        raise _http_error(response, "Upload")

    return response.json()


def UploadVideo(server_url: str, video_path: str, company_name: str) -> str:
    return UploadVideoEx(server_url, video_path, company_name)["video_url"]


def GetVideoURL(server_url: str, company_name: str) -> str:
    base_url = server_url.rstrip("/")

    response = requests.get(
        f"{base_url}/get_video_url", params={"company_name": company_name}, timeout=10
    )

    if response.status_code == 404:
        raise LookupError(f"Company not found: {company_name}")

    response.raise_for_status()

    return response.json()["video_url"]


def ProcessVideo(server_url: str, job_id) -> dict:
    """Просит сервер применить zoom к загруженному видео. 409 (уже идёт) не ошибка."""
    base_url = server_url.rstrip("/")

    response = requests.post(f"{base_url}/jobs/{job_id}/process", timeout=10)

    if response.status_code == 404:
        raise LookupError(f"Job not found: {job_id}")
    if response.status_code not in (200, 202, 409):
        raise _http_error(response, "Process request")

    return response.json()


def GetJobStatus(server_url: str, job_id) -> dict:
    base_url = server_url.rstrip("/")

    response = requests.get(f"{base_url}/jobs/{job_id}", timeout=10)

    if response.status_code == 404:
        raise LookupError(f"Job not found: {job_id}")
    if response.status_code != 200:
        raise _http_error(response, "Job status request")

    return response.json()


def WaitForJob(
    server_url: str, job_id, timeout: float = 300.0, interval: float = 2.0
) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        job = GetJobStatus(server_url, job_id)
        if job.get("status") in FINAL_STATUSES:
            return job
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(
                f"Processing not finished after {timeout:g} s "
                f"(status: {job.get('status')})"
            )
        time.sleep(min(interval, remaining))
