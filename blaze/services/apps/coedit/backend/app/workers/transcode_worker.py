"""ARQ worker for background transcode jobs."""
from arq.connections import RedisSettings
from app.services.transcode_service import run_transcode_job


async def run_transcode(ctx, job_id: str, version_id: str, input_path: str, asset_dir: str, asset_type: str):
    """ARQ task wrapper — runs transcode in thread to avoid blocking."""
    import asyncio
    import concurrent.futures
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        concurrent.futures.ThreadPoolExecutor(max_workers=2),
        run_transcode_job, job_id, version_id, input_path, asset_dir, asset_type
    )


class WorkerSettings:
    functions = [run_transcode]
    redis_settings = RedisSettings()
    max_jobs = 2
    job_timeout = 3600  # 1 hour max per transcode
