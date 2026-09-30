"""Structured-output schemas for Gemini. Used as response_schema and for validation."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class ExperienceItem(BaseModel):
    title: str
    organization: Optional[str] = None
    duration_years: Optional[float] = Field(default=None, ge=0, le=60)
    highlights: list[str] = Field(default_factory=list)


class EducationItem(BaseModel):
    degree: Optional[str] = None
    field: Optional[str] = None
    institution: Optional[str] = None


class LanguageSkill(BaseModel):
    language: str
    level: Optional[str] = None


class Contacts(BaseModel):
    email: Optional[str] = None
    phone: Optional[str] = None


class Demographics(BaseModel):
    """Only what the resume states explicitly. Used for aggregate B2 metrics, never scoring."""

    gender: Literal["female", "male", "unknown"] = "unknown"
    age: Optional[int] = Field(default=None, ge=14, le=100)
    birth_year: Optional[int] = Field(default=None, ge=1920, le=2020)
    marital_status: Optional[str] = None
    nationality: Optional[str] = None
    photo_mentioned: bool = False


class CandidateProfile(BaseModel):
    full_name: Optional[str] = None
    contacts: Contacts = Field(default_factory=Contacts)
    headline: Optional[str] = None
    summary: str = ""
    total_experience_years: Optional[float] = Field(default=None, ge=0, le=60)
    skills: list[str] = Field(default_factory=list)
    experience: list[ExperienceItem] = Field(default_factory=list)
    education: list[EducationItem] = Field(default_factory=list)
    languages: list[LanguageSkill] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    resume_language: Literal["ru", "kk", "en", "other"] = "other"
    demographics: Demographics = Field(default_factory=Demographics)


Verdict = Literal["strong", "good", "partial", "weak"]


class FitAssessment(BaseModel):
    vacancy_id: int
    score: int = Field(ge=0, le=100)
    verdict: Verdict
    summary: str
    matched_skills: list[str] = Field(default_factory=list)
    missing_skills: list[str] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)
    concerns: list[str] = Field(default_factory=list)


class ScoringResult(BaseModel):
    assessments: list[FitAssessment]


class VacancyInput(BaseModel):
    id: int
    title: str
    text: str
