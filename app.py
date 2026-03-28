from fastapi import FastAPI, Query, HTTPException, Response
import requests
import tempfile
import subprocess
import os

app = FastAPI()

WA_TOKEN = os.getenv("WA_TOKEN", "")
WA_PHONE_NUMBER_ID = os.getenv("WA_PHONE_NUMBER_ID", "")
WA_API_VERSION = os.getenv("WA_API_VERSION", "v23.0")

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

def compress_video_bytes(url: str, filename: str, target_mb: int = 15):
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

        with open(output_path, "rb") as f:
            file_bytes = f.read()

        return file_bytes, safe_name

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
        file_bytes, safe_name = compress_video_bytes(url, filename, target_mb)
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

@app.post("/prepare-whatsapp")
def prepare_whatsapp(
    url: str = Query(...),
    filename: str = Query("video.mp4"),
    target_mb: int = Query(15)
):
    try:
        if not WA_TOKEN or not WA_PHONE_NUMBER_ID:
            raise HTTPException(status_code=500, detail="Missing WA_TOKEN or WA_PHONE_NUMBER_ID in Railway variables")

        file_bytes, safe_name = compress_video_bytes(url, filename, target_mb)

        upload_url = f"https://graph.facebook.com/{WA_API_VERSION}/{WA_PHONE_NUMBER_ID}/media"
        headers = {
            "Authorization": f"Bearer {WA_TOKEN}"
        }

        files = {
            "file": (safe_name, file_bytes, "video/mp4")
        }

        data = {
            "messaging_product": "whatsapp",
            "type": "video/mp4"
        }

        resp = requests.post(upload_url, headers=headers, files=files, data=data, timeout=180)

        try:
            resp_json = resp.json()
        except Exception:
            resp_json = {"raw": resp.text}

        if resp.status_code >= 400:
            raise HTTPException(status_code=resp.status_code, detail=resp_json)

        media_id = resp_json.get("id")
        if not media_id:
            raise HTTPException(status_code=500, detail={"error": "No media_id returned", "response": resp_json})

        return {
            "success": True,
            "media_id": media_id,
            "file_name": safe_name
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
