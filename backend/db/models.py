from sqlalchemy import Column, String, Integer, Float, DateTime, Text, ForeignKey, JSON, Enum as SAEnum
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
import enum
from db.database import Base


class PlanEnum(str, enum.Enum):
    free = "free"
    pro = "pro"
    elite = "elite"


class User(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True)
    email = Column(String, unique=True, nullable=False, index=True)
    name = Column(String, nullable=False)
    hashed_password = Column(String, nullable=True)
    google_id = Column(String, nullable=True, unique=True, index=True)
    linkedin_id = Column(String, nullable=True)
    plan = Column(SAEnum(PlanEnum), default=PlanEnum.free, nullable=False)
    referral_code_used = Column(String, nullable=True, index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    saved_resumes = relationship("SavedResume", back_populates="user", cascade="all, delete-orphan")
    saved_jobs = relationship("SavedJob", back_populates="user", cascade="all, delete-orphan")
    adapted_resumes = relationship("AdaptedResume", back_populates="user", cascade="all, delete-orphan")
    job_searches = relationship("JobSearch", back_populates="user", cascade="all, delete-orphan")
    job_applications = relationship("JobApplication", back_populates="user", cascade="all, delete-orphan")
    apply_campaigns = relationship("ApplyCampaign", back_populates="user", cascade="all, delete-orphan")
    portal_credentials = relationship("PortalCredential", back_populates="user", cascade="all, delete-orphan")


class SavedResume(Base):
    __tablename__ = "saved_resumes"

    id = Column(String, primary_key=True)
    user_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    name = Column(String, nullable=False)
    resume_json = Column(JSON, nullable=False)
    template = Column(String, default="modern")
    ats_score = Column(Float, default=0.0)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    user = relationship("User", back_populates="saved_resumes")


class SavedJob(Base):
    __tablename__ = "saved_jobs"

    id = Column(String, primary_key=True)
    user_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    title = Column(String, nullable=False)
    company = Column(String, nullable=False)
    url = Column(String, nullable=True)
    status = Column(String, default="bookmarked")
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    user = relationship("User", back_populates="saved_jobs")


class AdaptedResume(Base):
    __tablename__ = "adapted_resumes"

    id = Column(String, primary_key=True)
    user_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    original_resume_id = Column(String, nullable=True)
    jd_text = Column(Text, nullable=True)
    company = Column(String, nullable=True)
    role = Column(String, nullable=True)
    adapted_json = Column(JSON, nullable=False)
    template = Column(String, default="modern")
    ats_before = Column(Float, default=0.0)
    ats_after = Column(Float, default=0.0)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    user = relationship("User", back_populates="adapted_resumes")


class JobSearch(Base):
    __tablename__ = "job_searches"

    id = Column(String, primary_key=True)
    user_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    query = Column(String, nullable=False)
    location = Column(String, nullable=True)
    results_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    user = relationship("User", back_populates="job_searches")


class JobApplication(Base):
    __tablename__ = "job_applications"

    id = Column(String, primary_key=True)
    user_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    job_id = Column(String, nullable=False, index=True)
    company = Column(String, nullable=False)
    role = Column(String, nullable=False)
    job_url = Column(String, nullable=True)
    platform = Column(String, nullable=True)
    match_score = Column(Integer, default=0)
    status = Column(String, default="applied")
    adapted_resume = Column(JSON, nullable=True)
    cover_letter = Column(Text, nullable=True)
    jd_snippet = Column(Text, nullable=True)
    applied_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    notes = Column(Text, nullable=True)

    user = relationship("User", back_populates="job_applications")


class ApplyCampaign(Base):
    __tablename__ = "apply_campaigns"

    id = Column(String, primary_key=True)
    user_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    name = Column(String, nullable=False)
    criteria = Column(JSON, nullable=False)
    status = Column(String, default="active")
    jobs_found = Column(Integer, default=0)
    jobs_applied = Column(Integer, default=0)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    user = relationship("User", back_populates="apply_campaigns")


class PortalCredential(Base):
    __tablename__ = "portal_credentials"

    id = Column(String, primary_key=True)
    user_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    portal = Column(String, nullable=False)      # linkedin | naukri | instahyre | indeed
    username = Column(String, nullable=False)    # email or phone
    password_enc = Column(String, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    user = relationship("User", back_populates="portal_credentials")


class AICache(Base):
    """Shared response cache — one Claude/JSearch call serves every user who asks the same thing."""
    __tablename__ = "ai_cache"

    key = Column(String, primary_key=True)              # sha256 of namespace + normalized input
    namespace = Column(String, nullable=False, index=True)  # job_search | interview_qs | company_intel | ...
    value_json = Column(JSON, nullable=False)
    expires_at = Column(DateTime, nullable=False, index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class AnalyticsEvent(Base):
    __tablename__ = "analytics_events"

    id = Column(String, primary_key=True)
    event = Column(String, nullable=False, index=True)   # page_view | feature_use | auth_event | upgrade_click
    user_id = Column(String, nullable=True, index=True)  # null = anonymous
    page = Column(String, nullable=True, index=True)     # /resume-builder, etc.
    feature = Column(String, nullable=True, index=True)  # resume_builder, job_finder, etc.
    metadata_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
