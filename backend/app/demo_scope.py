from __future__ import annotations

from sqlalchemy import or_
from sqlalchemy.orm import Query

from .models import Post


DEMO_FORUM_NAME = "示例高校"
DEMO_SOURCE_KEYWORD = "示例高校"
VISIBLE_FORUM_NAMES = {DEMO_FORUM_NAME, "\u5927\u5b66"}
VISIBLE_SOURCE_KEYWORDS = {DEMO_SOURCE_KEYWORD, "\u5927\u5b66\u5427"}


def demo_post_filter():
    return or_(
        Post.forum_name.in_(VISIBLE_FORUM_NAMES),
        *[Post.source.like(f"%{keyword}%") for keyword in VISIBLE_SOURCE_KEYWORDS],
    )


def apply_demo_post_scope(query: Query) -> Query:
    return query.filter(demo_post_filter())


def is_demo_post(post: Post | None) -> bool:
    if post is None:
        return False
    forum_name = str(post.forum_name or "").strip()
    source = str(post.source or "").strip()
    return forum_name in VISIBLE_FORUM_NAMES or any(keyword in source for keyword in VISIBLE_SOURCE_KEYWORDS)
