import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
PAPERS_RAW = DATA_DIR / "papers" / "raw"
PAPERS_PARSED = DATA_DIR / "papers" / "parsed"
GROUND_TRUTH = DATA_DIR / "ground_truth"
LOGS_DIR = ROOT / "logs"
RUNS_DIR = ROOT / "runs"
STATIC_DIR = ROOT / "static"
DB_PATH = ROOT / "db.sqlite"

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")

MODEL_EXTRACT = os.getenv("MODEL_EXTRACT", "anthropic/claude-sonnet-4.5")
MODEL_VERIFY = os.getenv("MODEL_VERIFY", "anthropic/claude-haiku-4.5")
MODEL_INTENT = os.getenv("MODEL_INTENT", "anthropic/claude-haiku-4.5")
MODEL_NARRATIVE = os.getenv("MODEL_NARRATIVE", "anthropic/claude-haiku-4.5")

LANGFUSE_PUBLIC_KEY = os.getenv("LANGFUSE_PUBLIC_KEY", "")
LANGFUSE_SECRET_KEY = os.getenv("LANGFUSE_SECRET_KEY", "")
LANGFUSE_HOST = os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com")

PIPELINE_VERSION = os.getenv("PIPELINE_VERSION", "0.1.0")
SCHEMA_VERSION = os.getenv("SCHEMA_VERSION", "1")

LOGS_DIR.mkdir(exist_ok=True)
RUNS_DIR.mkdir(exist_ok=True)
PAPERS_PARSED.mkdir(parents=True, exist_ok=True)
