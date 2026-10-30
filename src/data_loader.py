"""Load the CSV files: read as text -> validate -> convert to proper types.

Reading everything as text first matters: if pandas guessed types itself, a
typo like "2026-13-45" or "twelve" would silently turn into a blank and we
could not tell the user which value was wrong.

This module has no Streamlit code, so it can be tested and reused on its own.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, time
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from src import config
from src.validation import (DATE_FORMAT, ERROR, SCHEMAS, ValidationIssue, validate_dataset,
                            validate_relationships)


@dataclass
class DataBundle:
    """Everything the app needs: typed data, validation results and metadata."""
    frames: Dict[str, pd.DataFrame]
    issues: List[ValidationIssue]
    metadata: dict
    as_of: datetime              # the moment the data represents ("now" for the app)
    directory: Path
    is_demo: bool = field(default=True)

    @property
    def has_errors(self) -> bool:
        return any(issue.severity == ERROR for issue in self.issues)


def read_raw(name: str, directory: Path) -> pd.DataFrame:
    path = directory / config.DATASET_FILES[name]
    # keep_default_na=False keeps blanks as "" instead of guessing NaN.
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def to_typed(name: str, raw: pd.DataFrame) -> pd.DataFrame:
    """Convert validated text columns to dates/numbers. Bad values become NaT/NaN."""
    schema = SCHEMAS[name]
    df = raw.copy()
    for col in df.columns:
        df[col] = df[col].str.strip()
    for col in schema.date_columns:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col].replace("", None), format=DATE_FORMAT, errors="coerce")
    for col in schema.numeric_columns:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col].replace("", None), errors="coerce")
    return df


def read_metadata(directory: Path) -> dict:
    path = directory / config.METADATA_FILE
    if path.exists():
        return json.loads(path.read_text())
    return {}


def resolve_as_of(metadata: dict, is_demo: bool) -> datetime:
    """Demo data is a frozen snapshot, so "now" is its as-of date at briefing time.
    Live data uses the real current time."""
    if is_demo and metadata.get("as_of_date"):
        day = datetime.strptime(metadata["as_of_date"], "%Y-%m-%d").date()
        return datetime.combine(day, time(config.BRIEFING_HOUR))
    return datetime.now()


def load_all(directory: Optional[Path] = None, mode: Optional[str] = None) -> DataBundle:
    mode = (mode or config.DATA_MODE).lower()
    directory = Path(directory) if directory else config.data_dir(mode)
    frames: Dict[str, pd.DataFrame] = {}
    issues: List[ValidationIssue] = []

    for name in config.DATASET_FILES:
        try:
            raw = read_raw(name, directory)
        except FileNotFoundError:
            issues.append(ValidationIssue(name, ERROR, "",
                                          f"File '{config.DATASET_FILES[name]}' not found in {directory}."))
            raw = pd.DataFrame(columns=list(SCHEMAS[name].columns))
        issues.extend(validate_dataset(name, raw))
        frames[name] = to_typed(name, raw)

    issues.extend(validate_relationships(frames))
    metadata = read_metadata(directory)
    is_demo = mode == "demo"
    return DataBundle(frames=frames, issues=issues, metadata=metadata,
                      as_of=resolve_as_of(metadata, is_demo), directory=directory, is_demo=is_demo)
