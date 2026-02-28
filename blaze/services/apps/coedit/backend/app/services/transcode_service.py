import asyncio
import json
import subprocess
import time
import sqlite3
import redis
from pathlib import Path

from app.config import (
    FFMPEG, FFPROBE, DB_PATH, REDIS_URL,
    TRANSCODE_VIDEO_BITRATE, TRANSCODE_MAX_BITRATE,
    TRANSCODE_AUDIO_BITRATE, TRANSCODE_FPS
)


def _publish_progress(job_id, progress, status="processing"):
    """Sync publish transcode progress to Redis."""
    try:
        r = redis.from_url(REDIS_URL)
        r.publish("transcode:{}".format(job_id), json.dumps({
            "type": "transcode_progress",
            "job_id": job_id,
            "progress": progress,
            "status": status,
        }))
        r.close()
    except Exception:
        pass


def probe_media(input_path):
    """Get media info via ffprobe."""
    cmd = [
        FFPROBE, "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height,r_frame_rate,codec_name,duration",
        "-show_entries", "format=duration,size",
        "-of", "json",
        input_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        return {}
    data = json.loads(result.stdout)
    info = {}
    if data.get("streams"):
        s = data["streams"][0]
        info["width"] = s.get("width")
        info["height"] = s.get("height")
        info["codec"] = s.get("codec_name")
        fps_str = s.get("r_frame_rate", "24/1")
        if "/" in fps_str:
            num, den = fps_str.split("/")
            info["fps"] = float(num) / float(den) if float(den) > 0 else 24.0
        else:
            info["fps"] = float(fps_str)
    if data.get("format"):
        fmt = data["format"]
        dur = fmt.get("duration") or (data["streams"][0].get("duration") if data.get("streams") else None)
        if dur:
            info["duration_ms"] = int(float(dur) * 1000)
        info["file_size"] = int(fmt.get("size", 0))
    return info


def transcode_video(input_path, output_path, thumbnail_path, job_id=None):
    """Transcode to H.264 via VideoToolbox (Apple Silicon HW accel)."""
    # Get duration for progress tracking
    duration_s = None
    info = probe_media(input_path)
    if info.get("duration_ms"):
        duration_s = info["duration_ms"] / 1000.0

    cmd = [
        FFMPEG, "-i", input_path,
        "-c:v", "h264_videotoolbox",
        "-b:v", TRANSCODE_VIDEO_BITRATE,
        "-maxrate", TRANSCODE_MAX_BITRATE,
        "-bufsize", "20M",
        "-profile:v", "high",
        "-level", "4.1",
        "-pix_fmt", "yuv420p",
        "-r", str(TRANSCODE_FPS),
        "-vsync", "cfr",
        "-c:a", "aac", "-b:a", TRANSCODE_AUDIO_BITRATE,
        "-movflags", "+faststart",
        "-progress", "pipe:1",
        "-y", output_path
    ]

    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    # Parse progress from stdout
    last_progress = 0
    for line in process.stdout:
        line = line.strip()
        if line.startswith("out_time_us="):
            try:
                us = int(line.split("=")[1])
                if duration_s and duration_s > 0:
                    pct = min(95, int((us / 1_000_000) / duration_s * 100))
                    if pct > last_progress:
                        last_progress = pct
                        if job_id:
                            _publish_progress(job_id, pct)
            except (ValueError, ZeroDivisionError):
                pass

    process.wait(timeout=3600)
    if process.returncode != 0:
        stderr = process.stderr.read() if process.stderr else ""
        raise RuntimeError("FFmpeg transcode failed: {}".format(stderr[:500]))

    # Generate thumbnail from first frame
    thumb_cmd = [
        FFMPEG, "-i", output_path,
        "-vf", "select=eq(n\\,0)",
        "-frames:v", "1",
        "-y", thumbnail_path
    ]
    subprocess.run(thumb_cmd, capture_output=True, text=True, timeout=60)

    # Generate sprite sheet for scrubber preview (1 frame every 2s, 160px wide)
    sprite_path = str(Path(output_path).parent / "sprite.jpg")
    try:
        sprite_cmd = [
            FFMPEG, "-i", output_path,
            "-vf", "fps=0.5,scale=160:-1,tile=10x1",
            "-frames:v", "1",
            "-q:v", "5",
            "-y", sprite_path
        ]
        result = subprocess.run(sprite_cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            sprite_path = None
    except Exception:
        sprite_path = None

    return True, sprite_path


def _process_image(conn, job_id, version_id, input_path, asset_dir):
    """Process image: extract dimensions, generate thumbnail."""
    _publish_progress(job_id, 10, "processing")
    try:
        from PIL import Image
        img = Image.open(input_path)
        width, height = img.size
        img_format = img.format or "JPEG"

        # Set up version dir
        row = conn.execute("SELECT version_num FROM asset_versions WHERE id = ?", (version_id,)).fetchone()
        v_num = row[0] if row else 1
        v_dir = Path(asset_dir) / "versions" / "v{}".format(v_num)
        v_dir.mkdir(parents=True, exist_ok=True)

        # Generate thumbnail (max 400px wide)
        thumb_path = str(v_dir / "thumb.jpg")
        thumb = img.copy()
        if thumb.mode in ("RGBA", "P"):
            thumb = thumb.convert("RGB")
        thumb.thumbnail((400, 400))
        thumb.save(thumb_path, "JPEG", quality=85)
        img.close()

        _publish_progress(job_id, 80)

        conn.execute("""
            UPDATE asset_versions SET
                proxy_path = ?, thumbnail_path = ?,
                width = ?, height = ?,
                codec = ?
            WHERE id = ?
        """, (input_path, thumb_path, width, height, img_format.lower(), version_id))

    except Exception:
        # Fallback: no thumbnail, still mark ready
        conn.execute("""
            UPDATE asset_versions SET proxy_path = ? WHERE id = ?
        """, (input_path, version_id))

    conn.execute(
        "UPDATE transcode_jobs SET status = 'completed', progress = 100, completed_at = ? WHERE id = ?",
        (time.time(), job_id)
    )
    conn.execute(
        "UPDATE assets SET status = 'ready', updated_at = ? WHERE id = (SELECT asset_id FROM asset_versions WHERE id = ?)",
        (time.time(), version_id)
    )
    conn.commit()
    _publish_progress(job_id, 100, "completed")


def _process_audio(conn, job_id, version_id, input_path, asset_dir):
    """Process audio: extract duration via ffprobe."""
    _publish_progress(job_id, 10, "processing")

    info = probe_media(input_path)
    duration_ms = info.get("duration_ms")

    # Set up version dir for thumbnail placeholder
    row = conn.execute("SELECT version_num FROM asset_versions WHERE id = ?", (version_id,)).fetchone()
    v_num = row[0] if row else 1

    _publish_progress(job_id, 50)

    conn.execute("""
        UPDATE asset_versions SET
            proxy_path = ?, duration_ms = ?, codec = ?
        WHERE id = ?
    """, (input_path, duration_ms, "audio", version_id))

    conn.execute(
        "UPDATE transcode_jobs SET status = 'completed', progress = 100, completed_at = ? WHERE id = ?",
        (time.time(), job_id)
    )
    conn.execute(
        "UPDATE assets SET status = 'ready', updated_at = ? WHERE id = (SELECT asset_id FROM asset_versions WHERE id = ?)",
        (time.time(), version_id)
    )
    conn.commit()
    _publish_progress(job_id, 100, "completed")


def _process_pdf(conn, job_id, version_id, input_path, asset_dir):
    """Process PDF: generate first-page thumbnail if possible."""
    _publish_progress(job_id, 10, "processing")

    row = conn.execute("SELECT version_num FROM asset_versions WHERE id = ?", (version_id,)).fetchone()
    v_num = row[0] if row else 1
    v_dir = Path(asset_dir) / "versions" / "v{}".format(v_num)
    v_dir.mkdir(parents=True, exist_ok=True)

    thumb_path = None

    # Try to count pages and generate thumbnail via ffmpeg (works for some PDFs)
    try:
        thumb_out = str(v_dir / "thumb.jpg")
        cmd = [
            FFMPEG, "-i", input_path,
            "-frames:v", "1",
            "-y", thumb_out
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0 and Path(thumb_out).exists():
            thumb_path = thumb_out
    except Exception:
        pass

    _publish_progress(job_id, 80)

    conn.execute("""
        UPDATE asset_versions SET
            proxy_path = ?, thumbnail_path = ?, codec = ?
        WHERE id = ?
    """, (input_path, thumb_path, "pdf", version_id))

    conn.execute(
        "UPDATE transcode_jobs SET status = 'completed', progress = 100, completed_at = ? WHERE id = ?",
        (time.time(), job_id)
    )
    conn.execute(
        "UPDATE assets SET status = 'ready', updated_at = ? WHERE id = (SELECT asset_id FROM asset_versions WHERE id = ?)",
        (time.time(), version_id)
    )
    conn.commit()
    _publish_progress(job_id, 100, "completed")


def run_transcode_job(job_id, version_id, input_path, asset_dir, asset_type):
    """Synchronous transcode job (called by ARQ worker or thread pool)."""
    conn = sqlite3.connect(str(DB_PATH))
    try:
        now = time.time()
        conn.execute(
            "UPDATE transcode_jobs SET status = 'processing', started_at = ? WHERE id = ?",
            (now, job_id)
        )
        conn.commit()
        _publish_progress(job_id, 0, "processing")

        if asset_type == "image":
            _process_image(conn, job_id, version_id, input_path, asset_dir)
            return

        if asset_type == "audio":
            _process_audio(conn, job_id, version_id, input_path, asset_dir)
            return

        if asset_type == "pdf":
            _process_pdf(conn, job_id, version_id, input_path, asset_dir)
            return

        # Probe original
        info = probe_media(input_path)
        _publish_progress(job_id, 5)

        # Set up output paths
        version_dir = Path(asset_dir) / "versions"
        row = conn.execute("SELECT version_num FROM asset_versions WHERE id = ?", (version_id,)).fetchone()
        v_num = row[0] if row else 1
        v_dir = version_dir / "v{}".format(v_num)
        v_dir.mkdir(parents=True, exist_ok=True)

        proxy_path = str(v_dir / "proxy.mp4")
        thumb_path = str(v_dir / "thumb.jpg")

        # Transcode with progress
        _result = transcode_video(input_path, proxy_path, thumb_path, job_id=job_id)
        sprite_path = _result[1] if isinstance(_result, tuple) and len(_result) > 1 else None
        _publish_progress(job_id, 98)

        # Probe transcoded for accurate fps
        proxy_info = probe_media(proxy_path)

        # Update version with metadata
        conn.execute("""
            UPDATE asset_versions SET
                proxy_path = ?, thumbnail_path = ?, sprite_path = ?,
                duration_ms = ?, width = ?, height = ?,
                fps = ?, codec = ?
            WHERE id = ?
        """, (
            proxy_path, thumb_path, sprite_path,
            info.get("duration_ms"), info.get("width"), info.get("height"),
            proxy_info.get("fps", info.get("fps", TRANSCODE_FPS)),
            "h264",
            version_id
        ))

        conn.execute(
            "UPDATE transcode_jobs SET status = 'completed', progress = 100, completed_at = ? WHERE id = ?",
            (time.time(), job_id)
        )
        conn.execute(
            "UPDATE assets SET status = 'ready', updated_at = ? WHERE id = (SELECT asset_id FROM asset_versions WHERE id = ?)",
            (time.time(), version_id)
        )
        conn.commit()
        _publish_progress(job_id, 100, "completed")

    except Exception as e:
        conn.execute(
            "UPDATE transcode_jobs SET status = 'failed', error = ?, completed_at = ? WHERE id = ?",
            (str(e)[:500], time.time(), job_id)
        )
        conn.execute(
            "UPDATE assets SET status = 'error', updated_at = ? WHERE id = (SELECT asset_id FROM asset_versions WHERE id = ?)",
            (time.time(), version_id)
        )
        conn.commit()
        _publish_progress(job_id, 0, "failed")
        raise
    finally:
        conn.close()


async def enqueue_transcode(job_id, version_id, input_path, asset_dir, asset_type):
    """Enqueue a transcode job via ARQ."""
    try:
        from arq import create_pool
        from arq.connections import RedisSettings
        pool = await create_pool(RedisSettings())
        await pool.enqueue_job(
            "run_transcode",
            job_id, version_id, input_path, asset_dir, asset_type
        )
        await pool.close()
    except Exception:
        import concurrent.futures
        loop = asyncio.get_event_loop()
        loop.run_in_executor(
            concurrent.futures.ThreadPoolExecutor(max_workers=2),
            run_transcode_job, job_id, version_id, input_path, asset_dir, asset_type
        )
