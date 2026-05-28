#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
국민건강보험 빅데이터 기반 BMI & WHtR 연구 (DM_WHTR)
HANA 로컬 추출 산출물 검증 스크립트

extract_hana.py 실행 후 생성되는 data/raw 계층형 CSV 산출물을 DuckDB 병합 및
코호트 구축 전에 빠르게 점검합니다.
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import pandas as pd


REQUIRED_COLUMNS = {
    "death": ["INDI_DSCM_NO", "DTH_ASSMD_DT"],
    "eligibility_checkup": [
        "INDI_DSCM_NO",
        "STD_YYYY",
        "SEX_TYPE",
        "BYEAR",
        "BZ_YYYY",
        "G1E_HGHT",
        "G1E_WGHT",
        "G1E_WSTC",
        "G1E_BMI",
        "SMK_CURR",
        "DRK_LEVEL",
        "PA_ACTIVE",
    ],
    "diagnosis": ["INDI_DSCM_NO", ("ICD_CODE", "MCEX_SICK_SYM", "MCEX_SICK_SYM1"), "MDCARE_STRT_DT"],
    "billing": ["CMN_KEY", "INDI_DSCM_NO", "MCARE_TP", "MDCARE_STRT_DT"],
}

NUMERIC_PLAUSIBILITY_RULES = {
    "G1E_HGHT": (100.0, 250.0, 0.01),
    "G1E_WGHT": (20.0, 300.0, 0.01),
    "G1E_WSTC": (40.0, 200.0, 0.01),
    "G1E_BMI": (10.0, 70.0, 0.01),
    "BYEAR": (1930.0, 2005.0, 0.0),
}

CATEGORICAL_DOMAIN_RULES = {
    "SMK_CURR": {0, 1},
    "DRK_LEVEL": {0, 1, 2},
    "PA_ACTIVE": {0, 1},
}


@dataclass
class ValidationResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    count: int = 0
    fraction: float = 0.0
    details: dict[str, Any] = field(default_factory=dict)

    def extend(self, other: "ValidationResult") -> None:
        self.errors.extend(other.errors)
        self.warnings.extend(other.warnings)
        self.ok = self.ok and other.ok


def _expected_relative_files(start_year: int, end_year: int) -> list[str]:
    expected = ["death/death_all.csv"]
    for year in range(start_year, end_year + 1):
        expected.append(f"eligibility_checkup/elig_checkup_{year}.csv")
        for month in range(1, 13):
            expected.append(f"diagnosis/diagnosis_{year}_{month:02d}.csv")
            expected.append(f"billing/billing_{year}_{month:02d}.csv")
    return expected


def check_file_completeness(raw_dir: str, start_year: int, end_year: int) -> list[str]:
    """Return expected data/raw files that are missing."""
    root = Path(raw_dir)
    return [rel for rel in _expected_relative_files(start_year, end_year) if not (root / rel).exists()]


def _norm_col_name(name: Any) -> str:
    return str(name).strip().upper()


def _canonicalize_columns(df: pd.DataFrame, required_cols: Iterable[Any] | None = None) -> pd.DataFrame:
    """Return a copy whose known columns use canonical project names."""
    canonical_names: list[str] = []
    if required_cols:
        for required in required_cols:
            if isinstance(required, tuple):
                canonical_names.extend(str(alias) for alias in required)
            else:
                canonical_names.append(str(required))
    canonical_names.extend(
        list(NUMERIC_PLAUSIBILITY_RULES)
        + list(CATEGORICAL_DOMAIN_RULES)
        + ["STD_YYYY", "BZ_YYYY", "MDCARE_STRT_DT", "DTH_ASSMD_DT", "CMN_KEY", "MCARE_TP"]
    )
    norm_to_canonical = {_norm_col_name(name): name for name in canonical_names}
    rename_map = {
        column: norm_to_canonical[_norm_col_name(column)]
        for column in df.columns
        if _norm_col_name(column) in norm_to_canonical
    }
    if not rename_map:
        return df
    return df.rename(columns=rename_map)


def check_schema_columns(df: pd.DataFrame, required_cols: list[Any], source_label: str = "") -> list[str]:
    """Return missing required columns. Tuple entries mean one of aliases is required."""
    existing = {_norm_col_name(column) for column in df.columns}
    missing: list[str] = []
    for required in required_cols:
        if isinstance(required, tuple):
            aliases = {_norm_col_name(alias) for alias in required}
            if existing.isdisjoint(aliases):
                missing.append("/".join(str(alias) for alias in required))
        elif _norm_col_name(required) not in existing:
            missing.append(str(required))
    return missing


def check_numeric_plausibility(
    df: pd.DataFrame,
    col: str,
    lo: float,
    hi: float,
    max_outlier_frac: float = 0.01,
) -> ValidationResult:
    if col not in df.columns:
        return ValidationResult(False, errors=[f"{col} column missing"])
    raw_non_null = df[col].notna()
    values = pd.to_numeric(df[col], errors="coerce")
    denominator = int(raw_non_null.sum())
    if denominator == 0:
        return ValidationResult(True, count=0, fraction=0.0)
    bad_mask = raw_non_null & (values.isna() | ~values.between(lo, hi, inclusive="both"))
    bad_count = int(bad_mask.sum())
    fraction = bad_count / denominator
    ok = fraction <= max_outlier_frac
    errors = [] if ok else [f"{col} out-of-range fraction {fraction:.4f} exceeds {max_outlier_frac:.4f}"]
    return ValidationResult(ok, errors=errors, count=bad_count, fraction=fraction)


def _normalize_domain_value(value: Any) -> Any:
    if pd.isna(value):
        return None
    if isinstance(value, str):
        value = value.strip()
        if value == "":
            return None
    numeric = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.notna(numeric) and float(numeric).is_integer():
        return int(numeric)
    return value


def check_categorical_domain(df: pd.DataFrame, col: str, valid_values: set[Any]) -> list[Any]:
    if col not in df.columns:
        return ["<MISSING_COLUMN>"]
    invalid: list[Any] = []
    for raw_value in df[col].dropna().unique().tolist():
        value = _normalize_domain_value(raw_value)
        if value is not None and value not in valid_values and value not in invalid:
            invalid.append(value)
    return invalid


def check_null_rates(df: pd.DataFrame, critical_cols: list[str], max_null_frac: float = 0.20) -> ValidationResult:
    details: dict[str, float] = {}
    errors: list[str] = []
    total = len(df)
    for col in critical_cols:
        if col not in df.columns:
            details[col] = 1.0
            errors.append(f"{col} column missing")
            continue
        frac = 0.0 if total == 0 else float(df[col].isna().mean())
        details[col] = frac
        if col == "INDI_DSCM_NO" and frac > 0:
            errors.append(f"{col} has null values ({frac:.4f})")
        elif frac > max_null_frac:
            errors.append(f"{col} null fraction {frac:.4f} exceeds {max_null_frac:.4f}")
    return ValidationResult(ok=not errors, errors=errors, details=details)


def check_pk_uniqueness(df: pd.DataFrame, pk_cols: list[str]) -> int:
    missing = [col for col in pk_cols if col not in df.columns]
    if missing:
        return len(df)
    return int(df.duplicated(subset=pk_cols, keep=False).sum())


def check_bz_yyyy_matches_std_yyyy(df: pd.DataFrame) -> int:
    if "BZ_YYYY" not in df.columns or "STD_YYYY" not in df.columns:
        return len(df)
    bz_num = pd.to_numeric(df["BZ_YYYY"], errors="coerce")
    std_num = pd.to_numeric(df["STD_YYYY"], errors="coerce")
    both_numeric = bz_num.notna() & std_num.notna()
    numeric_mismatch = both_numeric & (bz_num.astype("Int64") != std_num.astype("Int64"))
    bz = df["BZ_YYYY"].astype(str).str.strip()
    std = df["STD_YYYY"].astype(str).str.strip()
    string_mismatch = ~both_numeric & (bz != std)
    return int((numeric_mismatch | string_mismatch).sum())


def check_lifestyle_harmonization(df: pd.DataFrame) -> list[str]:
    problems: list[str] = []
    for col, valid_values in CATEGORICAL_DOMAIN_RULES.items():
        if col not in df.columns:
            problems.append(f"{col} missing")
            continue
        if df[col].isna().any():
            problems.append(f"{col} has null values")
        invalid = check_categorical_domain(df, col, valid_values)
        if invalid:
            problems.append(f"{col} invalid values: {invalid}")
    return problems


def _read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, encoding="utf-8-sig", low_memory=False)


def _validate_eligibility_file(path: Path) -> ValidationResult:
    result = ValidationResult(ok=True)
    df = _canonicalize_columns(_read_csv(path), REQUIRED_COLUMNS["eligibility_checkup"])
    missing = check_schema_columns(df, REQUIRED_COLUMNS["eligibility_checkup"], str(path))
    if missing:
        result.errors.append(f"{path}: missing columns {missing}")
    if len(df) == 0:
        result.errors.append(f"{path}: empty eligibility/checkup file")
    null_result = check_null_rates(df, ["INDI_DSCM_NO", "G1E_HGHT", "G1E_WSTC"], max_null_frac=0.20)
    result.extend(null_result)
    for col, (lo, hi, threshold) in NUMERIC_PLAUSIBILITY_RULES.items():
        if col in df.columns:
            plausibility = check_numeric_plausibility(df, col, lo, hi, threshold)
            result.extend(plausibility)
    for problem in check_lifestyle_harmonization(df):
        result.errors.append(f"{path}: {problem}")
    duplicate_count = check_pk_uniqueness(df, ["INDI_DSCM_NO", "STD_YYYY"])
    if duplicate_count:
        result.errors.append(f"{path}: duplicate (INDI_DSCM_NO, STD_YYYY) rows = {duplicate_count}")
    mismatch_count = check_bz_yyyy_matches_std_yyyy(df)
    if mismatch_count:
        result.errors.append(f"{path}: BZ_YYYY != STD_YYYY rows = {mismatch_count}")
    result.ok = not result.errors
    return result


def _validate_simple_file(path: Path, kind: str) -> ValidationResult:
    result = ValidationResult(ok=True)
    df = _canonicalize_columns(_read_csv(path), REQUIRED_COLUMNS[kind])
    missing = check_schema_columns(df, REQUIRED_COLUMNS[kind], str(path))
    if missing:
        result.errors.append(f"{path}: missing columns {missing}")
    null_cols = ["INDI_DSCM_NO"]
    if kind in {"diagnosis", "billing"}:
        null_cols.append("MDCARE_STRT_DT")
    null_result = check_null_rates(df, null_cols, max_null_frac=0.05)
    result.extend(null_result)
    result.ok = not result.errors
    return result


def validate_raw_dataset(raw_dir: str, start_year: int, end_year: int) -> ValidationResult:
    """Validate extracted HANA CSV files and return a structured report."""
    root = Path(raw_dir)
    result = ValidationResult(ok=True)

    missing = check_file_completeness(str(root), start_year, end_year)
    for rel in missing:
        result.errors.append(f"missing file: {rel}")

    death_path = root / "death" / "death_all.csv"
    if death_path.exists():
        result.extend(_validate_simple_file(death_path, "death"))

    for year in range(start_year, end_year + 1):
        path = root / "eligibility_checkup" / f"elig_checkup_{year}.csv"
        if path.exists():
            result.extend(_validate_eligibility_file(path))
        for month in range(1, 13):
            diag_path = root / "diagnosis" / f"diagnosis_{year}_{month:02d}.csv"
            if diag_path.exists():
                result.extend(_validate_simple_file(diag_path, "diagnosis"))
            billing_path = root / "billing" / f"billing_{year}_{month:02d}.csv"
            if billing_path.exists():
                result.extend(_validate_simple_file(billing_path, "billing"))

    result.ok = not result.errors
    return result


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate DM_WHTR data/raw CSV files after extract_hana.py")
    parser.add_argument("--raw-dir", default=os.path.join("data", "raw"), help="raw CSV root directory")
    parser.add_argument("--start-year", type=int, default=2009)
    parser.add_argument("--end-year", type=int, default=2023)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    report = validate_raw_dataset(args.raw_dir, args.start_year, args.end_year)
    print("=" * 70)
    print("DM_WHTR HANA 추출 산출물 검증 결과")
    print("=" * 70)
    if report.errors:
        print("[FAIL] Errors:")
        for message in report.errors:
            print(f"  - {message}")
    if report.warnings:
        print("[WARN] Warnings:")
        for message in report.warnings:
            print(f"  - {message}")
    if report.ok:
        print("[OK] data/raw 검증 통과")
        return 0
    print("[x] data/raw 검증 실패")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
