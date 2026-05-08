"""Translate non-English parsed papers to English.

Walks ``data/papers/parsed/*.json``. For each paper, decides per item
whether translation is needed (script-based detection). English papers are
skipped fast with no LLM calls. Non-English papers are translated item by
item, with originals preserved under ``original_*`` fields.

Usage::

    python -m parse.run_translate                    # all papers
    python -m parse.run_translate --only Kochkin_2021.json Kozlov_2022.json
    python -m parse.run_translate --force            # retranslate already-translated
    python -m parse.run_translate --dry-run          # detect only
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

from sepsis_atlas.config import PAPERS_PARSED

from parse.translate import paper_language, translate_parsed


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Translate non-English parsed papers to English.")
    ap.add_argument("--only", nargs="+", default=None, help="Only translate these JSON filenames.")
    ap.add_argument("--force", action="store_true", help="Re-translate already-translated papers.")
    ap.add_argument("--dry-run", action="store_true", help="Detect language only, do not translate.")
    ap.add_argument(
        "--no-backup",
        action="store_true",
        help="Skip writing a .pre-translate.json sibling backup.",
    )
    args = ap.parse_args(argv)

    if not PAPERS_PARSED.exists():
        print(f"ERROR: {PAPERS_PARSED} does not exist.", file=sys.stderr)
        return 2

    paths = sorted(PAPERS_PARSED.glob("*.json"))
    paths = [p for p in paths if not p.name.endswith(".pre-translate.json")]
    if args.only:
        wanted = {Path(name).name for name in args.only}
        paths = [p for p in paths if p.name in wanted]
    if not paths:
        print("No parsed papers to process.", file=sys.stderr)
        return 1

    n_skip_en = 0
    n_skip_already = 0
    n_translated = 0
    n_failed = 0

    for p in paths:
        try:
            parsed = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            print(f"[FAIL] {p.name}: cannot read JSON ({e})", file=sys.stderr)
            n_failed += 1
            continue

        if parsed.get("translated_to_en") and not args.force:
            print(
                f"[SKIP] {p.name:30} already translated "
                f"(source={parsed.get('source_language', '?')})",
                file=sys.stderr,
            )
            n_skip_already += 1
            continue

        lang = paper_language(parsed)
        if lang == "en":
            # Mark the parsed JSON as English-checked so the next run is a no-op.
            if not parsed.get("translated_to_en"):
                parsed["translated_to_en"] = False
                parsed["source_language"] = "en"
                p.write_text(json.dumps(parsed, ensure_ascii=False))
            print(f"[EN  ] {p.name:30} no translation needed", file=sys.stderr)
            n_skip_en += 1
            continue

        if args.dry_run:
            print(f"[DRY ] {p.name:30} would translate (lang={lang})", file=sys.stderr)
            continue

        if not args.no_backup:
            backup = p.with_suffix(".pre-translate.json")
            if not backup.exists() or args.force:
                shutil.copy2(p, backup)

        t0 = time.time()
        try:
            translated = translate_parsed(parsed, paper_id=p.stem)
        except Exception as e:  # noqa: BLE001
            print(f"[FAIL] {p.name}: {type(e).__name__}: {e}", file=sys.stderr)
            n_failed += 1
            continue

        p.write_text(json.dumps(translated, ensure_ascii=False))
        elapsed = time.time() - t0
        print(
            f"[OK  ] {p.name:30} lang={lang} -> en  "
            f"sections={len(translated.get('sections', []))} "
            f"tables={len(translated.get('tables', []))} "
            f"{elapsed:.1f}s",
            file=sys.stderr,
            flush=True,
        )
        n_translated += 1

    print(
        f"\nDone. translated={n_translated}  english={n_skip_en}  "
        f"already_translated={n_skip_already}  failed={n_failed}",
        file=sys.stderr,
    )
    return 0 if n_failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
