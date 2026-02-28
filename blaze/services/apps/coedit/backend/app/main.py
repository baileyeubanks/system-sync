from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from app.database import init_db
from app.routes import auth, projects, assets, comments, share, notifications, ws, briefs

app = FastAPI(title="Co-Edit", version="0.2.0", docs_url="/api/docs", redoc_url=None)

# CORS — allow frontend dev server and production
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://blaze.taildcd0ef.ts.net", "http://localhost:5173", "http://127.0.0.1:5173", "https://contentco-op.com", "https://www.contentco-op.com", "https://content-co-op.netlify.app"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routes
app.include_router(auth.router)
app.include_router(projects.router)
app.include_router(assets.router)
app.include_router(comments.router)
app.include_router(share.router)
app.include_router(notifications.router)
app.include_router(ws.router)
app.include_router(briefs.router)


@app.on_event("startup")
async def startup():
    init_db()


@app.get("/api/health")
async def health():
    return {"status": "ok", "app": "Co-Edit", "version": "0.2.0"}


@app.get("/api/transcode/{job_id}")
async def transcode_status(job_id: str):
    from app.database import get_db
    db = await get_db()
    try:
        row = await db.execute("SELECT * FROM transcode_jobs WHERE id = ?", (job_id,))
        job = await row.fetchone()
        if not job:
            return {"status": "not_found"}
        return dict(job)
    finally:
        await db.close()


# Serve frontend static files in production
frontend_dist = Path(__file__).parent.parent.parent / "frontend" / "dist"
if frontend_dist.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="frontend")
