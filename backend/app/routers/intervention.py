import json
import os
from datetime import datetime
from pathlib import Path
from urllib import request as urlrequest
from urllib.error import URLError

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..demo_scope import is_demo_post
from ..display_filters import should_hide_post
from ..models import (
    AlertRecord,
    AnalysisResult,
    InterventionOperationLog,
    InterventionRecord,
    InterventionReplyRecord,
    Post,
)
from ..schemas import InterventionReplyReview, InterventionReplySent

router = APIRouter(prefix="/api/interventions", tags=["interventions"])

PROMPT_VERSION = "public_comment_v2"
RISK_SCORE_THRESHOLD = float(os.getenv("INTERVENTION_RISK_THRESHOLD", "0.65"))
HIGH_RISK_LEVELS = {"high", "critical", "severe"}
BACKEND_DIR = Path(__file__).resolve().parents[2]
DEEPSEEK_CONFIG_PATH = BACKEND_DIR / "deepseek_config.json"


def _shorten(text: str, limit: int = 700) -> str:
    value = str(text or "").strip()
    return value if len(value) <= limit else value[:limit] + "..."


def _now():
    return datetime.utcnow()


def _risk_sort_key(item):
    alert, analysis, post = item
    return (analysis.risk_score or 0, alert.created_at or datetime.min, post.publish_time or datetime.min)


def _is_intervention_candidate(post: Post, analysis: AnalysisResult) -> bool:
    if not post or not analysis or not is_demo_post(post) or should_hide_post(post):
        return False
    level = (analysis.risk_level or "").lower()
    return level in HIGH_RISK_LEVELS or (analysis.risk_score or 0) >= RISK_SCORE_THRESHOLD


def _candidate_query(db: Session):
    rows = (
        db.query(AlertRecord, AnalysisResult, Post)
        .join(AnalysisResult, AlertRecord.analysis_id == AnalysisResult.id)
        .join(Post, AnalysisResult.post_id_fk == Post.id)
        .all()
    )
    candidates = [
        (alert, analysis, post)
        for alert, analysis, post in rows
        if _is_intervention_candidate(post, analysis)
    ]
    return sorted(candidates, key=_risk_sort_key, reverse=True)


def _get_or_create_intervention(db: Session, alert: AlertRecord) -> InterventionRecord:
    row = (
        db.query(InterventionRecord)
        .filter(InterventionRecord.alert_id == alert.id)
        .order_by(InterventionRecord.id.desc())
        .first()
    )
    if row:
        return row

    row = InterventionRecord(
        alert_id=alert.id,
        campus_process_no=f"AUTO-RISK-{alert.id:04d}",
        process_status="waiting_reply",
        handler="admin",
        follow_up_note="系统自动纳入高风险干预队列",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _resolve_context(db: Session, intervention_id: int):
    intervention = db.query(InterventionRecord).filter(InterventionRecord.id == intervention_id).first()
    if not intervention:
        raise HTTPException(status_code=404, detail="干预记录不存在")

    alert = db.query(AlertRecord).filter(AlertRecord.id == intervention.alert_id).first()
    if not alert:
        raise HTTPException(status_code=404, detail="关联预警不存在")

    analysis = db.query(AnalysisResult).filter(AnalysisResult.id == alert.analysis_id).first()
    if not analysis:
        raise HTTPException(status_code=404, detail="关联分析结果不存在")

    post = db.query(Post).filter(Post.id == analysis.post_id_fk).first()
    if not _is_intervention_candidate(post, analysis):
        raise HTTPException(status_code=404, detail="关联帖子未进入高风险干预范围")

    return intervention, alert, analysis, post


def _get_or_create_reply_record(db: Session, intervention: InterventionRecord, post: Post):
    record = (
        db.query(InterventionReplyRecord)
        .filter(InterventionReplyRecord.intervention_id == intervention.id)
        .order_by(InterventionReplyRecord.id.desc())
        .first()
    )
    if record:
        return record

    record = InterventionReplyRecord(
        intervention_id=intervention.id,
        alert_id=intervention.alert_id,
        post_id=post.id,
        source_platform=post.source_platform,
        target_url=post.post_url,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def _reply_record_to_dict(record: InterventionReplyRecord | None):
    if not record:
        return None
    return {
        "id": record.id,
        "intervention_id": record.intervention_id,
        "alert_id": record.alert_id,
        "post_id": record.post_id,
        "target_url": record.target_url,
        "draft_content": record.draft_content,
        "final_content": record.final_content,
        "model_provider": record.model_provider,
        "model_name": record.model_name,
        "prompt_version": record.prompt_version,
        "generate_status": record.generate_status,
        "review_status": record.review_status,
        "review_note": record.review_note,
        "reviewed_by": record.reviewed_by,
        "reviewed_at": record.reviewed_at,
        "send_status": record.send_status,
        "send_attempt_count": record.send_attempt_count,
        "send_result_message": record.send_result_message,
        "external_reply_url": record.external_reply_url,
        "external_reply_id": record.external_reply_id,
        "sent_at": record.sent_at,
        "sent_by": record.sent_by,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


def _log_operation(
    db: Session,
    intervention_id: int,
    operation_type: str,
    detail: str,
    reply_record_id: int | None = None,
    operator: str = "admin",
    result: str = "success",
):
    db.add(
        InterventionOperationLog(
            intervention_id=intervention_id,
            reply_record_id=reply_record_id,
            operation_type=operation_type,
            operator=operator,
            operation_detail=detail,
            operation_result=result,
        )
    )
    db.commit()


def _fallback_reply(post: Post, analysis: AnalysisResult) -> str:
    level = (analysis.risk_level or "").lower()
    if level in HIGH_RISK_LEVELS or (analysis.risk_score or 0) >= 0.8:
        return (
            "看到你写下这些内容，能感觉到这段时间真的很不容易。先别一个人硬扛，"
            "可以找身边信任的人聊一聊，也欢迎联系老师或校内支持渠道，我们愿意陪你一起想办法。"
        )
    return (
        "看到你的分享，能理解这种状态可能让人很累。先给自己一点缓冲，"
        "也可以和同学、老师或信任的人聊聊，很多问题一起梳理会更容易一点。"
    )


def _load_deepseek_config():
    config = {
        "api_key": os.getenv("DEEPSEEK_API_KEY", "").strip(),
        "model": os.getenv("DEEPSEEK_MODEL", "deepseek-chat").strip() or "deepseek-chat",
        "base_url": os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/chat/completions").strip()
        or "https://api.deepseek.com/chat/completions",
    }
    if DEEPSEEK_CONFIG_PATH.exists():
        try:
            file_config = json.loads(DEEPSEEK_CONFIG_PATH.read_text(encoding="utf-8"))
            config["api_key"] = str(file_config.get("api_key") or config["api_key"]).strip()
            config["model"] = str(file_config.get("model") or config["model"]).strip()
            config["base_url"] = str(file_config.get("base_url") or config["base_url"]).strip()
        except (OSError, json.JSONDecodeError):
            pass
    return config


def _call_deepseek(post: Post, analysis: AnalysisResult):
    config = _load_deepseek_config()
    api_key = config["api_key"]
    model_name = config["model"]
    if not api_key or "在这里粘贴" in api_key:
        return _fallback_reply(post, analysis), model_name, "fallback_no_api_key"

    system_prompt = (
        "你现在是一位资深的心理健康咨询师，面对大学生发布的、可能显露心理健康问题的帖子，"
        "请根据他的帖子内容，有针对性地给出专业、贴心、温和且可执行的建议。"
        "回复要像一位有经验的心理咨询师在认真读完帖子后进行回应：先准确共情对方的具体处境和情绪，"
        "再帮助他把当前感受稍微理清，最后给出几条现实可做的小步骤。"
        "语气要真诚、稳定、有陪伴感，不要空泛安慰，不要简单说教，不要直接贴心理疾病标签，不要做医学诊断，"
        "不要提到监测系统、风险识别或后台分析，不要说自己是心理咨询师、老师、系统或机器人。"
        "如果帖子中出现强烈绝望、自伤、自杀、失控等危机信号，要明确而温柔地建议他立刻联系身边可信任的人、辅导员、学校心理中心或当地紧急求助渠道。"
        "字数尽量充实一些，建议控制在220到420字。"
        "输出必须是可以直接复制粘贴的中文纯文本正文：不要标题，不要星号，不要项目符号，不要Markdown格式，不要编号，不要引号，不要解释生成过程。"
    )
    user_prompt = (
        f"帖子标题：{post.title}\n"
        f"帖子正文：{_shorten(post.content)}\n"
        f"作者昵称：{post.author_name}\n"
        f"风险等级：{analysis.risk_level}\n"
        f"风险分：{analysis.risk_score}\n"
        f"命中标签：{analysis.labels}\n"
        f"系统建议：{analysis.suggestion}\n"
        "发布场景：人工审核后的心理支持回复\n"
        "回复身份：关怀支持者，但不要在正文里说明身份\n"
        "写作要求：结合帖子细节，不要套话；建议具体、温和、可执行；字数尽量多一些；只输出一段可直接复制粘贴的中文正文。"
    )
    payload = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.35,
        "max_tokens": 700,
    }
    req = urlrequest.Request(
        config["base_url"],
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlrequest.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        content = data["choices"][0]["message"]["content"].strip()
        return content, model_name, "success"
    except (KeyError, URLError, TimeoutError, Exception) as exc:
        return _fallback_reply(post, analysis), model_name, f"fallback_deepseek_error:{str(exc)[:120]}"


@router.get("")
def get_intervention_queue(db: Session = Depends(get_db)):
    result = []
    for alert, analysis, post in _candidate_query(db):
        intervention = _get_or_create_intervention(db, alert)
        reply = (
            db.query(InterventionReplyRecord)
            .filter(InterventionReplyRecord.intervention_id == intervention.id)
            .order_by(InterventionReplyRecord.id.desc())
            .first()
        )
        result.append(
            {
                "id": intervention.id,
                "alert_id": alert.id,
                "post_id": post.id,
                "title": post.title,
                "author_name": post.author_name,
                "post_url": post.post_url,
                "publish_time": post.publish_time,
                "risk_level": analysis.risk_level,
                "risk_score": analysis.risk_score,
                "labels": analysis.labels,
                "process_status": intervention.process_status,
                "handler": intervention.handler,
                "follow_up_note": intervention.follow_up_note,
                "reply_status": reply.review_status if reply else "not_generated",
                "send_status": reply.send_status if reply else "not_sent",
                "updated_at": reply.updated_at if reply else intervention.updated_at,
            }
        )
    return result


@router.get("/{intervention_id}/reply")
def get_intervention_reply(intervention_id: int, db: Session = Depends(get_db)):
    intervention, alert, analysis, post = _resolve_context(db, intervention_id)
    record = (
        db.query(InterventionReplyRecord)
        .filter(InterventionReplyRecord.intervention_id == intervention.id)
        .order_by(InterventionReplyRecord.id.desc())
        .first()
    )
    return {
        "intervention": {
            "id": intervention.id,
            "alert_id": intervention.alert_id,
            "handler": intervention.handler,
            "process_status": intervention.process_status,
        },
        "post": {
            "id": post.id,
            "title": post.title,
            "excerpt": post.excerpt,
            "content": _shorten(post.content, 1000),
            "author_name": post.author_name,
            "post_url": post.post_url,
            "publish_time": post.publish_time,
            "source_platform": post.source_platform,
        },
        "risk": {
            "risk_level": analysis.risk_level,
            "labels": analysis.labels,
            "risk_score": analysis.risk_score,
            "matched_keywords": analysis.matched_keywords,
            "suggestion": analysis.suggestion,
        },
        "reply": _reply_record_to_dict(record),
    }


@router.post("/{intervention_id}/reply/generate")
def generate_intervention_reply(intervention_id: int, db: Session = Depends(get_db)):
    intervention, alert, analysis, post = _resolve_context(db, intervention_id)
    record = _get_or_create_reply_record(db, intervention, post)
    draft, model_name, status = _call_deepseek(post, analysis)

    record.draft_content = draft
    record.final_content = draft
    record.model_provider = "deepseek"
    record.model_name = model_name
    record.prompt_version = PROMPT_VERSION
    record.generate_status = "success" if status == "success" else "fallback"
    record.review_status = "draft_ready"
    record.send_status = "not_sent"
    record.updated_at = _now()
    intervention.process_status = "draft_ready"
    intervention.updated_at = _now()
    db.commit()
    db.refresh(record)
    _log_operation(db, intervention.id, "generate", status, record.id)
    return {"message": "干预话术已生成", "reply": _reply_record_to_dict(record)}


@router.put("/{intervention_id}/reply/review")
def review_intervention_reply(intervention_id: int, data: InterventionReplyReview, db: Session = Depends(get_db)):
    intervention, alert, analysis, post = _resolve_context(db, intervention_id)
    record = _get_or_create_reply_record(db, intervention, post)
    action = data.review_action.lower().strip()
    if action not in {"approve", "reject", "save_only"}:
        raise HTTPException(status_code=400, detail="不支持的审核动作")
    if action in {"approve", "save_only"} and not data.final_content.strip():
        raise HTTPException(status_code=400, detail="请先填写干预回复文案")
    if action == "approve" and not data.is_public_comment_allowed:
        raise HTTPException(status_code=400, detail="未确认适合公开评论区发布，不能审核通过")

    record.final_content = data.final_content.strip()
    record.review_note = data.review_note
    record.reviewed_by = data.reviewer
    record.reviewed_at = _now()
    record.review_status = {"approve": "approved", "reject": "rejected", "save_only": "draft_ready"}[action]
    record.updated_at = _now()
    intervention.process_status = {"approve": "approved", "reject": "rejected", "save_only": "draft_ready"}[action]
    intervention.handler = data.reviewer or intervention.handler
    intervention.follow_up_note = data.review_note or intervention.follow_up_note
    intervention.updated_at = _now()
    db.commit()
    db.refresh(record)
    _log_operation(db, intervention.id, action, data.review_note or record.review_status, record.id, data.reviewer)
    return {"message": "审核状态已保存", "reply": _reply_record_to_dict(record)}


@router.post("/{intervention_id}/reply/mark-sent")
def mark_intervention_reply_sent(intervention_id: int, data: InterventionReplySent, db: Session = Depends(get_db)):
    intervention, alert, analysis, post = _resolve_context(db, intervention_id)
    record = _get_or_create_reply_record(db, intervention, post)
    if record.review_status != "approved":
        raise HTTPException(status_code=400, detail="回复尚未审核通过，不能标记发送")
    record.send_status = "sent"
    record.send_attempt_count += 1
    record.send_result_message = data.send_result_message
    record.external_reply_url = data.external_reply_url
    record.external_reply_id = data.external_reply_id
    record.sent_at = _now()
    record.sent_by = data.operator
    record.updated_at = _now()
    intervention.process_status = "sent"
    intervention.completed_at = _now()
    intervention.updated_at = _now()
    db.commit()
    db.refresh(record)
    _log_operation(db, intervention.id, "mark_sent", data.send_result_message, record.id, data.operator)
    return {"message": "发送结果已记录", "reply": _reply_record_to_dict(record)}


@router.get("/{intervention_id}/reply/logs")
def get_intervention_reply_logs(intervention_id: int, db: Session = Depends(get_db)):
    rows = (
        db.query(InterventionOperationLog)
        .filter(InterventionOperationLog.intervention_id == intervention_id)
        .order_by(InterventionOperationLog.id.desc())
        .all()
    )
    return [
        {
            "id": row.id,
            "reply_record_id": row.reply_record_id,
            "operation_type": row.operation_type,
            "operator": row.operator,
            "operation_detail": row.operation_detail,
            "operation_result": row.operation_result,
            "created_at": row.created_at,
        }
        for row in rows
    ]
