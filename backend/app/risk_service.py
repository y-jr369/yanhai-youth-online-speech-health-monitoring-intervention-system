import re
from typing import Iterable, List, Tuple

from .classifier import classify_text


RISK_LEVEL_LABELS = {
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


def make_excerpt(text: str, limit: int = 120) -> str:
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1] + "..."


def clean_model_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip())


def compose_model_text(title: str, content: str) -> str:
    title_clean = clean_model_text(title)
    content_clean = str(content or "").strip()
    normalized_content = clean_model_text(content_clean)

    if not title_clean:
        return normalized_content
    if not normalized_content:
        return title_clean
    if normalized_content == title_clean:
        return normalized_content

    merged_match = re.match(
        r"^(?:标题|鏍囬)[:：]\s*(.*?)\s*(?:正文|姝ｆ枃)[:：]\s*(.*)$",
        content_clean,
        flags=re.DOTALL,
    )
    if merged_match:
        merged_title = clean_model_text(merged_match.group(1))
        merged_body = clean_model_text(merged_match.group(2))
        if merged_body and merged_body != merged_title:
            return f"正文：{merged_body}\n\n标题：{merged_title or title_clean}"

    if title_clean in normalized_content:
        return normalized_content

    return f"正文：{normalized_content}\n\n标题：{title_clean}"


def analyze_text(text: str) -> Tuple[List[str], str, float, List[str], float]:
    return classify_text(text)


def analyze_post_text(title: str, content: str) -> Tuple[List[str], str, float, List[str], float]:
    return classify_text(compose_model_text(title, content))


def build_intervention_suggestion(risk_level: str, labels: Iterable[str]) -> str:
    labels = list(labels)
    if risk_level == "high":
        return "建议立即人工复核并发起校内干预流程，优先确认学生身份与近期状态。"
    if "depression" in labels or "anxiety" in labels or "self_harm" in labels:
        return "建议联系辅导员或心理中心进行跟进，重点关注连续性负面表达。"
    if "academic_stress" in labels or "social_withdrawal" in labels:
        return "建议结合学业和社交场景人工复核，判断是否需要早期关怀。"
    if risk_level == "medium":
        return "建议人工复核语境，确认是否存在持续辱骂、暴力威胁或情绪升级。"
    return "建议继续观察，如连续多次命中可升级复核。"


def risk_priority_from_level(risk_level: str) -> str:
    if risk_level == "high":
        return "urgent"
    if risk_level == "medium":
        return "normal"
    return "low"


def to_display_labels(labels: Iterable[str]) -> List[str]:
    return [LABEL_DISPLAY.get(label, label) for label in labels]
