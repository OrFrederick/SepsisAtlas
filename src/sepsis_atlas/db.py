from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Column,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    DateTime,
    JSON,
    create_engine,
    event,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

from sepsis_atlas.config import DB_PATH


class Base(DeclarativeBase):
    pass


class Paper(Base):
    __tablename__ = "papers"

    file_name: Mapped[str] = mapped_column(String, primary_key=True)
    doi: Mapped[Optional[str]] = mapped_column(String, index=True)
    pmid: Mapped[Optional[str]] = mapped_column(String)
    title: Mapped[Optional[str]] = mapped_column(Text)
    year: Mapped[Optional[int]] = mapped_column(Integer)
    journal: Mapped[Optional[str]] = mapped_column(String)
    authors: Mapped[Optional[str]] = mapped_column(Text)
    pdf_hash: Mapped[Optional[str]] = mapped_column(String)
    parser_version: Mapped[Optional[str]] = mapped_column(String)
    source: Mapped[Optional[str]] = mapped_column(String)  # provided / pubmed / manual
    ingest_ts: Mapped[Optional[datetime]] = mapped_column(DateTime, default=datetime.utcnow)
    run_id: Mapped[Optional[str]] = mapped_column(String)
    pipeline_version: Mapped[Optional[str]] = mapped_column(String)


class StudyCohort(Base):
    __tablename__ = "study_cohort"

    cohort_id: Mapped[str] = mapped_column(String, primary_key=True)
    paper_ref: Mapped[Optional[str]] = mapped_column(String)
    file_name: Mapped[Optional[str]] = mapped_column(ForeignKey("papers.file_name"), index=True)
    doi: Mapped[Optional[str]] = mapped_column(String)
    encounters_period: Mapped[Optional[str]] = mapped_column(String)
    population_location: Mapped[Optional[str]] = mapped_column(String)
    data_sets: Mapped[Optional[str]] = mapped_column(String)
    study_design: Mapped[Optional[str]] = mapped_column(Text)
    population_description: Mapped[Optional[str]] = mapped_column(Text)
    cohort_label: Mapped[Optional[str]] = mapped_column(String)
    cohort_size_n: Mapped[Optional[str]] = mapped_column(String)
    cohort_characteristics: Mapped[Optional[str]] = mapped_column(Text)
    cohort_characteristics_timepoint: Mapped[Optional[str]] = mapped_column(String)
    mortality_rate_pct: Mapped[Optional[float]] = mapped_column(Float)
    mortality_timepoint: Mapped[Optional[str]] = mapped_column(String)

    anchor_page: Mapped[Optional[int]] = mapped_column(Integer)
    anchor_bbox: Mapped[Optional[dict]] = mapped_column(JSON)
    anchor_text: Mapped[Optional[str]] = mapped_column(Text)
    anchor_section: Mapped[Optional[str]] = mapped_column(String)

    extractor_model: Mapped[Optional[str]] = mapped_column(String)
    prompt_id: Mapped[Optional[str]] = mapped_column(String)
    verifier_verdict: Mapped[Optional[str]] = mapped_column(String)
    verifier_score: Mapped[Optional[float]] = mapped_column(Float)
    verifier_rationale: Mapped[Optional[str]] = mapped_column(Text)
    field_status: Mapped[Optional[str]] = mapped_column(String)  # ok / not_reported / partial

    extracted_ts: Mapped[Optional[datetime]] = mapped_column(DateTime, default=datetime.utcnow)
    run_id: Mapped[Optional[str]] = mapped_column(String, index=True)
    pipeline_version: Mapped[Optional[str]] = mapped_column(String)
    schema_version: Mapped[Optional[str]] = mapped_column(String)


class PredictorModel(Base):
    __tablename__ = "predictor_model"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    cohort_id: Mapped[Optional[str]] = mapped_column(ForeignKey("study_cohort.cohort_id"), index=True)

    predictors: Mapped[Optional[str]] = mapped_column(Text)
    timing_predictor_measurement: Mapped[Optional[str]] = mapped_column(String)
    outcome: Mapped[Optional[str]] = mapped_column(String)
    model_specification: Mapped[Optional[str]] = mapped_column(Text)
    effect_size_str: Mapped[Optional[str]] = mapped_column(Text)

    effect_type: Mapped[Optional[str]] = mapped_column(String)  # OR / HR / RR / AUC / cutoff / mean_diff
    effect_value: Mapped[Optional[float]] = mapped_column(Float)
    ci_lo: Mapped[Optional[float]] = mapped_column(Float)
    ci_hi: Mapped[Optional[float]] = mapped_column(Float)
    p_value: Mapped[Optional[float]] = mapped_column(Float)

    auc: Mapped[Optional[float]] = mapped_column(Float)
    auc_ci_lo: Mapped[Optional[float]] = mapped_column(Float)
    auc_ci_hi: Mapped[Optional[float]] = mapped_column(Float)
    sens: Mapped[Optional[float]] = mapped_column(Float)
    spec: Mapped[Optional[float]] = mapped_column(Float)
    ppv: Mapped[Optional[float]] = mapped_column(Float)
    npv: Mapped[Optional[float]] = mapped_column(Float)
    c_index: Mapped[Optional[float]] = mapped_column(Float)
    cutoff: Mapped[Optional[str]] = mapped_column(String)

    outcome_type: Mapped[Optional[str]] = mapped_column(String, index=True)
    outcome_window_days: Mapped[Optional[int]] = mapped_column(Integer, index=True)
    predictor_canonical: Mapped[Optional[str]] = mapped_column(String, index=True)

    anchor_page: Mapped[Optional[int]] = mapped_column(Integer)
    anchor_bbox: Mapped[Optional[dict]] = mapped_column(JSON)
    anchor_text: Mapped[Optional[str]] = mapped_column(Text)
    anchor_section: Mapped[Optional[str]] = mapped_column(String)

    extractor_model: Mapped[Optional[str]] = mapped_column(String)
    prompt_id: Mapped[Optional[str]] = mapped_column(String)
    verifier_verdict: Mapped[Optional[str]] = mapped_column(String)
    verifier_score: Mapped[Optional[float]] = mapped_column(Float)
    verifier_rationale: Mapped[Optional[str]] = mapped_column(Text)
    field_status: Mapped[Optional[str]] = mapped_column(String)

    extracted_ts: Mapped[Optional[datetime]] = mapped_column(DateTime, default=datetime.utcnow)
    cost_usd: Mapped[Optional[float]] = mapped_column(Float)
    tokens_in: Mapped[Optional[int]] = mapped_column(Integer)
    tokens_out: Mapped[Optional[int]] = mapped_column(Integer)
    latency_ms: Mapped[Optional[int]] = mapped_column(Integer)

    pipeline_version: Mapped[Optional[str]] = mapped_column(String)
    schema_version: Mapped[Optional[str]] = mapped_column(String)
    run_id: Mapped[Optional[str]] = mapped_column(String, index=True)


class StudyPhenotypeSummary(Base):
    __tablename__ = "study_phenotype_summary"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    paper_ref: Mapped[Optional[str]] = mapped_column(String, index=True)
    file_name: Mapped[Optional[str]] = mapped_column(ForeignKey("papers.file_name"), index=True)
    country: Mapped[Optional[str]] = mapped_column(String)
    setting: Mapped[Optional[str]] = mapped_column(String)
    sample_size_n: Mapped[Optional[str]] = mapped_column(String)
    sepsis_definition: Mapped[Optional[str]] = mapped_column(String)
    clustering_method: Mapped[Optional[str]] = mapped_column(String)
    n_clusters: Mapped[Optional[int]] = mapped_column(Integer)
    clustering_variables: Mapped[Optional[str]] = mapped_column(Text)
    external_assignment_feasible: Mapped[Optional[str]] = mapped_column(String)
    cohort_id: Mapped[Optional[str]] = mapped_column(ForeignKey("study_cohort.cohort_id"))

    anchor_page: Mapped[Optional[int]] = mapped_column(Integer)
    anchor_bbox: Mapped[Optional[dict]] = mapped_column(JSON)
    anchor_text: Mapped[Optional[str]] = mapped_column(Text)
    anchor_section: Mapped[Optional[str]] = mapped_column(String)

    extractor_model: Mapped[Optional[str]] = mapped_column(String)
    prompt_id: Mapped[Optional[str]] = mapped_column(String)
    verifier_verdict: Mapped[Optional[str]] = mapped_column(String)
    verifier_score: Mapped[Optional[float]] = mapped_column(Float)
    verifier_rationale: Mapped[Optional[str]] = mapped_column(Text)
    field_status: Mapped[Optional[str]] = mapped_column(String)

    extracted_ts: Mapped[Optional[datetime]] = mapped_column(DateTime, default=datetime.utcnow)
    run_id: Mapped[Optional[str]] = mapped_column(String, index=True)
    pipeline_version: Mapped[Optional[str]] = mapped_column(String)
    schema_version: Mapped[Optional[str]] = mapped_column(String)


class PhenotypeCluster(Base):
    __tablename__ = "phenotype_cluster"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    study_phenotype_summary_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("study_phenotype_summary.id"), index=True
    )
    paper_ref: Mapped[Optional[str]] = mapped_column(String, index=True)
    cluster_label: Mapped[Optional[str]] = mapped_column(String)
    cluster_size_n: Mapped[Optional[str]] = mapped_column(String)
    key_features: Mapped[Optional[str]] = mapped_column(Text)
    clinical_description: Mapped[Optional[str]] = mapped_column(Text)
    outcomes: Mapped[Optional[str]] = mapped_column(Text)
    notes: Mapped[Optional[str]] = mapped_column(Text)

    anchor_page: Mapped[Optional[int]] = mapped_column(Integer)
    anchor_bbox: Mapped[Optional[dict]] = mapped_column(JSON)
    anchor_text: Mapped[Optional[str]] = mapped_column(Text)
    anchor_section: Mapped[Optional[str]] = mapped_column(String)

    extractor_model: Mapped[Optional[str]] = mapped_column(String)
    prompt_id: Mapped[Optional[str]] = mapped_column(String)
    verifier_verdict: Mapped[Optional[str]] = mapped_column(String)
    verifier_score: Mapped[Optional[float]] = mapped_column(Float)
    verifier_rationale: Mapped[Optional[str]] = mapped_column(Text)
    field_status: Mapped[Optional[str]] = mapped_column(String)

    extracted_ts: Mapped[Optional[datetime]] = mapped_column(DateTime, default=datetime.utcnow)
    run_id: Mapped[Optional[str]] = mapped_column(String, index=True)
    pipeline_version: Mapped[Optional[str]] = mapped_column(String)
    schema_version: Mapped[Optional[str]] = mapped_column(String)


class LLMCall(Base):
    __tablename__ = "llm_calls"

    call_id: Mapped[str] = mapped_column(String, primary_key=True)
    ts: Mapped[Optional[datetime]] = mapped_column(DateTime, default=datetime.utcnow)
    stage: Mapped[Optional[str]] = mapped_column(String, index=True)
    row_id: Mapped[Optional[str]] = mapped_column(String, index=True)
    paper_id: Mapped[Optional[str]] = mapped_column(String, index=True)
    query_id: Mapped[Optional[str]] = mapped_column(String, index=True)

    model: Mapped[Optional[str]] = mapped_column(String)
    prompt_id: Mapped[Optional[str]] = mapped_column(String)
    prompt_hash: Mapped[Optional[str]] = mapped_column(String)
    tokens_in: Mapped[Optional[int]] = mapped_column(Integer)
    tokens_out: Mapped[Optional[int]] = mapped_column(Integer)
    cost_usd: Mapped[Optional[float]] = mapped_column(Float)
    latency_ms: Mapped[Optional[int]] = mapped_column(Integer)
    retry_count: Mapped[Optional[int]] = mapped_column(Integer)
    parent_call_id: Mapped[Optional[str]] = mapped_column(String)

    input_path: Mapped[Optional[str]] = mapped_column(String)
    output_path: Mapped[Optional[str]] = mapped_column(String)


# "cleared" is a reserved tombstone verdict written when a reviewer clears
# their own override (we supersede instead of delete to keep the audit chain).
# It is never offered as a selectable option in the popover, so a reviewer
# cannot reach it via free text — the read paths treat it as "no active review".
HUMAN_REVIEW_VERDICTS = ("approve", "reject", "flag", "cleared")
# Keep in sync with `HumanReviewTable` in web/src/lib/humanReview.ts.
HUMAN_REVIEW_TABLES = (
    "study_cohort",
    "predictor_model",
    "study_phenotype_summary",
    "phenotype_cluster",
)


class HumanReview(Base):
    """Sidecar override of the automatic verifier verdict.

    Append-only: a revision becomes a new row whose `superseded_by` on the
    *previous* active row gets set to its own `review_id`. The "current" review
    for a target is the row with `superseded_by IS NULL`. Orphan reviews
    (referencing a row that no longer exists after re-extraction) are tolerated
    on purpose so reviewer judgement is not destroyed by an extraction rerun.
    """

    __tablename__ = "human_reviews"

    review_id: Mapped[str] = mapped_column(String, primary_key=True)
    table_name: Mapped[str] = mapped_column(String, index=True)
    row_id: Mapped[str] = mapped_column(String, index=True)
    human_verdict: Mapped[str] = mapped_column(String)
    human_rationale: Mapped[Optional[str]] = mapped_column(Text)
    reviewer: Mapped[Optional[str]] = mapped_column(String)
    reviewed_ts: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    superseded_by: Mapped[Optional[str]] = mapped_column(
        String, ForeignKey("human_reviews.review_id"), nullable=True, index=True
    )

    __table_args__ = (
        Index(
            "ix_human_reviews_target_active",
            "table_name",
            "row_id",
            "superseded_by",
        ),
    )


class Query(Base):
    __tablename__ = "queries"

    query_id: Mapped[str] = mapped_column(String, primary_key=True)
    ts: Mapped[Optional[datetime]] = mapped_column(DateTime, default=datetime.utcnow)
    nl_text: Mapped[Optional[str]] = mapped_column(Text)
    parsed_intent: Mapped[Optional[dict]] = mapped_column(JSON)
    sql_emitted: Mapped[Optional[str]] = mapped_column(Text)
    n_rows_returned: Mapped[Optional[int]] = mapped_column(Integer)
    total_cost_usd: Mapped[Optional[float]] = mapped_column(Float)
    total_latency_ms: Mapped[Optional[int]] = mapped_column(Integer)


def get_engine(url: str | None = None):
    """Create a SQLAlchemy engine with SQLite tuned for concurrent extraction.

    WAL journal mode lets readers stay live while a writer holds the lock,
    busy_timeout makes the driver retry for 30s instead of immediately
    raising "database is locked" when another worker is mid-commit, and
    check_same_thread=False permits the ThreadPoolExecutor in
    extract.run_extract to share the engine across worker threads.
    """
    eng_url = url or f"sqlite:///{DB_PATH}"
    is_sqlite = eng_url.startswith("sqlite")
    connect_args: dict = {}
    if is_sqlite:
        connect_args = {"timeout": 30.0, "check_same_thread": False}
    engine = create_engine(eng_url, future=True, connect_args=connect_args)
    if is_sqlite:
        @event.listens_for(engine, "connect")
        def _set_sqlite_pragmas(dbapi_conn, _):  # noqa: ARG001
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=30000")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.close()
    return engine


def init_db(url: str | None = None):
    engine = get_engine(url)
    Base.metadata.create_all(engine)
    return engine


def get_session(url: str | None = None):
    engine = get_engine(url)
    return sessionmaker(bind=engine, expire_on_commit=False)


if __name__ == "__main__":
    init_db()
    print(f"Initialized DB at {DB_PATH}")
