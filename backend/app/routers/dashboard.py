from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from ..database import get_db
from ..demo_scope import apply_demo_post_scope, demo_post_filter
from ..display_filters import sanitize_source_label, sanitize_visible_text, should_hide_post
from ..models import AlertRecord, AnalysisResult, ArchiveRecord, CrawlJob, CrawlSource, Post
from ..utils import make_excerpt


router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

RISK_LEVEL_DISPLAY = {
    "low": "低风险",
    "medium": "中风险",
    "high": "高风险",
}


def _duration_seconds(started_at: datetime | None, finished_at: datetime | None) -> float | None:
    if not started_at or not finished_at:
        return None
    return round((finished_at - started_at).total_seconds(), 2)


def _normalize_source_name(post: Post) -> str:
    return sanitize_source_label(post.source, post.forum_name)


def _visible_demo_posts(db: Session) -> list[Post]:
    return [post for post in apply_demo_post_scope(db.query(Post)).all() if not should_hide_post(post)]


@router.get("/overview")
def overview(db: Session = Depends(get_db)):
    now = datetime.utcnow()
    demo_post_ids = [post.id for post in _visible_demo_posts(db)]

    total_posts = len(demo_post_ids)
    total_alerts = (
        db.query(AlertRecord)
        .join(AnalysisResult, AnalysisResult.id == AlertRecord.analysis_id)
        .filter(AnalysisResult.post_id_fk.in_(demo_post_ids))
        .count()
        if demo_post_ids
        else 0
    )
    high_risk = (
        db.query(AnalysisResult)
        .filter(AnalysisResult.post_id_fk.in_(demo_post_ids), AnalysisResult.risk_level == "high")
        .count()
        if demo_post_ids
        else 0
    )
    archived = (
        db.query(ArchiveRecord)
        .join(AlertRecord, AlertRecord.id == ArchiveRecord.alert_id)
        .join(AnalysisResult, AnalysisResult.id == AlertRecord.analysis_id)
        .filter(AnalysisResult.post_id_fk.in_(demo_post_ids))
        .count()
        if demo_post_ids
        else 0
    )
    pending = (
        db.query(AlertRecord)
        .join(AnalysisResult, AnalysisResult.id == AlertRecord.analysis_id)
        .filter(AnalysisResult.post_id_fk.in_(demo_post_ids), AlertRecord.alert_status == "pending")
        .count()
        if demo_post_ids
        else 0
    )
    active_sources = db.query(CrawlSource).filter(CrawlSource.enabled == True).count()  # noqa: E712
    jobs_last_24h = db.query(CrawlJob).filter(CrawlJob.started_at >= now - timedelta(hours=24)).count()
    success_jobs = db.query(CrawlJob).filter(CrawlJob.status == "success").count()
    total_jobs = db.query(CrawlJob).count()
    success_rate = round((success_jobs / total_jobs) * 100, 1) if total_jobs else 0.0

    return {
        "total_posts": total_posts,
        "total_alerts": total_alerts,
        "high_risk": high_risk,
        "archived": archived,
        "pending": pending,
        "active_sources": active_sources,
        "jobs_last_24h": jobs_last_24h,
        "crawl_success_rate": success_rate,
    }


@router.get("/risk-distribution")
def risk_distribution(db: Session = Depends(get_db)):
    demo_post_ids = [post.id for post in _visible_demo_posts(db)]
    if not demo_post_ids:
        return []

    rows = (
        db.query(AnalysisResult.risk_level, func.count(AnalysisResult.id))
        .filter(AnalysisResult.post_id_fk.in_(demo_post_ids))
        .group_by(AnalysisResult.risk_level)
        .all()
    )
    return [
        {"name": RISK_LEVEL_DISPLAY.get(level, level), "value": count, "key": level}
        for level, count in rows
    ]


@router.get("/source-ranking")
def source_ranking(db: Session = Depends(get_db)):
    counts: dict[str, int] = {}
    for post in _visible_demo_posts(db):
        source_name = _normalize_source_name(post)
        counts[source_name] = counts.get(source_name, 0) + 1

    return [
        {"source": source, "count": count}
        for source, count in sorted(counts.items(), key=lambda item: item[1], reverse=True)
    ]


@router.get("/recent-alerts")
def recent_alerts(db: Session = Depends(get_db)):
    alerts = (
        db.query(AlertRecord)
        .join(AnalysisResult, AnalysisResult.id == AlertRecord.analysis_id)
        .join(Post, Post.id == AnalysisResult.post_id_fk)
        .filter(demo_post_filter())
        .options(joinedload(AlertRecord.analysis).joinedload(AnalysisResult.post))
        .order_by(AlertRecord.created_at.desc())
        .all()
    )

    data = []
    for alert in alerts:
        post = alert.analysis.post
        if should_hide_post(post):
            continue
        data.append(
            {
                "alert_id": alert.id,
                "status": alert.alert_status,
                "risk_level": alert.analysis.risk_level,
                "risk_level_display": RISK_LEVEL_DISPLAY.get(alert.analysis.risk_level, "低风险"),
                "title": post.title or post.post_id,
                "excerpt": post.excerpt or make_excerpt(post.content, 80),
                "source": _normalize_source_name(post),
                "created_at": alert.created_at,
            }
        )
        if len(data) >= 8:
            break
    return data


@router.get("/crawl-health")
def crawl_health(db: Session = Depends(get_db)):
    sources = db.query(CrawlSource).order_by(CrawlSource.updated_at.desc()).all()
    jobs = db.query(CrawlJob).order_by(CrawlJob.started_at.desc()).limit(10).all()

    return {
        "sources": [
            {
                "id": source.id,
                "name": sanitize_source_label(source.name, source.forum_name),
                "source_label": sanitize_source_label(source.source_label, source.forum_name),
                "enabled": source.enabled,
                "forum_name": sanitize_source_label(source.forum_name, source.forum_name),
                "source_mode": source.source_mode,
                "last_crawled_at": source.last_crawled_at,
                "next_crawl_at": source.next_crawl_at,
                "max_threads": source.max_threads,
            }
            for source in sources
        ],
        "jobs": [
            {
                "id": job.id,
                "source_id": job.source_id,
                "job_type": job.job_type,
                "status": job.status,
                "inserted_count": job.inserted_count,
                "duplicated_count": job.duplicated_count,
                "failed_count": job.failed_count,
                "started_at": job.started_at,
                "finished_at": job.finished_at,
                "duration_seconds": _duration_seconds(job.started_at, job.finished_at),
                "summary": sanitize_visible_text(job.summary),
            }
            for job in jobs
        ],
    }
