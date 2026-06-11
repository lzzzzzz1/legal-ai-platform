from typing import Literal

from pydantic import BaseModel, Field


RiskLevel = Literal["high", "medium", "low"]


class ReviewRisk(BaseModel):
    item: str = Field(..., description="Reviewed contract topic")
    level: RiskLevel
    original_text: str = Field(..., description="Exact contract text that triggered the risk")
    risk: str
    suggestion: str
    laws: list[str] = Field(default_factory=list, description="Referenced legal articles")


class ReviewResponse(BaseModel):
    filename: str
    risks: list[ReviewRisk]
