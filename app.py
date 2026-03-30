from fastapi import FastAPI, Query, HTTPException, Response
import requests
import tempfile
import subprocess
import os

app = FastAPI()

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

            r = requests.get(url, stream=True, timeout=180)
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

            safe_name = filename.rsplit(".", 1)[0] + "_compressed.mp4"

            with open(output_path, "rb") as f:
                file_bytes = f.read()

            return Response(
                content=file_bytes,
                media_type="video/mp4",
                headers={
                    "Content-Disposition": f'attachment; filename="{safe_name}"'
                }
            )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
