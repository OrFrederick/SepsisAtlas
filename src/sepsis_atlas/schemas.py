from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class Anchor(BaseModel):
    page: int
    bbox: list[float] = Field(description="[x0, y0, x1, y1] in PDF points")
    text: str = Field(description="Verbatim source span")
    section: Optional[str] = None


FieldStatus = Literal["ok", "not_reported", "partial"]


class StudyCohortOut(BaseModel):
    cohort_id: str = Field(description="Composite 'Author Year [Dataset] [CohortLabel]'")
    paper_ref: str
    encounters_period: Optional[str] = None
    population_location: Optional[str] = None
    data_sets: Optional[str] = None
    study_design: Optional[str] = None
    population_description: Optional[str] = None
    cohort_label: Optional[str] = None
    cohort_size_n: Optional[str] = None
    cohort_characteristics: Optional[str] = None
    cohort_characteristics_timepoint: Optional[str] = None
    mortality_rate_pct: Optional[float] = None
    mortality_timepoint: Optional[str] = None
    field_status: FieldStatus = "ok"
    anchor: Anchor


class PredictorModelOut(BaseModel):
    cohort_id: str
    predictors: str
    timing_predictor_measurement: Optional[str] = None
    outcome: str
    model_specification: Optional[str] = None
    effect_size_str: str

    effect_type: Optional[
        Literal["OR", "HR", "RR", "AUC", "AUROC", "cutoff", "mean_diff", "c_index", "other"]
    ] = None
    effect_value: Optional[float] = None
    ci_lo: Optional[float] = None
    ci_hi: Optional[float] = None
    p_value: Optional[float] = None

    auc: Optional[float] = None
    auc_ci_lo: Optional[float] = None
    auc_ci_hi: Optional[float] = None
    sens: Optional[float] = None
    spec: Optional[float] = None
    ppv: Optional[float] = None
    npv: Optional[float] = None
    c_index: Optional[float] = None
    cutoff: Optional[str] = None

    outcome_type: Optional[
        Literal["mortality", "readmission", "los", "organ_failure", "other"]
    ] = None
    outcome_window_days: Optional[int] = None
    predictor_canonical: Optional[str] = None

    field_status: FieldStatus = "ok"
    anchor: Anchor


class CohortEnumResponse(BaseModel):
    cohorts: list[StudyCohortOut]


class PredictorExtractResponse(BaseModel):
    rows: list[PredictorModelOut]


class VerifierResponse(BaseModel):
    verdict: Literal["ok", "partial", "reject"]
    score: float = Field(ge=0.0, le=1.0)
    rationale: str


class IntentParse(BaseModel):
    outcome_type: Optional[str] = None
    outcome_window_days: Optional[int] = None
    population: dict = Field(default_factory=dict)
    predictor: Optional[str] = None
    intent: Literal["ranking", "lookup", "comparison", "summary"] = "lookup"
