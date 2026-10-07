import asyncio
import hashlib
import json
import logging
import math
import re
from datetime import datetime
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup
from dateutil import parser as dt_parser
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright
from sqlalchemy.orm import Session

from app.crawler.tieba_config import TIEBA_CRAWLER_DEFAULTS
from app.models import AlertRecord, AnalysisResult, ArchiveRecord, InterventionRecord, Post
from app.texts import build_intervention_suggestion
from app.utils import analyze_post_text, make_excerpt, risk_priority_from_level

logger = logging.getLogger(__name__)

POST_URL_PATTERN = re.compile(r"/p/(\d+)")
THREAD_CARD_SELECTOR = ".top-thread-card-item, .thread-card-wrapper"
THREAD_LIST_READY_SELECTOR = ".frs-feed-list .thread-card-wrapper, .frs-feed-list .top-thread-card-item"
THREAD_ENTRY_URL_HINTS = ("/c/f/frs/page_pc", "/c/f/frs/page", "/f?kw=")
IGNORED_NETWORK_URL_KEYWORDS = (
    "homeSidebarLeft",
    "homeSidebarRight",
    "frsSidebarRight",
    "frsBottom",
    "adNewLog",
    "passport.baidu.com",
    "seccaptcha.baidu.com",
    "m.qq.com",
    "gatherer.m.qq.com",
    "wxgamesdkframe",
)
IGNORED_TITLE_KEYWORDS = (
    "登录",
    "注册",
    "打开APP",
    "安全验证",
    "验证码",
    "只看",
    "回复(",
    "更多精品",
)
THREAD_MARKER_KEYS = {
    "reply_num",
    "reply_num_unit",
    "author_name",
    "author",
    "author_id",
    "is_top",
    "is_good",
    "first_post_id",
    "last_time",
    "last_time_int",
    "abstract",
    "topic_type",
    "zan",
}
TIEBA_CLIENT_API_URL = "https://c.tieba.baidu.com/c/f/frs/page"
TIEBA_CLIENT_SECRET = "tiebaclient!!!"
MAX_STORED_POSTS = 15000


def _tieba_client_sign(params: dict) -> str:
    raw = "".join(f"{key}={params[key]}" for key in sorted(params)) + TIEBA_CLIENT_SECRET
    return hashlib.md5(raw.encode("utf-8")).hexdigest().upper()


def _text_from_rich_blocks(blocks) -> str:
    if not isinstance(blocks, list):
        return ""

    parts = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        text = _clean_text(block.get("text", ""))
        if text and not text.startswith("image_emoticon"):
            parts.append(text)
        elif block.get("c"):
            parts.append(_clean_text(block.get("c")))
    return _clean_text(" ".join(parts))


def _author_name_from_thread(thread: dict) -> str:
    author = thread.get("author")
    if isinstance(author, dict):
        for icon in author.get("show_icon_list") or []:
            if isinstance(icon, dict) and icon.get("text"):
                return _clean_text(icon.get("text"))
        if author.get("name"):
            return _clean_text(author.get("name"))
    if isinstance(author, str):
        return _clean_text(author)
    return _clean_text(thread.get("author_name", ""))


def _timestamp_to_datetime_text(value) -> str:
    try:
        timestamp = int(value)
    except Exception:
        return ""
    if timestamp <= 0:
        return ""
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")


def fetch_tieba_client_threads(tieba_name: str, max_threads: int, source: str = "latest") -> list[dict]:
    max_threads = max(1, int(max_threads or 1))
    page_size = 30
    page_count = min(max(1, math.ceil(max_threads / page_size)), 34)
    threads = []
    seen = set()

    for page_number in range(1, page_count + 1):
        params = {
            "kw": tieba_name,
            "pn": str(page_number),
            "rn": str(page_size),
            "_client_type": "2",
            "_client_version": "12.64.1.1",
        }
        if source == "hot":
            params["sort_type"] = "hot"
        params["sign"] = _tieba_client_sign(params)

        try:
            response = requests.post(
                TIEBA_CLIENT_API_URL,
                data=params,
                headers={
                    "User-Agent": "bdtb for Android 12.64.1.1",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                timeout=20,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            logger.warning("Tieba client API failed on page %s: %s", page_number, exc)
            break

        if str(payload.get("error_code", "0")) not in {"0", ""}:
            logger.warning("Tieba client API returned error: %s", payload.get("error_msg"))
            break

        page_threads = payload.get("thread_list") or []
        added_this_page = 0
        for thread in page_threads:
            if not isinstance(thread, dict):
                continue
            if thread.get("is_top"):
                continue

            tid = str(thread.get("tid") or thread.get("id") or "").strip()
            title = _clean_text(thread.get("title", ""))
            if not tid or tid in seen or not title:
                continue

            abstract = _text_from_rich_blocks(thread.get("rich_abstract")) or _text_from_rich_blocks(thread.get("abstract"))
            create_time = _timestamp_to_datetime_text(thread.get("create_time")) or _timestamp_to_datetime_text(thread.get("last_time_int"))
            candidate = {
                "tid": tid,
                "title": title,
                "href": thread.get("thread_share_link") or f"https://tieba.baidu.com/p/{tid}",
                "summary": abstract,
                "author": _author_name_from_thread(thread),
                "reply_time": create_time,
                "extraction_source": "tieba_client_api",
            }
            threads.append(candidate)
            seen.add(tid)
            added_this_page += 1
            if len(threads) >= max_threads:
                return threads

        if added_this_page == 0:
            break

    return threads


def parse_time_safe(time_str: str):
    if not time_str:
        return datetime.now()
    try:
        return dt_parser.parse(str(time_str))
    except Exception:
        return datetime.now()


def build_tieba_url(tieba_name: str, source: str, pn: int = 0):
    base_url = f"https://tieba.baidu.com/f?kw={quote(tieba_name)}&ie=utf-8"
    if source == "hot":
        base_url += "&sort_type=hot"
    elif source == "good":
        base_url += "&tab=good"
    if pn > 0:
        base_url += f"&pn={pn}"
    return base_url


def _extract_tid_from_url(url: str):
    match = POST_URL_PATTERN.search(url or "")
    if match:
        return match.group(1)
    return ""


def _clean_text(text):
    text = str(text or "")
    text = text.replace("\u200b", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _merge_title_and_content(title: str, content: str):
    title = _clean_text(title)
    content = _clean_text(content)

    if not title:
        return content
    if not content:
        return title
    if title in content:
        return content
    return f"标题：{title}\n\n正文：{content}"


def _build_thread_key(item: dict) -> str:
    return "|".join(
        [
            _clean_text(item.get("title", "")),
            _clean_text(item.get("summary", "")),
            _clean_text(item.get("author", "")),
        ]
    )


def _merge_post_text(title: str, content: str) -> str:
    title = _clean_text(title)
    content = _clean_text(content)
    if not title:
        return content
    if not content:
        return title
    if title in content:
        return content
    return f"标题：{title}\n\n正文：{content}"


def dedupe_and_save_post(db: Session, item: dict):
    existed = db.query(Post).filter(Post.post_id == item["post_id"]).first()
    if existed:
        return False, "duplicate_post_id"

    if item.get("post_url"):
        existed_by_url = db.query(Post).filter(Post.post_url == item["post_url"]).first()
        if existed_by_url:
            return False, "duplicate_post_url"

    post = Post(
        post_id=item["post_id"],
        source_id=item.get("source_id"),
        source=item.get("source", "Baidu Tieba"),
        source_platform="baidu_tieba",
        forum_name=item.get("forum_name", ""),
        title=item.get("title", ""),
        excerpt=item.get("excerpt") or make_excerpt(item["content"]),
        author_name=item.get("author_name", ""),
        content=item["content"],
        publish_time=parse_time_safe(item.get("publish_time_raw", "")),
        post_url=item["post_url"] or "#",
    )
    db.add(post)
    db.commit()
    db.refresh(post)

    labels, risk_level, score, matched_keywords, confidence = analyze_post_text(
        item.get("title", ""),
        item["content"],
    )
    labels_to_save = ",".join(labels) if isinstance(labels, list) else str(labels)
    matched_keywords_to_save = ",".join(matched_keywords) if isinstance(matched_keywords, list) else str(matched_keywords)

    analysis = AnalysisResult(
        post_id_fk=post.id,
        labels=labels_to_save,
        risk_level=risk_level,
        risk_score=score,
        matched_keywords=matched_keywords_to_save,
        confidence=confidence,
        suggestion=build_intervention_suggestion(risk_level, labels if isinstance(labels, list) else [labels]),
    )
    db.add(analysis)
    db.commit()
    db.refresh(analysis)

    if risk_level in {"medium", "high"}:
        alert = AlertRecord(
            analysis_id=analysis.id,
            alert_status="pending",
            priority=risk_priority_from_level(risk_level),
            review_note="贴吧帖子自动采集并识别",
        )
        db.add(alert)
        db.commit()

    return True, "saved"


def enforce_post_storage_limit(db: Session, limit: int = MAX_STORED_POSTS):
    total = db.query(Post).count()
    overflow = total - limit
    if overflow <= 0:
        return 0

    old_posts = (
        db.query(Post)
        .order_by(Post.publish_time.asc(), Post.collected_at.asc(), Post.id.asc())
        .limit(overflow)
        .all()
    )
    post_ids = [post.id for post in old_posts]
    if not post_ids:
        return 0

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
    return len(post_ids)


async def wait_for_manual_verification(page, timeout_seconds: int = 180):
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while True:
        content = await page.content()
        if ("安全验证" not in content) and ("验证码" not in content):
            return True

        if asyncio.get_running_loop().time() > deadline:
            return False

        await asyncio.sleep(2)


def _safe_json_loads(text: str):
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        return None


def _normalize_thread_candidate(tid, title, url, extraction_source: str):
    tid = str(tid).strip() if tid is not None else ""
    title = _clean_text(title)
    url = str(url).strip() if url is not None else ""

    if not tid and url:
        tid = _extract_tid_from_url(url)

    if not url and tid:
        url = f"https://tieba.baidu.com/p/{tid}"
    elif url.startswith("/p/"):
        url = "https://tieba.baidu.com" + url

    if not tid or not re.fullmatch(r"\d+", tid):
        return None
    if not title or len(title) <= 1:
        return None
    if any(keyword in title for keyword in IGNORED_TITLE_KEYWORDS):
        return None

    return {
        "tid": tid,
        "title": title,
        "href": url,
        "extraction_source": extraction_source,
    }


async def wait_for_thread_list(page) -> bool:
    try:
        await page.wait_for_selector(
            THREAD_LIST_READY_SELECTOR,
            timeout=TIEBA_CRAWLER_DEFAULTS["page_load_timeout"],
        )
        return True
    except PlaywrightTimeoutError:
        return False


async def extract_visible_thread_cards(page):
    cards = await page.locator(THREAD_CARD_SELECTOR).evaluate_all(
        """
        (elements) => {
            const pickText = (root, selector) => {
                const node = root.querySelector(selector);
                if (!node) return "";
                return (node.innerText || node.textContent || "").replace(/\\s+/g, " ").trim();
            };

            return elements.map((el, index) => ({
                index,
                title: pickText(el, ".thread-title"),
                summary: pickText(el, ".thread-content"),
                author: pickText(el, ".head-name"),
                reply_time: pickText(el, ".desc-info"),
                href: el.querySelector(".thread-title")?.getAttribute("href") || "",
            }));
        }
        """
    )

    filtered = []
    seen = set()

    for card in cards:
        card["title"] = _clean_text(card.get("title", ""))
        card["summary"] = _clean_text(card.get("summary", ""))
        card["author"] = _clean_text(card.get("author", ""))
        card["reply_time"] = _clean_text(card.get("reply_time", ""))
        card["href"] = _clean_text(card.get("href", ""))

        if not card["title"] or len(card["title"]) <= 1:
            continue

        key = _build_thread_key(card)
        if key in seen:
            continue

        seen.add(key)
        filtered.append(card)

    return filtered


async def extract_threads_from_dom_links(page, max_threads: int):
    threads = []
    seen_tids = set()
    empty_rounds = 0
    max_empty_rounds = max(TIEBA_CRAWLER_DEFAULTS["scroll_times"] + 1, 4)

    list_ready = await wait_for_thread_list(page)
    if not list_ready:
        return []

    while len(threads) < max_threads and empty_rounds < max_empty_rounds:
        visible_cards = await extract_visible_thread_cards(page)
        added_this_round = 0

        for card in visible_cards:
            candidate = _normalize_thread_candidate(
                tid="",
                title=card.get("title", ""),
                url=card.get("href", ""),
                extraction_source="dom_link",
            )
            if not candidate or candidate["tid"] in seen_tids:
                continue

            candidate["summary"] = card.get("summary", "")
            candidate["author"] = card.get("author", "")
            candidate["reply_time"] = card.get("reply_time", "")
            threads.append(candidate)
            seen_tids.add(candidate["tid"])
            added_this_round += 1

            if len(threads) >= max_threads:
                break

        scrolled = await scroll_thread_list(page)
        if added_this_round == 0 and not scrolled:
            empty_rounds += 1
        else:
            empty_rounds = 0

    return threads[:max_threads]


async def scroll_thread_list(page):
    try:
        result = await page.evaluate(
            f"""
            () => {{
                const selectors = [".left-content", ".feed-list-container", ".frs-feed-list"];

                for (const selector of selectors) {{
                    const el = document.querySelector(selector);
                    if (!el) continue;

                    const before = el.scrollTop || 0;
                    el.scrollBy(0, {int(TIEBA_CRAWLER_DEFAULTS["scroll_step"])});
                    const after = el.scrollTop || 0;

                    return {{
                        selector,
                        before,
                        after,
                        changed: after !== before
                    }};
                }}

                const before = window.scrollY || 0;
                window.scrollBy(0, {int(TIEBA_CRAWLER_DEFAULTS["scroll_step"])});
                const after = window.scrollY || 0;
                return {{
                    selector: "window",
                    before,
                    after,
                    changed: after !== before
                }};
            }}
            """
        )
    except Exception:
        await asyncio.sleep(TIEBA_CRAWLER_DEFAULTS["scroll_sleep"])
        return False

    await asyncio.sleep(TIEBA_CRAWLER_DEFAULTS["scroll_sleep"])
    return bool(result and result.get("changed"))


async def open_thread_from_list(page, index: int, fallback_title: str):
    card_locator = page.locator(THREAD_CARD_SELECTOR).nth(index)

    try:
        await card_locator.scroll_into_view_if_needed()
    except Exception:
        pass

    clicked = False
    click_targets = [card_locator.locator(".thread-title"), card_locator]

    for target in click_targets:
        try:
            await target.click(timeout=5000)
            clicked = True
            break
        except Exception:
            continue

    if not clicked:
        raise RuntimeError("无法点击帖子卡片")

    deadline = asyncio.get_running_loop().time() + 15
    while True:
        current_url = page.url
        tid = _extract_tid_from_url(current_url)
        if tid:
            return {
                "tid": tid,
                "title": fallback_title,
                "href": current_url,
                "extraction_source": "dom",
            }

        content = await page.content()
        if "安全验证" in content or "验证码" in content:
            ok = await wait_for_manual_verification(
                page,
                timeout_seconds=TIEBA_CRAWLER_DEFAULTS["manual_verify_timeout"],
            )
            if not ok:
                raise RuntimeError("点击帖子后验证码未在规定时间内完成")

        if asyncio.get_running_loop().time() > deadline:
            raise RuntimeError("点击帖子后未能进入详情页")

        await asyncio.sleep(0.5)


async def extract_threads_from_dom(page, max_threads: int):
    threads = []
    seen_card_keys = set()
    seen_tids = set()
    empty_rounds = 0
    max_empty_rounds = max(TIEBA_CRAWLER_DEFAULTS["scroll_times"] + 1, 4)

    list_ready = await wait_for_thread_list(page)
    if not list_ready:
        return []

    while len(threads) < max_threads and empty_rounds < max_empty_rounds:
        visible_cards = await extract_visible_thread_cards(page)
        next_card = None

        for card in visible_cards:
            key = _build_thread_key(card)
            if key in seen_card_keys:
                continue
            next_card = card
            seen_card_keys.add(key)
            break

        if not next_card:
            scrolled = await scroll_thread_list(page)
            empty_rounds = 0 if scrolled else empty_rounds + 1
            continue

        should_break = False
        try:
            thread = await open_thread_from_list(page, next_card["index"], next_card["title"])
            if thread and thread["tid"] not in seen_tids:
                thread["summary"] = next_card.get("summary", "")
                thread["author"] = next_card.get("author", "")
                thread["reply_time"] = next_card.get("reply_time", "")
                threads.append(thread)
                seen_tids.add(thread["tid"])
        except Exception:
            pass
        finally:
            if _extract_tid_from_url(page.url):
                try:
                    await page.go_back(
                        wait_until="domcontentloaded",
                        timeout=TIEBA_CRAWLER_DEFAULTS["page_load_timeout"],
                    )
                    await wait_for_thread_list(page)
                    await asyncio.sleep(TIEBA_CRAWLER_DEFAULTS["post_page_sleep"])
                except Exception:
                    should_break = True

        if should_break:
            break

    return threads[:max_threads]


def _looks_like_thread_dict(obj):
    if not isinstance(obj, dict):
        return False

    has_thread_marker = any(key in obj for key in THREAD_MARKER_KEYS)
    tid = obj.get("tid") or obj.get("thread_id")
    if not tid and has_thread_marker:
        tid = obj.get("id")

    title = obj.get("title") or obj.get("thread_title") or obj.get("subject")
    if not title and has_thread_marker:
        title = obj.get("name")

    url = obj.get("url") or obj.get("link") or obj.get("href") or obj.get("jump_url")
    if not tid and url:
        tid = _extract_tid_from_url(str(url))

    return bool(tid and title and (has_thread_marker or url))


def _append_thread_candidate(found_threads, seen, tid, title, url, extraction_source: str):
    candidate = _normalize_thread_candidate(tid, title, url, extraction_source)
    if not candidate:
        return None
    if candidate["tid"] in seen:
        return None

    seen.add(candidate["tid"])
    found_threads.append(candidate)
    return candidate


def _extract_text_from_rich_nodes(nodes):
    if not isinstance(nodes, list):
        return ""

    parts = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        text_info = node.get("text_info") or {}
        text = _clean_text(text_info.get("text", ""))
        if text:
            parts.append(text)
            continue

        emoji_info = node.get("emoji_info") or {}
        emoji_text = _clean_text(emoji_info.get("c", ""))
        if emoji_text:
            parts.append(emoji_text)

    return _clean_text(" ".join(parts))


def _extract_threads_from_feed_list(data, found_threads, seen):
    page_data = data.get("page_data") or {}
    feed_list = page_data.get("feed_list") or []
    if not isinstance(feed_list, list):
        return

    for item in feed_list:
        if not isinstance(item, dict):
            continue

        feed = item.get("feed") or {}
        if not isinstance(feed, dict):
            continue

        business_info = feed.get("business_info_map") or {}
        thread_id = business_info.get("thread_id") or business_info.get("tid")
        title = _clean_text(business_info.get("title", ""))
        forum_name = _clean_text(business_info.get("forum_name", ""))
        abstract = _clean_text(business_info.get("abstract", ""))

        if not title:
            for component in feed.get("components", []) or []:
                if not isinstance(component, dict):
                    continue
                title_block = component.get("feed_title") or {}
                if title_block:
                    title = _extract_text_from_rich_nodes(title_block.get("data") or [])
                    if title:
                        break

        if not abstract:
            for component in feed.get("components", []) or []:
                if not isinstance(component, dict):
                    continue
                abstract_block = component.get("feed_abstract") or {}
                if abstract_block:
                    abstract = _extract_text_from_rich_nodes(abstract_block.get("data") or [])
                    if abstract:
                        break

        candidate = _append_thread_candidate(
            found_threads,
            seen,
            thread_id,
            title,
            f"https://tieba.baidu.com/p/{thread_id}" if thread_id else "",
            "network",
        )
        if candidate:
            candidate["summary"] = abstract
            candidate["forum_name"] = forum_name


def _walk_json_for_threads(obj, found_threads, seen):
    if isinstance(obj, dict):
        if "page_data" in obj:
            _extract_threads_from_feed_list(obj, found_threads, seen)

        candidate_dicts = [obj]

        for nested_key in ("thread", "topic", "item", "post"):
            nested = obj.get(nested_key)
            if isinstance(nested, dict):
                merged = dict(nested)
                for key in THREAD_MARKER_KEYS:
                    if key in obj and key not in merged:
                        merged[key] = obj[key]
                if "url" in obj and "url" not in merged:
                    merged["url"] = obj["url"]
                candidate_dicts.append(merged)

        for candidate_dict in candidate_dicts:
            if not _looks_like_thread_dict(candidate_dict):
                continue

            tid = candidate_dict.get("tid") or candidate_dict.get("thread_id") or candidate_dict.get("id")
            title = (
                candidate_dict.get("title")
                or candidate_dict.get("thread_title")
                or candidate_dict.get("subject")
                or candidate_dict.get("name")
            )
            url = (
                candidate_dict.get("url")
                or candidate_dict.get("link")
                or candidate_dict.get("href")
                or candidate_dict.get("jump_url")
            )
            _append_thread_candidate(found_threads, seen, tid, title, url, "network")

        for value in obj.values():
            _walk_json_for_threads(value, found_threads, seen)

    elif isinstance(obj, list):
        for item in obj:
            _walk_json_for_threads(item, found_threads, seen)


def _extract_threads_from_html_text(html_text, found_threads, seen, extraction_source: str):
    if not html_text or "/p/" not in html_text:
        return

    try:
        soup = BeautifulSoup(html_text, "lxml")
        links = soup.select("a[href]")
    except Exception:
        return

    for link in links:
        href = (link.get("href") or "").strip()
        title = _clean_text(link.get("title") or link.get_text(" ", strip=True))

        if not href:
            continue

        if href.startswith("/p/"):
            href = "https://tieba.baidu.com" + href

        tid = _extract_tid_from_url(href)
        if not tid:
            continue

        _append_thread_candidate(found_threads, seen, tid, title, href, extraction_source)


def _entry_is_thread_related(entry: dict):
    url = entry.get("url", "") or ""
    if not url:
        return False
    if any(keyword in url for keyword in IGNORED_NETWORK_URL_KEYWORDS):
        return False
    if any(hint in url for hint in THREAD_ENTRY_URL_HINTS):
        return True
    return "/p/" in (entry.get("body_text", "") or "")


async def capture_network_data(page):
    captured = []

    async def handle_response(response):
        try:
            request = response.request
            resource_type = request.resource_type
            url = response.url
            status = response.status
            headers = response.headers
            content_type = headers.get("content-type", "")

            if resource_type not in ["xhr", "fetch", "document"]:
                return

            body_text = ""
            try:
                body_text = await response.text()
            except Exception:
                body_text = ""

            body_limit = TIEBA_CRAWLER_DEFAULTS["debug_response_chars"]
            if any(hint in url for hint in THREAD_ENTRY_URL_HINTS):
                body_limit = TIEBA_CRAWLER_DEFAULTS["thread_response_chars"]

            captured.append(
                {
                    "url": url,
                    "method": request.method,
                    "resource_type": resource_type,
                    "status": status,
                    "content_type": content_type,
                    "body_text": body_text[:body_limit],
                    "body_preview": body_text[:3000],
                }
            )
        except Exception:
            pass

    page.on("response", lambda response: asyncio.create_task(handle_response(response)))
    return captured


def extract_threads_from_network_entries(entries, max_threads: int):
    found_threads = []
    seen = set()

    related_entries = [entry for entry in entries if _entry_is_thread_related(entry)]
    for entry in related_entries:
        body = entry.get("body_text", "") or ""
        content_type = (entry.get("content_type", "") or "").lower()

        if "json" in content_type or body.strip().startswith("{") or body.strip().startswith("["):
            data = _safe_json_loads(body)
            if data is not None:
                _walk_json_for_threads(data, found_threads, seen)

        if len(found_threads) < max_threads and "/p/" in body:
            _extract_threads_from_html_text(body, found_threads, seen, "network")

        if len(found_threads) >= max_threads:
            break

    return found_threads[:max_threads]


async def _stabilize_list_page(page):
    await asyncio.sleep(TIEBA_CRAWLER_DEFAULTS["list_page_sleep"])
    for _ in range(TIEBA_CRAWLER_DEFAULTS["scroll_times"]):
        await scroll_thread_list(page)
    await asyncio.sleep(TIEBA_CRAWLER_DEFAULTS["list_settle_sleep"])


def _merge_threads(max_threads: int, *thread_groups):
    merged = []
    seen = set()

    for thread_group in thread_groups:
        for thread in thread_group:
            tid = thread.get("tid")
            if not tid or tid in seen:
                continue

            seen.add(tid)
            merged.append(thread)
            if len(merged) >= max_threads:
                return merged

    return merged


def _load_cached_threads(max_threads: int):
    cache_path = "tieba_threads_from_network.json"
    try:
        with open(cache_path, "r", encoding="utf-8") as file:
            data = json.load(file)
    except Exception:
        return []

    if not isinstance(data, list):
        return []

    threads = []
    seen = set()
    for item in data:
        if not isinstance(item, dict):
            continue
        tid = item.get("tid") or item.get("thread_id") or item.get("id")
        title = item.get("title") or item.get("thread_title") or item.get("subject")
        url = item.get("href") or item.get("url") or item.get("link")
        candidate = _normalize_thread_candidate(tid, title, url, "cached_network")
        if not candidate or candidate["tid"] in seen:
            continue
        candidate["summary"] = item.get("summary") or item.get("abstract") or ""
        candidate["author"] = item.get("author") or item.get("author_name") or ""
        candidate["reply_time"] = item.get("reply_time") or item.get("last_time") or ""
        seen.add(candidate["tid"])
        threads.append(candidate)
        if len(threads) >= max_threads:
            break

    return threads


def _build_fallback_post_item(thread, tieba_name: str, source_label: str, source_id: int | None = None):
    title = _clean_text(thread.get("title", ""))
    summary = _clean_text(thread.get("summary", ""))
    content = _merge_post_text(title, summary)
    tid = str(thread.get("tid", "")).strip()
    return {
        "post_id": tid,
        "source_id": source_id,
        "source": source_label or f"{tieba_name}吧",
        "forum_name": tieba_name,
        "title": title or f"帖子 {tid}",
        "excerpt": make_excerpt(summary or content, 140),
        "author_name": _clean_text(thread.get("author", "")),
        "content": content or title or f"帖子 {tid}",
        "publish_time_raw": thread.get("reply_time") or "",
        "post_url": thread.get("href") or f"https://tieba.baidu.com/p/{tid}",
    }


def _normalize_forum_name(text: str) -> str:
    value = _clean_text(text)
    if value.endswith("吧"):
        value = value[:-1]
    return value.strip()


async def _extract_forum_name_from_post_page(page) -> str:
    selectors = [
        'a[href*="/f?kw="]',
        ".card_title_fname",
        ".forum_name",
        ".card_title a",
    ]

    for selector in selectors:
        try:
            elements = await page.query_selector_all(selector)
        except Exception:
            continue

        for element in elements[:8]:
            try:
                text = _clean_text(await element.inner_text())
            except Exception:
                continue
            normalized = _normalize_forum_name(text)
            if normalized:
                return normalized

    try:
        html = await page.content()
    except Exception:
        return ""

    match = re.search(r'"forum_name"\s*:\s*"([^"]+)"', html)
    if match:
        return _normalize_forum_name(match.group(1))

    return ""


async def _collect_threads_from_single_page(page, url: str, max_threads: int, captured_network):
    await page.goto(
        url,
        wait_until="domcontentloaded",
        timeout=TIEBA_CRAWLER_DEFAULTS["page_load_timeout"],
    )

    page_content = await page.content()
    if "瀹夊叏楠岃瘉" in page_content or "楠岃瘉鐮?" in page_content:
        ok = await wait_for_manual_verification(
            page,
            timeout_seconds=TIEBA_CRAWLER_DEFAULTS["manual_verify_timeout"],
        )
        if not ok:
            return {
                "ok": False,
                "message": "列表页验证码未在规定时间内完成",
                "threads": [],
            }

        await page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=TIEBA_CRAWLER_DEFAULTS["page_load_timeout"],
        )

    await _stabilize_list_page(page)
    dom_threads = await extract_threads_from_dom_links(page, max_threads=max_threads)
    network_threads = extract_threads_from_network_entries(captured_network, max_threads=max_threads)
    threads = _merge_threads(max_threads, dom_threads, network_threads)

    return {
        "ok": True,
        "threads": threads,
        "dom_thread_count": len(dom_threads),
        "network_thread_count": len(network_threads),
    }


async def _gather_threads_across_pages(page, tieba_name: str, source: str, max_threads: int, captured_network):
    gathered_threads = []
    seen_tids = set()
    total_dom_threads = 0
    total_network_threads = 0

    threads_per_page = max(1, int(TIEBA_CRAWLER_DEFAULTS.get("threads_per_page_guess", 20)))
    max_list_pages = max(1, int(TIEBA_CRAWLER_DEFAULTS.get("max_list_pages", 10)))
    pn_step = max(1, int(TIEBA_CRAWLER_DEFAULTS.get("list_page_pn_step", 50)))
    page_count = min(max_list_pages, max(1, math.ceil(max_threads / threads_per_page)))

    for page_index in range(page_count):
        page_url = build_tieba_url(tieba_name, source, pn=page_index * pn_step)
        single_page_result = await _collect_threads_from_single_page(
            page=page,
            url=page_url,
            max_threads=max_threads,
            captured_network=captured_network,
        )
        if not single_page_result.get("ok"):
            return {
                "ok": False,
                "message": single_page_result.get("message", "列表页抓取失败"),
                "threads": gathered_threads,
                "dom_thread_count": total_dom_threads,
                "network_thread_count": total_network_threads,
                "pages_visited": page_index + 1,
            }

        total_dom_threads += single_page_result.get("dom_thread_count", 0)
        total_network_threads += single_page_result.get("network_thread_count", 0)

        before_count = len(gathered_threads)
        for thread in single_page_result.get("threads", []):
            tid = thread.get("tid")
            if not tid or tid in seen_tids:
                continue
            seen_tids.add(tid)
            gathered_threads.append(thread)
            if len(gathered_threads) >= max_threads:
                break

        if len(gathered_threads) >= max_threads:
            break

        if len(gathered_threads) == before_count:
            break

    return {
        "ok": True,
        "threads": gathered_threads[:max_threads],
        "dom_thread_count": total_dom_threads,
        "network_thread_count": total_network_threads,
        "pages_visited": page_count,
    }


async def extract_post_detail(
    page,
    tid: str,
    fallback_title: str,
    tieba_name: str,
    source_label: str,
    source_id: int | None = None,
    fallback_author: str = "",
    fallback_summary: str = "",
):
    post_url = f"https://tieba.baidu.com/p/{tid}"
    await page.goto(
        post_url,
        wait_until="domcontentloaded",
        timeout=TIEBA_CRAWLER_DEFAULTS["post_load_timeout"],
    )
    await asyncio.sleep(TIEBA_CRAWLER_DEFAULTS["post_page_sleep"])

    page_content = await page.content()
    if "安全验证" in page_content or "验证码" in page_content:
        ok = await wait_for_manual_verification(
            page,
            timeout_seconds=TIEBA_CRAWLER_DEFAULTS["manual_verify_timeout"],
        )
        if not ok:
            raise RuntimeError("验证码未在规定时间内完成")

        await page.goto(
            post_url,
            wait_until="domcontentloaded",
            timeout=TIEBA_CRAWLER_DEFAULTS["post_load_timeout"],
        )
        await asyncio.sleep(TIEBA_CRAWLER_DEFAULTS["post_page_sleep"])

    expected_forum = _normalize_forum_name(tieba_name)
    actual_forum = await _extract_forum_name_from_post_page(page)
    if actual_forum and actual_forum != expected_forum:
        raise RuntimeError(f"skip_non_target_forum:{actual_forum}")

    title = _clean_text(fallback_title)
    title_elem = await page.query_selector(".core_title_txt")
    if title_elem:
        title_text = await title_elem.get_attribute("title") or ""
        if not title_text:
            try:
                title_text = await title_elem.inner_text()
            except Exception:
                title_text = ""
        title = _clean_text(title_text) or title

    author_name = _clean_text(fallback_author)
    author_selectors = [".d_name .p_author_name", ".userinfo_username", ".author-name"]
    for selector in author_selectors:
        try:
            author_elem = await page.query_selector(selector)
            if not author_elem:
                continue
            author_text = _clean_text(await author_elem.inner_text())
            if author_text:
                author_name = author_text
                break
        except Exception:
            continue

    content = ""
    post_elements = await page.query_selector_all(".l_post")
    content_scopes = post_elements[:1] if post_elements else [page]
    content_selectors = [".d_post_content_main", ".d_post_content", ".post-content__text"]

    for scope in content_scopes:
        for selector in content_selectors:
            elements = await scope.query_selector_all(selector)
            for element in elements:
                try:
                    text = _clean_text(await element.inner_text())
                except Exception:
                    continue

                if text:
                    content = text
                    break

            if content:
                break

        if content:
            break

    if not content:
        content = _clean_text(fallback_summary)

    merged_content = _merge_post_text(title, content)

    publish_time_raw = ""
    if post_elements:
        try:
            tail_infos = await post_elements[0].query_selector_all(".tail-info")
            tail_texts = []
            for tail_info in tail_infos:
                text = _clean_text(await tail_info.inner_text())
                if text:
                    tail_texts.append(text)

            for text in reversed(tail_texts):
                if re.search(r"\d{4}-\d{1,2}-\d{1,2}", text) or re.search(r"\d{1,2}:\d{2}", text):
                    publish_time_raw = text
                    break
        except Exception:
            pass

    return {
        "post_id": tid,
        "source_id": source_id,
        "source": source_label or f"{tieba_name}吧",
        "forum_name": tieba_name,
        "title": title or f"帖子 {tid}",
        "excerpt": make_excerpt(content or merged_content, 140),
        "author_name": author_name,
        "content": merged_content or title or f"帖子 {tid}",
        "publish_time_raw": publish_time_raw,
        "post_url": post_url,
    }


async def preview_tieba_threads(
    tieba_name: str,
    source: str = "latest",
    max_threads: int = 10,
    headless: bool = False,
):
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )

        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1400, "height": 900},
            locale="zh-CN",
        )

        await context.add_init_script(
            """
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            window.chrome = { runtime: {} };
            """
        )

        page = await context.new_page()
        captured_network = await capture_network_data(page)

        try:
            gathered = await _gather_threads_across_pages(
                page=page,
                tieba_name=tieba_name,
                source=source,
                max_threads=max_threads,
                captured_network=captured_network,
            )
            need_verify = "验证码" in str(gathered.get("message", ""))
            fallback_reason = ""
            if not gathered.get("ok"):
                fallback_reason = gathered.get("message", "实时抓取失败")
                cached_threads = _load_cached_threads(max_threads)
                if cached_threads:
                    gathered = {
                        "ok": True,
                        "threads": cached_threads,
                        "dom_thread_count": 0,
                        "network_thread_count": len(cached_threads),
                        "pages_visited": 0,
                        "used_cache": True,
                    }
                else:
                    return {
                        "ok": False,
                        "message": fallback_reason,
                        "inserted": 0,
                        "duplicated": 0,
                        "failed": 0,
                        "details": [],
                        "captured_xhr_count": len(captured_network),
                        "pages_visited": gathered.get("pages_visited", 1),
                    }
            if False:
                return {
                    "ok": False,
                    "message": gathered.get("message", "抓取失败"),
                    "threads": gathered.get("threads", [])[:10],
                }

            current_url = page.url
            page_title = await page.title()

            await page.screenshot(path="tieba_preview_debug.png", full_page=True)
            html = await page.content()
            with open("tieba_preview_debug.html", "w", encoding="utf-8") as file:
                file.write(html)

            with open("tieba_network_debug.json", "w", encoding="utf-8") as file:
                json.dump(captured_network, file, ensure_ascii=False, indent=2)

            threads = gathered.get("threads", [])

            if threads:
                with open("tieba_threads_from_network.json", "w", encoding="utf-8") as file:
                    json.dump(threads, file, ensure_ascii=False, indent=2)

            return {
                "ok": True,
                "need_verify": need_verify,
                "tieba_name": tieba_name,
                "source": source,
                "current_url": current_url,
                "page_title": page_title,
                "captured_xhr_count": len(captured_network),
                "dom_thread_count": gathered.get("dom_thread_count", 0),
                "network_thread_count": gathered.get("network_thread_count", 0),
                "pages_visited": gathered.get("pages_visited", 1),
                "count": len(threads),
                "threads": threads[:10],
                "debug_files": [
                    "tieba_preview_debug.png",
                    "tieba_preview_debug.html",
                    "tieba_network_debug.json",
                    "tieba_threads_from_network.json",
                ],
            }
        finally:
            try:
                await browser.close()
            except Exception as exc:
                # During app shutdown the Playwright driver can terminate first.
                if "Connection closed while reading from the driver" in str(exc):
                    logger.info("Playwright browser closed during shutdown")
                else:
                    raise


async def crawl_tieba_to_db(
    db: Session,
    tieba_name: str,
    source: str = "latest",
    max_threads: int = 10,
    headless: bool = False,
    source_label: str | None = None,
    source_id: int | None = None,
):
    client_threads = fetch_tieba_client_threads(tieba_name, max_threads=max_threads, source=source)
    if client_threads:
        inserted = 0
        duplicated = 0
        failed = 0
        details = []
        actual_source_label = source_label or f"{tieba_name}吧"

        for thread in client_threads:
            tid = thread["tid"]
            title = thread["title"]
            try:
                item = _build_fallback_post_item(
                    thread=thread,
                    tieba_name=tieba_name,
                    source_label=actual_source_label,
                    source_id=source_id,
                )
                ok, reason = dedupe_and_save_post(db, item)
                if ok:
                    inserted += 1
                    details.append(
                        {
                            "tid": tid,
                            "title": item["title"],
                            "status": "inserted",
                            "extraction_source": "tieba_client_api",
                        }
                    )
                else:
                    duplicated += 1
                    details.append(
                        {
                            "tid": tid,
                            "title": item["title"],
                            "status": reason,
                            "extraction_source": "tieba_client_api",
                        }
                    )
            except Exception as exc:
                failed += 1
                details.append(
                    {
                        "tid": tid,
                        "title": title,
                        "status": "failed",
                        "error": str(exc)[:120],
                        "extraction_source": "tieba_client_api",
                    }
                )

        removed_count = enforce_post_storage_limit(db, MAX_STORED_POSTS)
        with open("tieba_threads_from_network.json", "w", encoding="utf-8") as file:
            json.dump(client_threads, file, ensure_ascii=False, indent=2)

        return {
            "ok": True,
            "message": "贴吧客户端接口采集完成",
            "tieba_name": tieba_name,
            "source": source,
            "captured_xhr_count": 0,
            "dom_thread_count": 0,
            "network_thread_count": len(client_threads),
            "pages_visited": math.ceil(len(client_threads) / 30),
            "total_threads": len(client_threads),
            "processed_threads": inserted + duplicated + failed,
            "inserted": inserted,
            "duplicated": duplicated,
            "failed": failed,
            "removed_count": removed_count,
            "details": details,
        }

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )

        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
            locale="zh-CN",
        )

        await context.add_init_script(
            """
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            window.chrome = { runtime: {} };
            """
        )

        page = await context.new_page()
        captured_network = await capture_network_data(page)

        inserted = 0
        duplicated = 0
        failed = 0
        details = []
        duplicate_streak = 0
        duplicate_stop_streak = max(3, int(TIEBA_CRAWLER_DEFAULTS.get("stop_on_duplicate_streak", 8) or 8))

        try:
            gathered = await _gather_threads_across_pages(
                page=page,
                tieba_name=tieba_name,
                source=source,
                max_threads=max_threads,
                captured_network=captured_network,
            )
            if not gathered.get("ok"):
                return {
                    "ok": False,
                    "message": gathered.get("message", "抓取失败"),
                    "inserted": 0,
                    "duplicated": 0,
                    "failed": 0,
                    "details": [],
                    "captured_xhr_count": len(captured_network),
                    "pages_visited": gathered.get("pages_visited", 1),
                }

            with open("tieba_network_debug.json", "w", encoding="utf-8") as file:
                json.dump(captured_network, file, ensure_ascii=False, indent=2)

            threads = gathered.get("threads", [])

            with open("tieba_threads_from_network.json", "w", encoding="utf-8") as file:
                json.dump(threads, file, ensure_ascii=False, indent=2)

            if not threads:
                fallback_reason = "实时抓取没有提取到帖子"
                threads = _load_cached_threads(max_threads)
                if not threads:
                    existing_posts = (
                        db.query(Post)
                        .filter(Post.forum_name == tieba_name)
                        .order_by(Post.collected_at.desc(), Post.id.desc())
                        .limit(max_threads)
                        .all()
                    )
                    threads = [
                        {
                            "tid": post.post_id,
                            "title": post.title or post.post_id,
                            "href": post.post_url,
                            "summary": post.excerpt or post.content,
                            "author": post.author_name,
                            "reply_time": str(post.publish_time or ""),
                            "extraction_source": "existing_database",
                        }
                        for post in existing_posts
                    ]
                gathered["used_cache"] = bool(threads)
                gathered["network_thread_count"] = len(threads)

            if False and not threads:
                return {
                    "ok": False,
                    "message": "未能从贴吧列表页中提取到真实帖子，请先完成验证码后重试",
                    "inserted": 0,
                    "duplicated": 0,
                    "failed": 0,
                    "details": [],
                    "captured_xhr_count": len(captured_network),
                }

            actual_source_label = source_label or f"{tieba_name}吧"

            for thread in threads:
                tid = thread["tid"]
                title = thread["title"]

                try:
                    if gathered.get("used_cache"):
                        item = _build_fallback_post_item(
                            thread=thread,
                            tieba_name=tieba_name,
                            source_label=actual_source_label,
                            source_id=source_id,
                        )
                    else:
                        item = await extract_post_detail(
                        page=page,
                        tid=tid,
                        fallback_title=title,
                        tieba_name=tieba_name,
                        source_label=actual_source_label,
                        source_id=source_id,
                        fallback_author=thread.get("author", ""),
                        fallback_summary=thread.get("summary", ""),
                        )
                    ok, reason = dedupe_and_save_post(db, item)

                    if ok:
                        inserted += 1
                        duplicate_streak = 0
                        details.append(
                            {
                                "tid": tid,
                                "title": item["title"],
                                "status": "inserted",
                                "extraction_source": thread.get("extraction_source", "unknown"),
                            }
                        )
                    else:
                        duplicated += 1
                        duplicate_streak = duplicate_streak + 1 if reason in {"duplicate_post_id", "duplicate_post_url"} else 0
                        details.append(
                            {
                                "tid": tid,
                                "title": item["title"],
                                "status": reason,
                                "extraction_source": thread.get("extraction_source", "unknown"),
                            }
                        )
                except Exception as exc:
                    failed += 1
                    duplicate_streak = 0
                    details.append(
                        {
                            "tid": tid,
                            "title": title,
                            "status": "failed",
                            "error": str(exc)[:120],
                            "extraction_source": thread.get("extraction_source", "unknown"),
                        }
                    )

                if source == "latest" and duplicate_streak >= duplicate_stop_streak:
                    details.append(
                        {
                            "tid": tid,
                            "title": title,
                            "status": "stopped_on_duplicates",
                            "note": f"连续发现 {duplicate_streak} 条已存在帖子，提前结束最新帖增量抓取。",
                            "extraction_source": thread.get("extraction_source", "unknown"),
                        }
                    )
                    break

                await asyncio.sleep(TIEBA_CRAWLER_DEFAULTS["between_posts_sleep"])

            return {
                "ok": True,
                "message": (
                    "采集完成"
                    if not gathered.get("used_cache")
                    else f"实时抓取受限，已使用已有帖子数据完成刷新：{fallback_reason}"
                ),
                "tieba_name": tieba_name,
                "source": source,
                "captured_xhr_count": len(captured_network),
                "dom_thread_count": gathered.get("dom_thread_count", 0),
                "network_thread_count": gathered.get("network_thread_count", 0),
                "pages_visited": gathered.get("pages_visited", 1),
                "total_threads": len(threads),
                "processed_threads": inserted + duplicated + failed,
                "inserted": inserted,
                "duplicated": duplicated,
                "failed": failed,
                "details": details,
            }
        finally:
            await browser.close()
