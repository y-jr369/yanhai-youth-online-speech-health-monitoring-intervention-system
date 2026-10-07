from __future__ import annotations

import asyncio
import threading
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.crawler.tieba_config import TIEBA_CRAWLER_DEFAULTS
from app.crawler.tieba_service import crawl_tieba_to_db, preview_tieba_threads
from app.database import SessionLocal, get_db
from app.display_filters import sanitize_source_label, sanitize_visible_text
from app.models import CrawlJob, CrawlSource


router = APIRouter(prefix="/api/crawler", tags=["crawler"])

MANUAL_REFRESH_TIMEOUT_SECONDS = 300
MANUAL_REFRESH_MAX_THREADS = 15000
STALE_RUNNING_JOB_MINUTES = 10


class TiebaPreviewRequest(BaseModel):
    tieba_name: str = TIEBA_CRAWLER_DEFAULTS["tieba_name"]
    source: str = TIEBA_CRAWLER_DEFAULTS["source"]
    source_label: str = "百度贴吧"
    max_threads: int = TIEBA_CRAWLER_DEFAULTS["max_threads"]
    headless: bool = TIEBA_CRAWLER_DEFAULTS["headless"]


class TiebaRunRequest(BaseModel):
    source_id: int | None = None
    tieba_name: str = TIEBA_CRAWLER_DEFAULTS["tieba_name"]
    source: str = TIEBA_CRAWLER_DEFAULTS["source"]
    source_label: str = "百度贴吧"
    max_threads: int = TIEBA_CRAWLER_DEFAULTS["max_threads"]
    headless: bool = TIEBA_CRAWLER_DEFAULTS["headless"]


def _duration_seconds(started_at: datetime | None, finished_at: datetime | None) -> float | None:
    if not started_at or not finished_at:
        return None
    return round((finished_at - started_at).total_seconds(), 2)


def _serialize_job(job: CrawlJob) -> dict:
    return {
        "id": job.id,
        "status": job.status,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "duration_seconds": _duration_seconds(job.started_at, job.finished_at),
        "summary": sanitize_visible_text(job.summary),
        "inserted_count": job.inserted_count,
        "duplicated_count": job.duplicated_count,
        "failed_count": job.failed_count,
        "total_threads": job.total_threads,
    }


def _resolve_manual_source(db: Session) -> CrawlSource | None:
    source = (
        db.query(CrawlSource)
        .filter(CrawlSource.enabled == True, CrawlSource.source_mode == "latest")  # noqa: E712
        .order_by(CrawlSource.updated_at.desc(), CrawlSource.id.desc())
        .first()
    )
    if source:
        return source

    source = (
        db.query(CrawlSource)
        .filter(CrawlSource.source_mode == "latest")
        .order_by(CrawlSource.updated_at.desc(), CrawlSource.id.desc())
        .first()
    )
    if source:
        return source

    return db.query(CrawlSource).order_by(CrawlSource.updated_at.desc(), CrawlSource.id.desc()).first()


def _serialize_manual_source(source: CrawlSource) -> dict:
    return {
        "id": source.id,
        "forum_name": sanitize_source_label(source.forum_name, source.forum_name),
        "source_mode": source.source_mode,
        "source_label": sanitize_source_label(source.source_label, source.forum_name),
        "max_threads": source.max_threads,
        "headless": source.headless,
    }


def _build_requested_summary(data: TiebaRunRequest) -> str:
    mode_label = {"latest": "最新", "hot": "热门"}.get(data.source, data.source)
    return f"已提交手动抓取任务：模式={mode_label}，计划抓取上限={data.max_threads}。"


def _build_manual_crawl_summary(result: dict) -> str:
    inserted = int(result.get("inserted") or 0)
    duplicated = int(result.get("duplicated") or 0)
    failed = int(result.get("failed") or 0)
    processed_threads = int(result.get("processed_threads") or result.get("total_threads") or 0)
    pages_visited = int(result.get("pages_visited") or 0)
    return (
        f"手动抓取完成：本次检查 {processed_threads} 条候选帖子，"
        f"新增 {inserted} 条，重复 {duplicated} 条，失败 {failed} 条，"
        f"访问列表页 {pages_visited} 页。"
    )


def _cleanup_stale_running_jobs(db: Session):
    stale_before = datetime.utcnow() - timedelta(minutes=STALE_RUNNING_JOB_MINUTES)
    stale_jobs = (
        db.query(CrawlJob)
        .filter(CrawlJob.status == "running", CrawlJob.started_at < stale_before)
        .all()
    )

    if not stale_jobs:
        return

    now = datetime.utcnow()
    for job in stale_jobs:
        job.status = "failed"
        job.finished_at = now
        job.summary = "旧的手动抓取任务长时间未完成，系统已自动结束，请重新点击手动更新抓取。"

    db.commit()


def _create_crawl_job(data: TiebaRunRequest, db: Session) -> CrawlJob:
    job = CrawlJob(
        source_id=data.source_id,
        job_type="manual",
        status="running",
        started_at=datetime.utcnow(),
        total_threads=data.max_threads,
        summary=_build_requested_summary(data),
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


async def _execute_tieba_run(data: TiebaRunRequest, db: Session, job: CrawlJob):
    source = None
    if data.source_id is not None:
        source = db.query(CrawlSource).filter(CrawlSource.id == data.source_id).first()
        if not source:
            raise HTTPException(status_code=404, detail="未找到对应的抓取源配置")

    started_at = job.started_at or datetime.utcnow()

    try:
        result = await crawl_tieba_to_db(
            db=db,
            tieba_name=data.tieba_name,
            source=data.source,
            max_threads=data.max_threads,
            headless=data.headless,
            source_label=data.source_label,
            source_id=data.source_id,
        )
    except Exception as exc:
        finished_at = datetime.utcnow()
        job.status = "failed"
        job.failed_count = int(data.max_threads or 0)
        job.finished_at = finished_at
        job.summary = f"手动抓取异常结束：{str(exc)[:180]}"
        db.commit()
        return {
            "ok": False,
            "message": str(exc),
            "inserted": 0,
            "duplicated": 0,
            "failed": int(data.max_threads or 0),
            "details": [],
            "job": _serialize_job(job),
        }

    finished_at = datetime.utcnow()
    job.status = "success" if result.get("ok") else "failed"
    job.inserted_count = int(result.get("inserted") or 0)
    job.duplicated_count = int(result.get("duplicated") or 0)
    job.failed_count = int(result.get("failed") or 0)
    job.total_threads = int(result.get("processed_threads") or result.get("total_threads") or data.max_threads or 0)
    job.finished_at = finished_at
    job.summary = _build_manual_crawl_summary(result) if result.get("ok") else (result.get("message") or "抓取失败")

    if source is not None:
        source.last_crawled_at = finished_at
        source.next_crawl_at = finished_at + timedelta(minutes=source.crawl_interval_minutes)

    db.commit()

    result["job"] = _serialize_job(job)
    result["job"]["started_at"] = started_at
    return result


def _run_manual_refresh_job(payload: dict, job_id: int):
    db = SessionLocal()
    try:
        data = TiebaRunRequest(**payload)
        job = db.query(CrawlJob).filter(CrawlJob.id == job_id).first()
        if not job:
            return

        try:
            asyncio.run(
                asyncio.wait_for(
                    _execute_tieba_run(data, db, job),
                    timeout=MANUAL_REFRESH_TIMEOUT_SECONDS,
                )
            )
        except asyncio.TimeoutError:
            db.rollback()
            job = db.query(CrawlJob).filter(CrawlJob.id == job_id).first()
            if job and job.status == "running":
                job.status = "failed"
                job.failed_count = int(data.max_threads or 0)
                job.finished_at = datetime.utcnow()
                job.summary = (
                    f"手动抓取超时：超过 {MANUAL_REFRESH_TIMEOUT_SECONDS} 秒仍未完成，"
                    "可能遇到验证码、网络波动或贴吧页面结构变化。"
                )
                db.commit()
        except Exception as exc:
            db.rollback()
            job = db.query(CrawlJob).filter(CrawlJob.id == job_id).first()
            if job and job.status == "running":
                job.status = "failed"
                job.failed_count = int(data.max_threads or 0)
                job.finished_at = datetime.utcnow()
                job.summary = f"手动抓取后台执行异常：{str(exc)[:180]}"
                db.commit()
    finally:
        db.close()


@router.post("/tieba/preview")
async def tieba_preview(data: TiebaPreviewRequest):
    return await preview_tieba_threads(
        tieba_name=data.tieba_name,
        source=data.source,
        max_threads=data.max_threads,
        headless=data.headless,
    )


@router.post("/tieba/run")
async def tieba_run(data: TiebaRunRequest, db: Session = Depends(get_db)):
    job = _create_crawl_job(data, db)
    return await _execute_tieba_run(data, db, job)


@router.post("/manual-refresh")
async def manual_refresh_posts(db: Session = Depends(get_db)):
    _cleanup_stale_running_jobs(db)

    source = _resolve_manual_source(db)
    if not source:
        raise HTTPException(status_code=404, detail="当前没有可用的抓取源配置")

    running_job = (
        db.query(CrawlJob)
        .filter(CrawlJob.job_type == "manual", CrawlJob.status == "running")
        .order_by(CrawlJob.started_at.desc(), CrawlJob.id.desc())
        .first()
    )
    if running_job:
        return {
            "ok": True,
            "accepted": False,
            "pending": True,
            "message": "已有手动抓取任务正在后台运行，请稍后查看结果。",
            "inserted": running_job.inserted_count,
            "duplicated": running_job.duplicated_count,
            "failed": running_job.failed_count,
            "job": _serialize_job(running_job),
            "manual_source": _serialize_manual_source(source),
        }

    payload = TiebaRunRequest(
        source_id=source.id,
        tieba_name=source.forum_name,
        source=source.source_mode,
        source_label=source.source_label or source.name or source.forum_name,
        max_threads=max(1, min(int(source.max_threads or 10), MANUAL_REFRESH_MAX_THREADS)),
        headless=True,
    )
    job = _create_crawl_job(payload, db)

    worker = threading.Thread(
        target=_run_manual_refresh_job,
        args=(payload.model_dump(), job.id),
        daemon=True,
    )
    worker.start()

    return {
        "ok": True,
        "accepted": True,
        "pending": True,
        "message": (
            "已开始后台抓取新帖子，页面会自动刷新抓取结果。"
            if payload.headless
            else "已开始后台抓取；如果弹出贴吧验证浏览器，请先完成验证，页面会自动刷新结果。"
        ),
        "inserted": 0,
        "duplicated": 0,
        "failed": 0,
        "job": _serialize_job(job),
        "manual_source": _serialize_manual_source(source),
    }
