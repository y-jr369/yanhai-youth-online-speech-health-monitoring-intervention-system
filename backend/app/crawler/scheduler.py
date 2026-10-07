import asyncio
import logging
from datetime import datetime, timedelta

from app.database import SessionLocal
from app.models import CrawlJob, CrawlSource
from app.crawler.tieba_service import crawl_tieba_to_db


logger = logging.getLogger(__name__)
_SOURCE_RUN_LOCKS: dict[int, asyncio.Lock] = {}


def _get_crawl_error_message(exc: Exception) -> str:
    text = str(exc)
    if "Executable doesn't exist" in text and "ms-playwright" in text:
        return "Playwright 浏览器未安装，已跳过本轮采集。请执行: python -m playwright install chromium"
    return text


def _get_source_lock(source_id: int) -> asyncio.Lock:
    lock = _SOURCE_RUN_LOCKS.get(source_id)
    if lock is None:
        lock = asyncio.Lock()
        _SOURCE_RUN_LOCKS[source_id] = lock
    return lock


async def _crawl_with_fallback(db, source):
    attempts = [source.headless]
    if source.headless:
        # Scheduled jobs often fail on captcha in headless mode. Retry once in headed mode.
        attempts.append(False)

    last_result = None
    for headless in attempts:
        result = await crawl_tieba_to_db(
            db=db,
            tieba_name=source.forum_name,
            source=source.source_mode,
            max_threads=source.max_threads,
            headless=headless,
            source_label=source.source_label or source.name,
            source_id=source.id,
        )
        last_result = result
        message = str(result.get("message", ""))
        if result.get("ok") or "验证码" not in message:
            return result

    return last_result or {
        "ok": False,
        "message": "采集失败",
        "inserted": 0,
        "duplicated": 0,
        "failed": 0,
        "total_threads": 0,
    }


async def run_source_job(source_id: int, job_type: str = "scheduled", force: bool = False) -> None:
    lock = _get_source_lock(source_id)
    async with lock:
        await _run_source_job_locked(source_id, job_type=job_type, force=force)


async def _run_source_job_locked(source_id: int, job_type: str = "scheduled", force: bool = False) -> None:
    db = SessionLocal()
    source = None
    job = None
    try:
        source = db.query(CrawlSource).filter(CrawlSource.id == source_id).first()
        if not source or not source.enabled:
            return

        now = datetime.utcnow()
        if not force and source.next_crawl_at and source.next_crawl_at > now:
            return

        job = CrawlJob(source_id=source.id, job_type=job_type, status="running", started_at=now)
        db.add(job)
        db.commit()
        db.refresh(job)

        result = await _crawl_with_fallback(db=db, source=source)

        job_ok = bool(result.get("ok"))
        if job_ok:
            source.last_crawled_at = now
            source.next_crawl_at = now + timedelta(minutes=source.crawl_interval_minutes)
        else:
            source.next_crawl_at = datetime.utcnow() + timedelta(minutes=max(source.crawl_interval_minutes, 10))

        job.status = "success" if job_ok else "failed"
        job.inserted_count = result.get("inserted", 0)
        job.duplicated_count = result.get("duplicated", 0)
        job.failed_count = result.get("failed", 0)
        job.total_threads = result.get("total_threads", 0)
        job.summary = result.get("message", "")
        job.finished_at = datetime.utcnow()
        db.commit()
    except Exception as exc:
        message = _get_crawl_error_message(exc)
        if message != str(exc):
            logger.warning("Scheduled crawl skipped for source_id=%s: %s", source_id, message)
        else:
            logger.exception("Scheduled crawl failed for source_id=%s", source_id)
        if job:
            job.status = "failed"
            job.summary = message
            job.finished_at = datetime.utcnow()
        if source:
            source.next_crawl_at = datetime.utcnow() + timedelta(minutes=max(source.crawl_interval_minutes, 10))
        db.commit()
    finally:
        db.close()


async def run_enabled_sources_once(job_type: str = "startup") -> None:
    db = SessionLocal()
    try:
        source_ids = [
            source_id
            for (source_id,) in (
                db.query(CrawlSource.id)
                .filter(CrawlSource.enabled == True)  # noqa: E712
                .order_by(CrawlSource.id.asc())
                .all()
            )
        ]
    finally:
        db.close()

    for source_id in source_ids:
        await run_source_job(source_id, job_type=job_type, force=True)


async def scheduler_loop(stop_event: asyncio.Event):
    while not stop_event.is_set():
        db = SessionLocal()
        try:
            now = datetime.utcnow()
            due_sources = (
                db.query(CrawlSource)
                .filter(CrawlSource.enabled == True)  # noqa: E712
                .filter(
                    (CrawlSource.next_crawl_at.is_(None)) |
                    (CrawlSource.next_crawl_at <= now)
                )
                .order_by(CrawlSource.next_crawl_at.asc().nullsfirst(), CrawlSource.id.asc())
                .all()
            )
            source_ids = [source.id for source in due_sources]
        finally:
            db.close()

        for source_id in source_ids:
            if stop_event.is_set():
                break
            await run_source_job(source_id, job_type="scheduled")

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=30)
        except asyncio.TimeoutError:
            continue
