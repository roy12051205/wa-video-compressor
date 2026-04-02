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

@app.get("/")
def root():
    return {"ok": True, "message": "preview compressor running"}

@app.get("/compress")
def compress(
    url: str = Query(...),
    filename: str = Query("video.mp4"),
    target_mb: int = Query(4)
):
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            input_ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "mp4"
            input_path = os.path.join(tmpdir, f"input.{input_ext}")
            output_path = os.path.join(tmpdir, "output.mp4")

            # 3.9 MB limit
            target_limit_mb = max(float(target_mb) - 0.1, 0.5)
            target_limit_bytes = int(target_limit_mb * 1024 * 1024)

            # download original video
            r = requests.get(url, stream=True, timeout=180)
            if r.status_code != 200:
                raise HTTPException(status_code=400, detail="Could not download source video")

            with open(input_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        f.write(chunk)

            # ---------------------------------------------------
            # PASS 1: first trim 10 sec, light quality
            # If this is already <= 3.9 MB, return this directly
            # ---------------------------------------------------
            cmd = [
                "ffmpeg",
                "-hide_banner",
                "-loglevel", "error",
                "-y",
                "-i", input_path,
                "-t", "10",
                "-vf", "scale='min(480,iw)':-2",
                "-r", "24",
                "-threads", "0",
                "-c:v", "libx264",
                "-preset", "ultrafast",
                "-crf", "28",
                "-c:a", "aac",
                "-b:a", "64k",
                "-movflags", "+faststart",
                output_path
            ]
            run(cmd)

            # ---------------------------------------------------
            # PASS 2: if still bigger than 3.9 MB, compress more
            # ---------------------------------------------------
            if os.path.getsize(output_path) > target_limit_bytes:
                cmd2 = [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel", "error",
                    "-y",
                    "-i", input_path,
                    "-t", "10",
                    "-vf", "scale='min(360,iw)':-2",
                    "-r", "20",
                    "-threads", "0",
                    "-c:v", "libx264",
                    "-preset", "ultrafast",
                    "-crf", "34",
                    "-c:a", "aac",
                    "-b:a", "32k",
                    "-movflags", "+faststart",
                    output_path
                ]
                run(cmd2)

            # ---------------------------------------------------
            # PASS 3: if still bigger than 3.9 MB, compress harder
            # ---------------------------------------------------
            if os.path.getsize(output_path) > target_limit_bytes:
                cmd3 = [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel", "error",
                    "-y",
                    "-i", input_path,
                    "-t", "10",
                    "-vf", "scale='min(240,iw)':-2",
                    "-r", "18",
                    "-threads", "0",
                    "-c:v", "libx264",
                    "-preset", "ultrafast",
                    "-crf", "38",
                    "-c:a", "aac",
                    "-b:a", "24k",
                    "-movflags", "+faststart",
                    output_path
                ]
                run(cmd3)

            safe_name = filename.rsplit(".", 1)[0] + "_preview.mp4"

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

@app.get("/normalize-phone")
def normalize_phone(
    contact_phone: str = "",
    shipping_phone: str = "",
    billing_phone: str = ""
):
    raw = contact_phone or shipping_phone or billing_phone

    raw = raw.strip()
    raw = raw.replace(" ", "").replace("-", "").replace("(", "").replace(")", "").replace("+", "")

    if not raw:
        return {"success": False, "final_phone": ""}

    final_phone = "+91" + raw[-10:]

    return {
        "success": True,
        "final_phone": final_phone
    }
