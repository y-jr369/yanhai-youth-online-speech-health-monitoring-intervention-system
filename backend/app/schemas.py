from typing import Optional

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str
    password: str


class ReviewRequest(BaseModel):
    review_note: str = ""
    alert_status: str = Field(default="reviewed")
    reviewed_by: str = Field(default="admin")


class ArchiveRequest(BaseModel):
    archive_reason: str = "handled"
    archived_by: str = "admin"


class InterventionCreate(BaseModel):
    alert_id: int
    campus_process_no: str
    student_name: str = ""
    student_no: str = ""
    process_status: str = "submitted"
    handler: str = "admin"
    match_note: str = ""
    follow_up_note: Optional[str] = ""


class InterventionUpdate(BaseModel):
    process_status: str
    handler: Optional[str] = None
    match_note: Optional[str] = None
    follow_up_note: Optional[str] = None


class InterventionReplyReview(BaseModel):
    final_content: str
    review_action: str = Field(default="approve")
    review_note: str = ""
    reviewer: str = "admin"
    is_public_comment_allowed: bool = True


class InterventionReplySent(BaseModel):
    operator: str = "admin"
    external_reply_url: str = ""
    external_reply_id: str = ""
    send_result_message: str = "已由人工发布或确认发送"


class CrawlSourceUpsert(BaseModel):
    name: str
    platform: str = "baidu_tieba"
    forum_name: str
    source_mode: str = "latest"
    source_label: str = ""
    description: str = ""
    enabled: bool = True
    crawl_interval_minutes: int = Field(default=60, ge=5, le=1440)
    max_threads: int = Field(default=200, ge=1, le=300)
    headless: bool = False


class CrawlSourceToggle(BaseModel):
    enabled: bool


class TiebaPreviewRequest(BaseModel):
    tieba_name: str
    source: str = "latest"
    source_label: str = "Baidu Tieba"
    max_threads: int = Field(default=200, ge=1, le=300)
    headless: bool = False


class TiebaRunRequest(BaseModel):
    source_id: Optional[int] = None
    tieba_name: str
    source: str = "latest"
    source_label: str = "Baidu Tieba"
    max_threads: int = Field(default=200, ge=1, le=300)
    headless: bool = False
