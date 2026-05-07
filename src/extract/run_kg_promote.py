"""One-shot CLI that runs both lateral-promote stages.

Usage:
    python -m extract.run_kg_promote

Reads from the Neo4j and SQL configured via the standard env vars
(NEO4J_URI/USER/PASSWORD/DATABASE, SEPSIS_DB_URL) used elsewhere in
the project. Idempotent — safe to re-run.
"""

from __future__ import annotations

import argparse
import os

from api.backends.kg_store import KGStore
from extract.kg_lateral_promote import run as lateral_promote
from extract.kg_phenotype_mirror import run as phenotype_mirror
from sepsis_atlas.db import get_engine


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-lateral",
        action="store_true",
        help="Skip the Predictor/Outcome/StatMethod/Setting promote stage.",
    )
    parser.add_argument(
        "--skip-phenotype",
        action="store_true",
        help="Skip the PhenotypeCluster mirror stage.",
    )
    args = parser.parse_args()

    store = KGStore()
    store.bootstrap_schema()
    try:
        if not args.skip_lateral:
            counts = lateral_promote(store)
            print(f"[lateral_promote] {counts}", flush=True)
        if not args.skip_phenotype:
            engine = get_engine(os.getenv("SEPSIS_DB_URL"))
            counts = phenotype_mirror(store, engine)
            print(f"[phenotype_mirror] {counts}", flush=True)
    finally:
        store.close()


if __name__ == "__main__":
    main()
