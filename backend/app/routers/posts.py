from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload

from ..database import get_db
from ..demo_scope import apply_demo_post_scope, is_demo_post
from ..display_filters import sanitize_forum_name, sanitize_source_label, should_hide_post
from ..models import Post
from ..utils import make_excerpt


router = APIRouter(prefix="/api/posts", tags=["posts"])

RISK_LEVEL_DISPLAY = {
    "low": "低风险",
    "medium": "中风险",
    "high": "高风险",
}

LABEL_DISPLAY = {
    "violence": "暴力威胁",
    "abuse": "辱骂冲突",
    "anxiety": "焦虑压力",
    "depression": "抑郁绝望",
    "self_harm": "自伤自杀倾向",
    "academic_stress": "学业压力",
    "social_withdrawal": "社交退缩",
    "emotional_distress": "情绪困扰",
    "normal": "正常",
}


def _display_labels(labels: list[str]) -> list[str]:
    return [LABEL_DISPLAY.get(label, label) for label in labels]


def _display_source(post: Post) -> str:
    return sanitize_source_label(post.source, post.forum_name)


def _serialize_post_view(post: Post) -> dict:
    analysis = post.analysis
    label_values = [item for item in (analysis.labels.split(",") if analysis and analysis.labels else ["normal"]) if item]
    risk_level = (analysis.risk_level or "low") if analysis else "low"
    matched_keywords = [
        item for item in (analysis.matched_keywords.split(",") if analysis and analysis.matched_keywords else []) if item
    ]

    return {
        "id": post.id,
        "post_id": post.post_id,
        "source_id": post.source_id,
        "source": _display_source(post),
        "source_platform": post.source_platform,
        "forum_name": sanitize_forum_name(post.forum_name),
        "title": post.title,
        "excerpt": post.excerpt or make_excerpt(post.content),
        "content": post.content,
        "author_name": post.author_name,
        "publish_time": post.publish_time,
        "collected_at": post.collected_at,
        "post_url": post.post_url,
        "labels": label_values,
        "labels_display": _display_labels(label_values),
        "risk_level": risk_level,
        "risk_level_display": RISK_LEVEL_DISPLAY.get(risk_level, "低风险"),
        "risk_score": analysis.risk_score if analysis else 0,
        "matched_keywords": matched_keywords,
        "confidence": analysis.confidence if analysis else 0,
        "suggestion": analysis.suggestion if analysis else "",
    }


@router.get("")
def get_posts(
    risk_level: str | None = None,
    source_id: int | None = None,
    keyword: str | None = None,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=15000),
    db: Session = Depends(get_db),
):
    query = apply_demo_post_scope(
        db.query(Post).options(joinedload(Post.analysis)).order_by(Post.publish_time.desc())
    )

    if source_id:
        query = query.filter(Post.source_id == source_id)

    if keyword:
        like_value = f"%{keyword.strip()}%"
        query = query.filter(
            or_(
                Post.title.like(like_value),
                Post.content.like(like_value),
                Post.author_name.like(like_value),
            )
        )

    posts = [post for post in query.all() if not should_hide_post(post)]
    serialized = [_serialize_post_view(post) for post in posts]

    if risk_level:
        serialized = [item for item in serialized if item["risk_level"] == risk_level]

    total = len(serialized)
    items = serialized[skip : skip + limit]
    return {
        "total": total,
        "items": items,
    }


@router.get("/{post_id}")
def get_post_detail(post_id: int, db: Session = Depends(get_db)):
    post = (
        db.query(Post)
        .options(joinedload(Post.analysis))
        .filter(Post.id == post_id)
        .first()
    )
    if not post or not is_demo_post(post) or should_hide_post(post):
        raise HTTPException(status_code=404, detail="帖子不存在")
    return _serialize_post_view(post)
