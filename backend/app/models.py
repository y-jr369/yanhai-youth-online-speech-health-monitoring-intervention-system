from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from .database import Base


def utcnow():
    return datetime.utcnow()


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, nullable=False, index=True)
    password = Column(String(100), nullable=False)
    role = Column(String(20), default="admin", nullable=False)
    created_at = Column(DateTime, default=utcnow, nullable=False)


class CrawlSource(Base):
    __tablename__ = "crawl_sources"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(120), unique=True, nullable=False, index=True)
    platform = Column(String(50), default="baidu_tieba", nullable=False, index=True)
    forum_name = Column(String(120), nullable=False, index=True)
    source_mode = Column(String(20), default="latest", nullable=False)
    source_label = Column(String(120), default="", nullable=False)
    description = Column(String(255), default="", nullable=False)
    enabled = Column(Boolean, default=True, nullable=False, index=True)
    crawl_interval_minutes = Column(Integer, default=60, nullable=False)
    max_threads = Column(Integer, default=10, nullable=False)
    headless = Column(Boolean, default=True, nullable=False)
    last_crawled_at = Column(DateTime, nullable=True)
    next_crawl_at = Column(DateTime, nullable=True, index=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    posts = relationship("Post", back_populates="crawl_source")
    jobs = relationship("CrawlJob", back_populates="crawl_source")


class CrawlJob(Base):
    __tablename__ = "crawl_jobs"

    id = Column(Integer, primary_key=True, index=True)
    source_id = Column(Integer, ForeignKey("crawl_sources.id"), nullable=True, index=True)
    job_type = Column(String(20), default="manual", nullable=False)
    status = Column(String(20), default="running", nullable=False, index=True)
    inserted_count = Column(Integer, default=0, nullable=False)
    duplicated_count = Column(Integer, default=0, nullable=False)
    failed_count = Column(Integer, default=0, nullable=False)
    total_threads = Column(Integer, default=0, nullable=False)
    summary = Column(Text, default="", nullable=False)
    started_at = Column(DateTime, default=utcnow, nullable=False, index=True)
    finished_at = Column(DateTime, nullable=True)

    crawl_source = relationship("CrawlSource", back_populates="jobs")


class Post(Base):
    __tablename__ = "posts"

    id = Column(Integer, primary_key=True, index=True)
    post_id = Column(String(80), unique=True, nullable=False, index=True)
    source_id = Column(Integer, ForeignKey("crawl_sources.id"), nullable=True, index=True)
    source = Column(String(120), default="Baidu Tieba", nullable=False, index=True)
    source_platform = Column(String(50), default="baidu_tieba", nullable=False)
    forum_name = Column(String(120), default="", nullable=False)
    title = Column(String(255), default="", nullable=False)
    excerpt = Column(String(500), default="", nullable=False)
    author_name = Column(String(120), default="", nullable=False)
    content = Column(Text, nullable=False)
    publish_time = Column(DateTime, default=utcnow, nullable=False, index=True)
    collected_at = Column(DateTime, default=utcnow, nullable=False, index=True)
    post_url = Column(String(255), default="#", nullable=False, index=True)

    crawl_source = relationship("CrawlSource", back_populates="posts")
    analysis = relationship("AnalysisResult", back_populates="post", uselist=False)


class AnalysisResult(Base):
    __tablename__ = "analysis_results"

    id = Column(Integer, primary_key=True, index=True)
    post_id_fk = Column(Integer, ForeignKey("posts.id"), nullable=False, index=True)
    labels = Column(String(255), default="normal", nullable=False)
    risk_level = Column(String(20), default="low", nullable=False, index=True)
    risk_score = Column(Float, default=0.0, nullable=False)
    matched_keywords = Column(String(255), default="", nullable=False)
    confidence = Column(Float, default=0.0, nullable=False)
    suggestion = Column(Text, default="", nullable=False)
    created_at = Column(DateTime, default=utcnow, nullable=False)

    post = relationship("Post", back_populates="analysis")
    alert = relationship("AlertRecord", back_populates="analysis", uselist=False)


class AlertRecord(Base):
    __tablename__ = "alerts"

    id = Column(Integer, primary_key=True, index=True)
    analysis_id = Column(Integer, ForeignKey("analysis_results.id"), nullable=False, index=True)
    alert_status = Column(String(20), default="pending", nullable=False, index=True)
    priority = Column(String(20), default="normal", nullable=False)
    review_note = Column(Text, default="", nullable=False)
    reviewed_by = Column(String(50), default="", nullable=False)
    created_at = Column(DateTime, default=utcnow, nullable=False, index=True)
    reviewed_at = Column(DateTime, nullable=True)

    analysis = relationship("AnalysisResult", back_populates="alert")


class ArchiveRecord(Base):
    __tablename__ = "archives"

    id = Column(Integer, primary_key=True, index=True)
    alert_id = Column(Integer, nullable=False, index=True)
    archive_reason = Column(String(255), default="resolved", nullable=False)
    archived_by = Column(String(50), default="admin", nullable=False)
    archived_at = Column(DateTime, default=utcnow, nullable=False, index=True)


class InterventionRecord(Base):
    __tablename__ = "interventions"

    id = Column(Integer, primary_key=True, index=True)
    alert_id = Column(Integer, nullable=False, index=True)
    campus_process_no = Column(String(100), nullable=False, index=True)
    student_name = Column(String(50), default="", nullable=False)
    student_no = Column(String(50), default="", nullable=False)
    process_status = Column(String(50), default="submitted", nullable=False, index=True)
    handler = Column(String(50), default="admin", nullable=False)
    match_note = Column(Text, default="", nullable=False)
    follow_up_note = Column(Text, default="", nullable=False)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)
    completed_at = Column(DateTime, nullable=True)


class InterventionReplyRecord(Base):
    __tablename__ = "intervention_reply_records"

    id = Column(Integer, primary_key=True, index=True)
    intervention_id = Column(Integer, ForeignKey("interventions.id"), nullable=False, index=True)
    alert_id = Column(Integer, nullable=False, index=True)
    post_id = Column(Integer, nullable=False, index=True)
    source_platform = Column(String(50), default="baidu_tieba", nullable=False)
    target_url = Column(String(255), default="", nullable=False)
    draft_content = Column(Text, default="", nullable=False)
    final_content = Column(Text, default="", nullable=False)
    model_provider = Column(String(50), default="deepseek", nullable=False)
    model_name = Column(String(80), default="", nullable=False)
    prompt_version = Column(String(50), default="public_comment_v1", nullable=False)
    generate_status = Column(String(30), default="not_generated", nullable=False, index=True)
    review_status = Column(String(30), default="not_reviewed", nullable=False, index=True)
    review_note = Column(Text, default="", nullable=False)
    reviewed_by = Column(String(50), default="", nullable=False)
    reviewed_at = Column(DateTime, nullable=True)
    send_status = Column(String(30), default="not_sent", nullable=False, index=True)
    send_attempt_count = Column(Integer, default=0, nullable=False)
    send_result_message = Column(Text, default="", nullable=False)
    external_reply_url = Column(String(255), default="", nullable=False)
    external_reply_id = Column(String(120), default="", nullable=False)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)
    sent_at = Column(DateTime, nullable=True)
    sent_by = Column(String(50), default="", nullable=False)

    intervention = relationship("InterventionRecord")


class InterventionOperationLog(Base):
    __tablename__ = "intervention_operation_logs"

    id = Column(Integer, primary_key=True, index=True)
    intervention_id = Column(Integer, nullable=False, index=True)
    reply_record_id = Column(Integer, nullable=True, index=True)
    operation_type = Column(String(40), nullable=False, index=True)
    operator = Column(String(50), default="admin", nullable=False)
    operation_detail = Column(Text, default="", nullable=False)
    operation_result = Column(String(40), default="success", nullable=False)
    created_at = Column(DateTime, default=utcnow, nullable=False, index=True)
