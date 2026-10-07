import argparse
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from app.database import SessionLocal
from app.models import AlertRecord, AnalysisResult, Post
from app.texts import build_intervention_suggestion
from app.utils import analyze_post_text, risk_priority_from_level

# ── AI辅助生成 · DeepSeek V4, 2026-04-28 ──

BATCH_DEFAULT = 100


def _ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _log(msg: str) -> None:
    print(f"[{_ts()}] {msg}", flush=True)


def _to_csv_text(items) -> str:
    return ",".join(items) if isinstance(items, list) else str(items or "")


def _bulk_load_related(
    db, post_ids: List[int]
) -> Tuple[Dict[int, AnalysisResult], Dict[int, AlertRecord]]:
    """批量预加载已有分析结果与告警，消除 N+1 查询。"""
    analyses = (
        db.query(AnalysisResult)
        .filter(AnalysisResult.post_id_fk.in_(post_ids))
        .all()
    )
    analysis_map: Dict[int, AnalysisResult] = {a.post_id_fk: a for a in analyses}

    analysis_ids = [a.id for a in analyses]
    alert_map: Dict[int, AlertRecord] = {}
    if analysis_ids:
        alerts = (
            db.query(AlertRecord)
            .filter(AlertRecord.analysis_id.in_(analysis_ids))
            .all()
        )
        alert_map = {al.analysis_id: al for al in alerts}

    return analysis_map, alert_map


def main(
    batch_size: int = BATCH_DEFAULT,
    limit: Optional[int] = None,
    dry_run: bool = False,
) -> dict:

    _log("阶段 1/5: 连接数据库...")
    db = SessionLocal()
    db.expire_on_commit = False  
    updated = 0
    created = 0
    removed = 0
    errors = 0
    t_start = time.perf_counter()

    try:
        _log("阶段 2/5: 加载帖子列表...")
        query = db.query(Post).order_by(Post.id)
        if limit:
            query = query.limit(limit)
        posts: List[Post] = query.all()
        total = len(posts)

        if total == 0:
            _log("未找到帖子，退出。")
            return {"total_posts": 0}

        _log(f"已加载 {total} 条帖子 | batch_size={batch_size}"
             f"{' | DRY-RUN' if dry_run else ''}")

        _log("阶段 3/5: 批量预加载已有分析结果与告警...")
        t_load = time.perf_counter()
        post_ids = [p.id for p in posts]
        analysis_map, alert_map = _bulk_load_related(db, post_ids)
        _log(f"已加载 {len(analysis_map)} 条分析结果 + {len(alert_map)} 条告警"
             f" | 耗时 {time.perf_counter() - t_load:.2f}s")

        _log(f"阶段 4/5: 开始逐条推理 (共 {total} 条)...")
        t_batch = time.perf_counter()

        for idx, post in enumerate(posts, start=1):
            try:
                labels, risk_level, score, matched_keywords, confidence = analyze_post_text(
                    post.title,
                    post.content,
                )

                analysis = analysis_map.get(post.id)
                if analysis is None:
                    analysis = AnalysisResult(post_id_fk=post.id)
                    db.add(analysis)

                analysis.labels = _to_csv_text(labels)
                analysis.risk_level = risk_level
                analysis.risk_score = score
                analysis.matched_keywords = _to_csv_text(matched_keywords)
                analysis.confidence = confidence
                analysis.suggestion = build_intervention_suggestion(
                    risk_level,
                    labels if isinstance(labels, list) else [labels],
                )
                if not analysis.created_at:
                    analysis.created_at = datetime.utcnow()
                updated += 1

                alert = alert_map.get(analysis.id) if analysis.id is not None else None
                if risk_level in {"medium", "high"}:
                    if alert is None:
                        alert = AlertRecord(
                            analysis=analysis,
                            alert_status="pending",
                            priority=risk_priority_from_level(risk_level),
                            review_note="Alert rebuilt after analysis recalculation",
                        )
                        db.add(alert)
                        created += 1
                    else:
                        alert.priority = risk_priority_from_level(risk_level)
                else:
                    if alert is not None:
                        db.delete(alert)
                        removed += 1

                if idx % batch_size == 0 or idx == total:
                    if not dry_run:
                        db.commit()
                    pct = idx / total * 100
                    _log(
                        f"[{idx}/{total} {pct:.1f}%] "
                        f"更新:{updated} 新增告警:{created} "
                        f"移除告警:{removed} 错误:{errors} "
                        f"本批耗时:{time.perf_counter() - t_batch:.1f}s"
                    )
                    t_batch = time.perf_counter()

            except Exception as exc:
                errors += 1
                try:
                    db.rollback()
                except Exception:
                    pass
                _log(f"  ⚠ 跳过 帖子 ID={post.id} (post_id={post.post_id!r}): {exc}")

        t_total = time.perf_counter() - t_start
        _log(f"阶段 5/5: 重载完成 | 总耗时 {t_total:.1f}s")

        result = {
            "total_posts": total,
            "updated_analyses": updated,
            "created_alerts": created,
            "removed_alerts": removed,
            "errors": errors,
            "elapsed_seconds": round(t_total, 2),
            "posts_per_second": round(total / t_total, 2) if t_total > 0 else 0,
        }
        _log(str(result))
        return result

    finally:
        db.close()
        _log("数据库连接已关闭。")


# ── AI辅助生成 · DeepSeek V4, 2026-04-28 : CLI 入口 ──
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="重新计算全量帖子的分析结果与告警"
    )
    parser.add_argument(
        "--batch-size", type=int, default=BATCH_DEFAULT,
        help=f"每批提交的记录数 (默认: {BATCH_DEFAULT})",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="限制处理的帖子数量 (默认: 全部)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="仅模拟运行，不实际写入数据库",
    )
    args = parser.parse_args()
    main(batch_size=args.batch_size, limit=args.limit, dry_run=args.dry_run)
