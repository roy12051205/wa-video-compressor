from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import FileResponse
import requests
import tempfile
import subprocess
import os

app = FastAPI()

def run(cmd):
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr)
    return result.stdout

def get_duration(input_path: str) -> float:
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        input_path
    ]
    out = run(cmd).strip()
    return float(out)

@app.get("/")
def root():
    return {"ok": True, "message": "compressor running"}

@app.get("/compress")
def compress(
    url: str = Query(...),
    filename: str = Query("video.mp4"),
    target_mb: int = Query(15)
):
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = os.path.join(tmpdir, "input.mp4")
            output_path = os.path.join(tmpdir, "output.mp4")

            r = requests.get(url, stream=True, timeout=120)
            if r.status_code != 200:
                raise HTTPException(status_code=400, detail="Could not download source video")

            with open(input_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        f.write(chunk)

            duration = get_duration(input_path)
            if duration <= 0:
                raise HTTPException(status_code=400, detail="Invalid video duration")

            target_bytes = target_mb * 1024 * 1024
            audio_bitrate = 64000
            overhead = 32000
            total_bitrate = int((target_bytes * 8) / duration)
            video_bitrate = max(total_bitrate - audio_bitrate - overhead, 200000)

            cmd = [
                "ffmpeg", "-y",
                "-i", input_path,
                "-vf", "scale='min(720,iw)':-2",
                "-c:v", "libx264",
                "-preset", "veryfast",
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
                    "ffmpeg", "-y",
                    "-i", input_path,
                    "-vf", "scale='min(540,iw)':-2",
                    "-c:v", "libx264",
                    "-preset", "veryfast",
                    "-crf", "32",
                    "-c:a", "aac",
                    "-b:a", "48k",
                    "-movflags", "+faststart",
                    output_path
                ]
                run(cmd2)

            safe_name = filename.rsplit(".", 1)[0] + "_compressed.mp4"
            return FileResponse(output_path, media_type="video/mp4", filename=safe_name)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
