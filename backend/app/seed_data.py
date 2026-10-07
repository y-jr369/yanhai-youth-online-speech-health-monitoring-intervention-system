from datetime import datetime, timedelta

from sqlalchemy import or_
from sqlalchemy.orm import Session

from .models import (
    AlertRecord,
    AnalysisResult,
    ArchiveRecord,
    CrawlSource,
    InterventionRecord,
    Post,
    User,
)

LEGACY_DEMO_POST_IDS = {"seed-1001", "seed-1002", "seed-1003", "seed-1004"}
LEGACY_DEMO_POST_URL_PREFIX = "https://example.com/posts/"
DEMO_POST_PREFIX = "demo-tieba-"
DEMO_POST_URL_PREFIX = "https://tieba.baidu.com/p/demo-"

TARGET_FORUM_NAME = "\u5927\u5b66"
TARGET_SOURCE_LABEL = "\u6821\u56ed\u8bba\u575b"

DEFAULT_SOURCES = [
    {
        "name": "\u6821\u56ed\u8bba\u575b-\u6700\u65b0",
        "forum_name": TARGET_FORUM_NAME,
        "source_mode": "latest",
        "source_label": TARGET_SOURCE_LABEL,
        "description": "默认监测最新公开帖子",
        "enabled": True,
        "crawl_interval_minutes": 60,
        "max_threads": 1000,
        "headless": False,
        "next_crawl_at_offset_minutes": 0,
    },
    {
        "name": "\u6821\u56ed\u8bba\u575b-\u70ed\u95e8",
        "forum_name": TARGET_FORUM_NAME,
        "source_mode": "hot",
        "source_label": TARGET_SOURCE_LABEL,
        "description": "监测热门帖，适合发现扩散较快的风险言论",
        "enabled": False,
        "crawl_interval_minutes": 60,
        "max_threads": 150,
        "headless": False,
        "next_crawl_at_offset_minutes": 10,
    },
]


def _delete_posts_with_relations(db: Session, posts: list[Post]) -> None:
    if not posts:
        return

    post_ids = [post.id for post in posts]
    analysis_ids = [
        analysis_id
        for (analysis_id,) in db.query(AnalysisResult.id).filter(AnalysisResult.post_id_fk.in_(post_ids)).all()
    ]

    if analysis_ids:
        alert_ids = [
            alert_id
            for (alert_id,) in db.query(AlertRecord.id).filter(AlertRecord.analysis_id.in_(analysis_ids)).all()
        ]
        if alert_ids:
            db.query(ArchiveRecord).filter(ArchiveRecord.alert_id.in_(alert_ids)).delete(synchronize_session=False)
            db.query(InterventionRecord).filter(InterventionRecord.alert_id.in_(alert_ids)).delete(synchronize_session=False)
            db.query(AlertRecord).filter(AlertRecord.id.in_(alert_ids)).delete(synchronize_session=False)

        db.query(AnalysisResult).filter(AnalysisResult.id.in_(analysis_ids)).delete(synchronize_session=False)

    db.query(Post).filter(Post.id.in_(post_ids)).delete(synchronize_session=False)
    db.commit()


def _cleanup_demo_posts(db: Session) -> None:
    demo_posts = (
        db.query(Post)
        .filter(
            or_(
                Post.post_id.in_(LEGACY_DEMO_POST_IDS),
                Post.post_url.like(f"{LEGACY_DEMO_POST_URL_PREFIX}%"),
                Post.post_id.like(f"{DEMO_POST_PREFIX}%"),
                Post.post_url.like(f"{DEMO_POST_URL_PREFIX}%"),
            )
        )
        .all()
    )
    _delete_posts_with_relations(db, demo_posts)


def _upsert_default_sources(db: Session) -> None:
    now = datetime.utcnow()

    for source_config in DEFAULT_SOURCES:
        source = db.query(CrawlSource).filter(CrawlSource.name == source_config["name"]).first()
        next_crawl_at = now + timedelta(minutes=source_config["next_crawl_at_offset_minutes"])

        if source is None:
            db.add(
                CrawlSource(
                    name=source_config["name"],
                    forum_name=source_config["forum_name"],
                    source_mode=source_config["source_mode"],
                    source_label=source_config["source_label"],
                    description=source_config["description"],
                    enabled=source_config["enabled"],
                    crawl_interval_minutes=source_config["crawl_interval_minutes"],
                    max_threads=source_config["max_threads"],
                    headless=source_config["headless"],
                    next_crawl_at=next_crawl_at,
                )
            )
            continue

        source.name = source_config["name"]
        source.forum_name = source_config["forum_name"]
        source.source_mode = source_config["source_mode"]
        source.source_label = source_config["source_label"]
        source.description = source_config["description"]
        source.enabled = source_config["enabled"]
        source.crawl_interval_minutes = source_config["crawl_interval_minutes"]
        source.max_threads = source_config["max_threads"]
        source.headless = source_config["headless"]
        if source.last_crawled_at is None:
            source.next_crawl_at = next_crawl_at

    db.commit()


def seed_database(db: Session) -> None:
    _cleanup_demo_posts(db)

    if not db.query(User).filter(User.username == "admin").first():
        db.add(User(username="admin", password="123456", role="admin"))
    demo_user = db.query(User).filter(User.username == "18656641007").first()
    if demo_user is None:
        db.add(User(username="18656641007", password="yjr18656641007", role="admin"))
    else:
        demo_user.password = "yjr18656641007"
        demo_user.role = "admin"
    db.commit()

    _upsert_default_sources(db)
