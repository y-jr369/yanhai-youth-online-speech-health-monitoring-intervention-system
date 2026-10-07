import argparse
import asyncio
import json
from datetime import datetime

from app.crawler.tieba_service import crawl_tieba_to_db
from app.database import SessionLocal
from app.models import CrawlJob, CrawlSource


def ensure_source(db, forum_name: str, source_mode: str, max_threads: int, headless: bool):
    source = (
        db.query(CrawlSource)
        .filter(CrawlSource.forum_name == forum_name, CrawlSource.source_mode == source_mode)
        .first()
    )
    if source:
        source.max_threads = max_threads
        source.headless = headless
        source.enabled = True
        source.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(source)
        return source

    source = CrawlSource(
        name=f"{forum_name}_{source_mode}",
        forum_name=forum_name,
        source_mode=source_mode,
        source_label=f"{forum_name}吧",
        enabled=True,
        max_threads=max_threads,
        headless=headless,
    )
    db.add(source)
    db.commit()
    db.refresh(source)
    return source


def main():
    parser = argparse.ArgumentParser(description="Crawl one Tieba forum into the database")
    parser.add_argument("forum_name", help="Tieba forum name")
    parser.add_argument("source_mode", nargs="?", default="latest", choices=["latest", "hot", "good"])
    parser.add_argument("max_threads", nargs="?", type=int, default=20)
    parser.add_argument("--headless", action="store_true", help="Run browser in headless mode")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        source = ensure_source(
            db=db,
            forum_name=args.forum_name,
            source_mode=args.source_mode,
            max_threads=args.max_threads,
            headless=args.headless,
        )

        job = CrawlJob(
            source_id=source.id,
            job_type="manual",
            status="running",
            summary="Manual Tieba crawl",
        )
        db.add(job)
        db.commit()
        db.refresh(job)

        result = asyncio.run(
            crawl_tieba_to_db(
                db=db,
                tieba_name=args.forum_name,
                source=args.source_mode,
                max_threads=args.max_threads,
                headless=args.headless,
                source_label=source.source_label or f"{args.forum_name}吧",
                source_id=source.id,
            )
        )

        source.last_crawled_at = datetime.utcnow()
        job.status = "finished" if result.get("ok") else "failed"
        job.inserted_count = result.get("inserted", 0)
        job.duplicated_count = result.get("duplicated", 0)
        job.failed_count = result.get("failed", 0)
        job.total_threads = result.get("total_threads", 0)
        job.summary = json.dumps(result, ensure_ascii=False)
        job.finished_at = datetime.utcnow()
        db.commit()

        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        db.close()


if __name__ == "__main__":
    main()
