from typing import Iterable, List


RISK_LEVEL_LABELS = {
    "low": "低风险",
    "medium": "中风险",
    "high": "高风险",
}

ALERT_STATUS_LABELS = {
    "pending": "待处理",
    "reviewed": "已复核",
    "archived": "已归档",
}

ALERT_STATUS_ALIASES = {
    "pending": "pending",
    "待处理": "pending",
    "reviewed": "reviewed",
    "已复核": "reviewed",
    "已处理": "reviewed",
    "archived": "archived",
    "已归档": "archived",
}

INTERVENTION_STATUS_LABELS = {
    "submitted": "已提交",
    "matching": "匹配学生中",
    "contacted": "已进入干预",
    "closed": "已完成",
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


def to_display_labels(labels: Iterable[str]) -> List[str]:
    return [LABEL_DISPLAY.get(label, label) for label in labels]


def normalize_alert_status(status: str) -> str:
    value = str(status or "").strip()
    return ALERT_STATUS_ALIASES.get(value, value or "pending")
