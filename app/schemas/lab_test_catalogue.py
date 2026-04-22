"""
Schemas for Test Catalogue Management screen.
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


TestCatalogueStatus = Literal["ACTIVE", "INACTIVE"]


class TestCategoryChip(BaseModel):
    category_name: str
    test_count: int


class TestCatalogueRow(BaseModel):
    test_code: str
    test_name: str
    category: str
    sample_type: str
    turnaround_time: str
    price_inr: float
    parameters_count: int
    status: TestCatalogueStatus


class TestCatalogueSummary(BaseModel):
    active_tests: int = 0
    categories: int = 0
    total_parameters: int = 0


class TestCatalogueMeta(BaseModel):
    generated_at: datetime
    live_data: bool = False
    demo_data: bool = False


class TestCatalogueListResponse(BaseModel):
    meta: TestCatalogueMeta
    category_chips: List[TestCategoryChip] = Field(default_factory=list)
    summary: TestCatalogueSummary
    rows: List[TestCatalogueRow] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class AddCategoryRequest(BaseModel):
    category_name: str = Field(..., min_length=2, max_length=120)


class AddCategoryResponse(BaseModel):
    message: str
    category_name: str


class TestParameterRequest(BaseModel):
    parameter_name: str = Field(..., min_length=1, max_length=120)
    unit: Optional[str] = Field(None, max_length=30)
    reference_range: Optional[str] = Field(None, max_length=120)


class AddTestRequest(BaseModel):
    test_name: str = Field(..., min_length=2, max_length=160)
    test_code: Optional[str] = Field(None, max_length=40, description="If absent, backend may auto-generate.")
    category: str = Field(..., min_length=2, max_length=120)
    sample_type: str = Field(..., min_length=2, max_length=60)
    turnaround_time: str = Field(..., min_length=1, max_length=60)
    price_inr: float = Field(..., ge=0)
    test_instructions: Optional[str] = Field(None, max_length=2000)
    parameters: List[TestParameterRequest] = Field(default_factory=list)


class AddTestResponse(BaseModel):
    message: str
    test_code: str
    test_name: str


class BulkActionResponse(BaseModel):
    message: str
    action: str

