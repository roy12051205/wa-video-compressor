from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import Response
import requests
import tempfile
import subprocess
import os
import boto3

app = FastAPI()

R2_ACCOUNT_ID = os.getenv("1181711be60b6fcaa2e19486bf348da3", "")
R2_ACCESS_KEY_ID = os.getenv("8244ddc8041efe0b82cab031bbf44303", "")
R2_SECRET_ACCESS_KEY = os.getenv("d893c3e2713a624594d2b0ed9ea7e654c85fab941c564b5935dfc4e60977422e", "")
R2_BUCKET_NAME = os.getenv("watch-videos", "")
R2_PUBLIC_BASE_URL = os.getenv("https://pub-58af6cf789dd4673a343681b7cb2e8c0.r2.dev", "")

def run(cmd):
    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr)
    return result

def get_duration(input_path: str) -> float:
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        input_path
    ]
    result = run(cmd)
    return float(result.stdout.strip())

def compress_video(input_path: str, output_path: str, target_mb: int):
    duration = get_duration(input_path)
    if duration <= 0:
        raise HTTPException(status_code=400, detail="Invalid video duration")

    target_bytes = target_mb * 1024 * 1024
    audio_bitrate = 64000
    overhead = 32000
    total_bitrate = int((target_bytes * 8) / duration)
    video_bitrate = max(total_bitrate - audio_bitrate - overhead, 200000)

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-y",
        "-i", input_path,
        "-vf", "scale='min(720,iw)':-2",
        "-r", "30",
        "-threads", "0",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-b:v", str(video_bitrate),
        "-maxrate", str(video_bitrate),
        "-bufsize", str(video_bitrate * 2),
        "-c:a", "aac",
        "-b:a", "64k",
        "-movflags", "+faststart",
        output_path
    ]
    run(cmd)

    if os.path.getsize(output_path) > 16 * 1024 * 1024:
        cmd2 = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "error",
            "-y",
            "-i", input_path,
            "-vf", "scale='min(540,iw)':-2",
            "-r", "24",
            "-threads", "0",
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-crf", "32",
            "-c:a", "aac",
            "-b:a", "48k",
            "-movflags", "+faststart",
            output_path
        ]
        run(cmd2)

@app.get("/")
def root():
    return {"ok": True, "message": "compressor running"}

@app.get("/compress-to-r2")
def compress_to_r2(
    url: str = Query(...),
    filename: str = Query("video.mp4"),
    target_mb: int = Query(15)
):
    try:
        if not all([R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET_NAME, R2_PUBLIC_BASE_URL]):
            raise HTTPException(status_code=500, detail="Missing R2 environment variables")

        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = os.path.join(tmpdir, "input.mp4")
            output_path = os.path.join(tmpdir, "output.mp4")

            # download original
            r = requests.get(url, stream=True, timeout=180)
            if r.status_code != 200:
                raise HTTPException(status_code=400, detail="Could not download source video")

            with open(input_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        f.write(chunk)

            # compress
            compress_video(input_path, output_path, target_mb)

            base_name = filename.rsplit(".", 1)[0]
            compressed_name = f"{base_name}_compressed.mp4"

            # upload to R2
            s3 = boto3.client(
                "s3",
                endpoint_url=f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
                aws_access_key_id=R2_ACCESS_KEY_ID,
                aws_secret_access_key=R2_SECRET_ACCESS_KEY,
                region_name="auto"
            )

            s3.upload_file(
                output_path,
                R2_BUCKET_NAME,
                compressed_name,
                ExtraArgs={"ContentType": "video/mp4"}
            )

            compressed_url = f"{R2_PUBLIC_BASE_URL}/{compressed_name}"

            return {
                "success": True,
                "compressed_file_name": compressed_name,
                "compressed_url": compressed_url
            }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    }
