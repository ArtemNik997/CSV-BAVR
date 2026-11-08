import os
import requests


def UploadVideo(server_url: str, video_path: str, company_name: str) -> str:
    base_url = server_url.rstrip("/")
    
    if not os.path.isfile(video_path):
        raise FileNotFoundError(f"Video file not found: {video_path}")
    
    filename = os.path.basename(video_path)
    with open(video_path, "rb") as f:
        response = requests.post(
            f"{base_url}/upload",
            files={"file": (filename, f, "video/webm")},
            data={"company_name": company_name},
            timeout=(10, 300)
        )
    
    if response.status_code != 200:
        raise Exception(
            f"Upload failed with status {response.status_code}: {response.text}"
        )
    
    return response.json()["video_url"]


def GetVideoURL(server_url: str, company_name: str) -> str:
    base_url = server_url.rstrip("/")
    
    response = requests.get(
        f"{base_url}/get_video_url",
        params={"company_name": company_name},
        timeout=10
    )
    
    if response.status_code == 404:
        raise LookupError(f"Company not found: {company_name}")
    
    response.raise_for_status()
    
    return response.json()["video_url"]