from __future__ import annotations

import re


GENERIC_FORUM_NAME = "校园论坛"
UNKNOWN_SOURCE_NAME = "未知来源"
DISPLAY_FORUM_ALIASES = {
    "\u5927\u5b66": GENERIC_FORUM_NAME,
    "\u5927\u5b66\u5427": GENERIC_FORUM_NAME,
}
HIDDEN_SCHOOL_TERMS = (
    "示例高校",
    "示例高校",
    "示例高校",
    "本校",
    "本校",
    "尼理",
    "本校",
    "尼本校",
)
HIDDEN_SCHOOL_PATTERN = re.compile(
    "|".join(re.escape(term) for term in sorted(HIDDEN_SCHOOL_TERMS, key=len, reverse=True))
)


def _normalize_text(value: object | None) -> str:
    return str(value or "").strip()


def contains_hidden_school_text(*values: object | None) -> bool:
    for value in values:
        text = _normalize_text(value)
        if text and HIDDEN_SCHOOL_PATTERN.search(text):
            return True
    return False


def sanitize_visible_text(value: object | None, replacement: str = GENERIC_FORUM_NAME) -> str:
    text = _normalize_text(value)
    if not text:
        return ""
    return HIDDEN_SCHOOL_PATTERN.sub(replacement, text)


def sanitize_source_label(source: object | None = None, forum_name: object | None = None) -> str:
    source_text = _normalize_text(source)
    forum_text = _normalize_text(forum_name)
    if source_text in DISPLAY_FORUM_ALIASES:
        return DISPLAY_FORUM_ALIASES[source_text]
    if forum_text in DISPLAY_FORUM_ALIASES:
        return DISPLAY_FORUM_ALIASES[forum_text]
    if contains_hidden_school_text(source_text, forum_text):
        return GENERIC_FORUM_NAME
    if source_text:
        return source_text
    if forum_text:
        return forum_text
    return UNKNOWN_SOURCE_NAME


def sanitize_forum_name(forum_name: object | None) -> str:
    text = _normalize_text(forum_name)
    if not text:
        return ""
    if text in DISPLAY_FORUM_ALIASES:
        return DISPLAY_FORUM_ALIASES[text]
    if contains_hidden_school_text(text):
        return GENERIC_FORUM_NAME
    return text


def should_hide_post_content(*values: object | None) -> bool:
    return contains_hidden_school_text(*values)


def should_hide_post(post) -> bool:
    return should_hide_post_content(
        getattr(post, "title", None),
        getattr(post, "excerpt", None),
        getattr(post, "content", None),
    )
