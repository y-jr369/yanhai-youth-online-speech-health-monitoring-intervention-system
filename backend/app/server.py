from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.database import Base, SessionLocal, engine
from app.routers import alerts, archive, auth, dashboard, intervention, posts
from app.routers import crawler as crawler_router
from app.seed_data import seed_database
from app.models import CrawlJob


BASE_DIR = Path(__file__).resolve().parents[2]
FRONTEND_DIR = BASE_DIR / "frontend"
ASSETS_DIR = FRONTEND_DIR / "assets"
HTML_PAGES = {
    "login.html",
    "dashboard.html",
    "posts.html",
    "alerts.html",
    "archive.html",
    "intervention.html",
}


def _html_response(page_name: str) -> FileResponse:
    return FileResponse(
        FRONTEND_DIR / page_name,
        headers={"Cache-Control": "no-store"},
    )


app = FastAPI(title="言海瞭望青少年网络言论健康监测与干预系统")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

Base.metadata.create_all(bind=engine)

if ASSETS_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(ASSETS_DIR)), name="assets")


@app.on_event("startup")
async def startup():
    db = SessionLocal()
    try:
        seed_database(db)
        stale_jobs = db.query(CrawlJob).filter(CrawlJob.status == "running").all()
        for job in stale_jobs:
            job.status = "failed"
            job.summary = "服务启动时发现旧的未完成抓取任务，已自动结束，请重新点击手动更新抓取。"
        if stale_jobs:
            db.commit()
    finally:
        db.close()


app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(posts.router)
app.include_router(alerts.router)
app.include_router(archive.router)
app.include_router(intervention.router)
app.include_router(crawler_router.router)


@app.get("/", include_in_schema=False)
def root():
    return _html_response("login.html")


@app.get("/{page_name}", include_in_schema=False)
def serve_page(page_name: str):
    if page_name not in HTML_PAGES:
        raise HTTPException(status_code=404, detail="Page not found")
    return _html_response(page_name)
