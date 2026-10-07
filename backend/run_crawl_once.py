import argparse
import asyncio
import json
from datetime import datetime, timedelta

from app.crawler.tieba_service import crawl_tieba_to_db
from app.database import SessionLocal
from app.models import CrawlJob, CrawlSource


def run_enabled_sources(limit: int | None = None):
    db = SessionLocal()
    try:
        query = db.query(CrawlSource).filter(CrawlSource.enabled.is_(True)).order_by(CrawlSource.id.asc())
        sources = query.limit(limit).all() if limit else query.all()
        results = []

        for source in sources:
            job = CrawlJob(
                source_id=source.id,
                job_type="scheduled",
                status="running",
                summary=f"Batch crawl: {source.forum_name}",
            )
            db.add(job)
            db.commit()
            db.refresh(job)

            result = asyncio.run(
                crawl_tieba_to_db(
                    db=db,
                    tieba_name=source.forum_name,
                    source=source.source_mode,
                    max_threads=source.max_threads,
                    headless=source.headless,
                    source_label=source.source_label or f"{source.forum_name}吧",
                    source_id=source.id,
                )
            )

            source.last_crawled_at = datetime.utcnow()
            source.next_crawl_at = datetime.utcnow() + timedelta(minutes=source.crawl_interval_minutes or 60)

            job.status = "finished" if result.get("ok") else "failed"
            job.inserted_count = result.get("inserted", 0)
            job.duplicated_count = result.get("duplicated", 0)
            job.failed_count = result.get("failed", 0)
            job.total_threads = result.get("total_threads", 0)
            job.summary = json.dumps(result, ensure_ascii=False)
            job.finished_at = datetime.utcnow()
            db.commit()

            results.append(
                {
                    "source_id": source.id,
                    "forum_name": source.forum_name,
                    "source_mode": source.source_mode,
                    "result": result,
                }
            )

        print(json.dumps({"count": len(results), "results": results}, ensure_ascii=False, indent=2))
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(description="Run one crawl round for enabled Tieba sources")
    parser.add_argument("--limit", type=int, default=None, help="Process only the first N enabled sources")
    args = parser.parse_args()
    run_enabled_sources(limit=args.limit)


if __name__ == "__main__":
    main()
