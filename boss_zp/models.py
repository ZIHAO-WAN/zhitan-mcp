"""Public tool results. Authentication material never belongs in these models."""

from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field

SearchStop = Literal["limit", "site_end", "no_new_ids", "deadline"]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Problem(Model):
    code: str
    message: str
    next_action: str | None = None


T = TypeVar("T")


class Result(Model, Generic[T]):
    ok: bool
    data: T | None = None
    error: Problem | None = None


class JobSummary(Model):
    job_id: str
    url: str
    title: str
    company: str | None = None
    location: str | None = None
    salary_text: str | None = None
    experience: str | None = None
    education: str | None = None
    hr_active: str | None = None
    company_info: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class SearchResult(Model):
    keyword: str
    city: str
    applied_filters: dict[str, str]
    returned_count: int
    stop_reason: SearchStop
    recorded_outcomes: dict[str, str] = Field(default_factory=dict)
    jobs: list[JobSummary]


class JobDetail(Model):
    job: JobSummary
    description: str
    contact_status: Literal["not_contacted", "already_contacted", "unknown"]


class SendResult(Model):
    outcome: Literal["sent", "already_contacted", "not_clicked", "rejected", "unknown"]
    reason: str | None = None
