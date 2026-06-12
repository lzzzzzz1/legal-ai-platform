from typing import Literal

from pydantic import BaseModel, Field


RiskLevel = Literal["high", "medium", "low"]


class ReviewRisk(BaseModel):
    item: str = Field(..., description="Reviewed contract topic")
    level: RiskLevel
    original_text: str = Field(..., description="Exact contract text that triggered the risk")
    anchor_text: str | None = Field(default=None, description="Nearby exact text used to locate insertions")
    insert_after_text: str | None = Field(default=None, description="Exact text after which a missing clause should be inserted")
    risk: str
    suggestion: str
    laws: list[str] = Field(default_factory=list, description="Referenced legal articles")


class ReviewResponse(BaseModel):
    filename: str
    contract_type: str | None = Field(default=None, description="Detected enterprise contract type")
    contract_text: str | None = Field(default=None, description="Plain text extracted from the uploaded contract")
    risks: list[ReviewRisk]
