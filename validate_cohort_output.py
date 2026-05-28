#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DM_WHTR final analytical cohort output validator.

Run after build_cohort.py or merge_local_data_duckdb.py and before
analyze_trajectories.py. This is a stop-the-line QA gate for the final
analytical cohort CSVs.
"""

from __future__ import annotations

import argparse
import os
import re
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

EXPECTED_FOLLOWUP_DAYS = 4016
ENDPOINTS = ("CVD", "Stroke", "T2DM", "CKD", "Any")
BASE_ENDPOINTS = ("CVD", "Stroke", "T2DM", "CKD")

REQUIRED_COLUMNS = [
    'ID', 'Sex', 'Age_Baseline', 'Baseline_Year', 'Height', 'Weight',
    'BMI_Baseline', 'Waist_Baseline', 'WHtR_Baseline', 'Obese_Group',
    'BMI_Slope', 'WHtR_Slope', 'Waist_Slope', 'Income_Decile',
    'Smoking_Status', 'BP_Systolic', 'BP_Diastolic', 'Glucose',
    'Cholesterol', 'eGFR', 'Hypertension_Med', 'Dyslipidemia_Med',
    'Event_CVD', 'Time_CVD', 'Event_Stroke', 'Time_Stroke',
    'Event_T2DM', 'Time_T2DM', 'Event_CKD', 'Time_CKD',
    'Event_Any', 'Time_Any',
]

MODEL3_COVARIATE_COUNT = 12


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
        self.details.update(other.details)
        self.ok = self.ok and other.ok


def _result(errors: list[str] | None = None, warnings: list[str] | None = None, **details: Any) -> ValidationResult:
    errors = errors or []
    warnings = warnings or []
    return ValidationResult(ok=not errors, errors=errors, warnings=warnings, details=details)


def _as_numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors='coerce')


def _is_integer_like(series: pd.Series) -> pd.Series:
    numeric = _as_numeric(series)
    return numeric.notna() & (numeric % 1 == 0)


def check_required_columns(df: pd.DataFrame) -> ValidationResult:
    missing = [col for col in REQUIRED_COLUMNS if col not in df.columns]
    return _result([f"missing required columns: {', '.join(missing)}"] if missing else [], missing_columns=missing)


def check_identifier_integrity(df: pd.DataFrame) -> ValidationResult:
    errors: list[str] = []
    if 'ID' not in df.columns:
        return _result(["missing required column: ID"])
    duplicate_count = int(df['ID'].duplicated().sum())
    if duplicate_count:
        errors.append(f"ID duplicate rows found: {duplicate_count}")
    id_strings = df['ID'].astype(str)
    invalid_mask = ~id_strings.str.fullmatch(r'[0-9a-f]{16}')
    if invalid_mask.any():
        examples = id_strings[invalid_mask].head(5).tolist()
        errors.append(f"ID must be 16 lowercase hex hashed identifiers; invalid examples: {examples}")
    return _result(errors, duplicate_count=duplicate_count, invalid_id_count=int(invalid_mask.sum()))


def check_sample_size(df: pd.DataFrame, min_n: int = 500) -> ValidationResult:
    if len(df) < min_n:
        return _result([f"sample size below minimum: n={len(df)} < {min_n}"], n=len(df), min_n=min_n)
    return _result(n=len(df), min_n=min_n)


def check_event_time_logic(df: pd.DataFrame, endpoint: str, expected_followup_days: int = EXPECTED_FOLLOWUP_DAYS) -> ValidationResult:
    event_col = f'Event_{endpoint}'
    time_col = f'Time_{endpoint}'
    errors: list[str] = []
    missing = [col for col in (event_col, time_col) if col not in df.columns]
    if missing:
        return _result([f"{endpoint}: missing columns: {', '.join(missing)}"])

    event = _as_numeric(df[event_col])
    time = _as_numeric(df[time_col])
    invalid_event = ~(event.isin([0, 1]))
    if invalid_event.any():
        errors.append(f"{event_col}: Event must be 0/1; invalid rows={int(invalid_event.sum())}")
    non_positive_time = time <= 0
    if non_positive_time.any():
        errors.append(f"{time_col}: Time must be > 0; invalid rows={int(non_positive_time.sum())}")
    too_long_time = time > expected_followup_days
    if too_long_time.any():
        errors.append(f"{time_col}: Time exceeds {expected_followup_days}; invalid rows={int(too_long_time.sum())}")
    event_at_max = (event == 1) & (time >= expected_followup_days)
    if event_at_max.any():
        errors.append(f"{endpoint}: Event=1 must have Time < {expected_followup_days}; invalid rows={int(event_at_max.sum())}")
    return _result(errors)


def check_all_event_time_logic(df: pd.DataFrame) -> ValidationResult:
    result = _result()
    for endpoint in ENDPOINTS:
        result.extend(check_event_time_logic(df, endpoint))
    return result


def check_composite_endpoint_consistency(df: pd.DataFrame) -> ValidationResult:
    errors: list[str] = []
    needed = [f'Event_{ep}' for ep in BASE_ENDPOINTS] + [f'Time_{ep}' for ep in BASE_ENDPOINTS] + ['Event_Any', 'Time_Any']
    missing = [col for col in needed if col not in df.columns]
    if missing:
        return _result([f"composite endpoint missing columns: {', '.join(missing)}"])

    base_events = pd.DataFrame({ep: _as_numeric(df[f'Event_{ep}']) for ep in BASE_ENDPOINTS})
    base_times = pd.DataFrame({ep: _as_numeric(df[f'Time_{ep}']) for ep in BASE_ENDPOINTS})
    expected_any_event = (base_events.sum(axis=1) > 0).astype(float)
    actual_any_event = _as_numeric(df['Event_Any'])
    event_mismatch = actual_any_event != expected_any_event
    if event_mismatch.any():
        errors.append(f"Event_Any inconsistent with component endpoints; invalid rows={int(event_mismatch.sum())}")

    expected_any_time = []
    for idx in df.index:
        event_eps = [ep for ep in BASE_ENDPOINTS if base_events.loc[idx, ep] == 1]
        if event_eps:
            expected_any_time.append(float(base_times.loc[idx, event_eps].min()))
        else:
            expected_any_time.append(float(base_times.loc[idx, list(BASE_ENDPOINTS)].min()))
    expected_any_time_series = pd.Series(expected_any_time, index=df.index)
    actual_any_time = _as_numeric(df['Time_Any'])
    time_mismatch = actual_any_time != expected_any_time_series
    if time_mismatch.any():
        errors.append(f"Time_Any inconsistent with earliest component endpoint time; invalid rows={int(time_mismatch.sum())}")
    return _result(errors)


def check_obese_group_consistency(df: pd.DataFrame) -> ValidationResult:
    needed = ['BMI_Baseline', 'WHtR_Baseline', 'Obese_Group']
    missing = [col for col in needed if col not in df.columns]
    if missing:
        return _result([f"Obese_Group consistency missing columns: {', '.join(missing)}"])
    bmi = _as_numeric(df['BMI_Baseline'])
    whtr = _as_numeric(df['WHtR_Baseline'])
    expected = pd.Series(index=df.index, dtype='int64')
    expected[(bmi < 25.0) & (whtr < 0.5)] = 1
    expected[(bmi < 25.0) & (whtr >= 0.5)] = 2
    expected[(bmi >= 25.0) & (whtr < 0.5)] = 3
    expected[(bmi >= 25.0) & (whtr >= 0.5)] = 4
    actual = _as_numeric(df['Obese_Group'])
    mismatch = actual != expected
    if mismatch.any():
        return _result([f"Obese_Group inconsistent with BMI_Baseline/WHtR_Baseline; invalid rows={int(mismatch.sum())}"])
    return _result()


def check_covariate_domains(df: pd.DataFrame) -> ValidationResult:
    errors: list[str] = []
    missing_cells = int(df[REQUIRED_COLUMNS].isna().sum().sum()) if all(c in df.columns for c in REQUIRED_COLUMNS) else int(df.isna().sum().sum())
    if missing_cells:
        errors.append(f"missing/null cohort values are not allowed; missing cells={missing_cells}")

    range_rules = {
        'Age_Baseline': (20, 45),
        'Baseline_Year': (2009, 2012),
        'Height': (130, 200),
        'Weight': (30, 200),
        'BMI_Baseline': (10, 70),
        'Waist_Baseline': (40, 200),
        'WHtR_Baseline': (0.3, 0.8),
        'BP_Systolic': (60, 250),
        'BP_Diastolic': (30, 150),
        'Glucose': (40, 600),
        'Cholesterol': (50, 600),
        'eGFR': (1, 200),
        'Income_Decile': (1, 10),
        'BMI_Slope': (-10, 10),
        'WHtR_Slope': (-0.2, 0.2),
        'Waist_Slope': (-50, 50),
    }
    for col, (lo, hi) in range_rules.items():
        if col not in df.columns:
            errors.append(f"{col}: missing required domain column")
            continue
        values = _as_numeric(df[col])
        invalid = values.isna() | ~np.isfinite(values) | (values < lo) | (values > hi)
        if invalid.any():
            errors.append(f"{col}: outside allowed range {lo}-{hi}; invalid rows={int(invalid.sum())}")

    domain_rules = {
        'Sex': {1, 2},
        'Obese_Group': {1, 2, 3, 4},
        'Smoking_Status': {1, 2, 3},
        'Hypertension_Med': {0, 1},
        'Dyslipidemia_Med': {0, 1},
    }
    for col, valid in domain_rules.items():
        if col not in df.columns:
            errors.append(f"{col}: missing required domain column")
            continue
        values = _as_numeric(df[col])
        invalid = values.isna() | ~values.isin(valid) | ~_is_integer_like(df[col])
        if invalid.any():
            errors.append(f"{col}: outside allowed domain {sorted(valid)}; invalid rows={int(invalid.sum())}")

    if 'Income_Decile' in df.columns:
        invalid_income_integer = ~_is_integer_like(df['Income_Decile'])
        if invalid_income_integer.any():
            errors.append(f"Income_Decile: must be integer-like; invalid rows={int(invalid_income_integer.sum())}")

    if 'BP_Systolic' in df.columns and 'BP_Diastolic' in df.columns:
        bp_inverted = _as_numeric(df['BP_Systolic']) < _as_numeric(df['BP_Diastolic'])
        if bp_inverted.any():
            errors.append(f"BP_Systolic must be >= BP_Diastolic; invalid rows={int(bp_inverted.sum())}")

    return _result(errors)


def check_lag1y_subset_relationship(df_full: pd.DataFrame, df_lag: pd.DataFrame) -> ValidationResult:
    errors: list[str] = []
    if 'ID' not in df_full.columns or 'ID' not in df_lag.columns:
        return _result(["lag1y subset check requires ID columns in both cohorts"])
    full_ids = set(df_full['ID'].astype(str))
    lag_ids = set(df_lag['ID'].astype(str))
    extras = sorted(lag_ids - full_ids)
    if extras:
        errors.append(f"lag1y cohort must be subset of full cohort; extra IDs examples={extras[:5]}")

    early_event_ids = set(df_full.loc[(_as_numeric(df_full['Event_Any']) == 1) & (_as_numeric(df_full['Time_Any']) <= 365), 'ID'].astype(str))
    retained_early_events = sorted(early_event_ids & lag_ids)
    if retained_early_events:
        errors.append(f"lag1y cohort retained early Event_Any<=365 IDs; examples={retained_early_events[:5]}")

    early_non_event_ids = set(df_full.loc[(_as_numeric(df_full['Event_Any']) == 0) & (_as_numeric(df_full['Time_Any']) <= 365), 'ID'].astype(str))
    removed_early_non_events = sorted(early_non_event_ids - lag_ids)
    if removed_early_non_events:
        errors.append(f"lag1y cohort removed early non-event/censoring IDs; examples={removed_early_non_events[:5]}")

    expected_removed = len(early_event_ids)
    actual_removed = len(full_ids - lag_ids)
    if actual_removed != expected_removed:
        errors.append(f"lag1y removed count mismatch: expected early events={expected_removed}, actual removed={actual_removed}")

    common_ids = sorted(lag_ids & full_ids)
    compare_cols = [col for col in REQUIRED_COLUMNS if col in df_full.columns and col in df_lag.columns and col != 'ID']
    if common_ids and compare_cols:
        full_cmp = df_full[df_full['ID'].astype(str).isin(common_ids)].set_index('ID')[compare_cols].sort_index()
        lag_cmp = df_lag[df_lag['ID'].astype(str).isin(common_ids)].set_index('ID')[compare_cols].sort_index()
        if not full_cmp.equals(lag_cmp):
            errors.append("lag1y cohort changed values for retained IDs")

    return _result(errors, expected_removed=expected_removed, actual_removed=actual_removed)


def check_cox_preconditions(
    df: pd.DataFrame,
    endpoint: str,
    min_total_events: int = 20,
    min_events_per_group: int = 0,
    model_covariate_count: int = MODEL3_COVARIATE_COUNT,
) -> ValidationResult:
    errors: list[str] = []
    warnings: list[str] = []
    event_col = f'Event_{endpoint}'
    if event_col not in df.columns:
        return _result([f"{event_col}: missing event column"])
    events = _as_numeric(df[event_col])
    total_events = int((events == 1).sum())
    if total_events < min_total_events:
        errors.append(f"{endpoint}: total events below minimum for Cox sanity: events={total_events} < {min_total_events}")
    epv = total_events / model_covariate_count if model_covariate_count else float('inf')
    if epv < 10:
        warnings.append(f"{endpoint}: Model 3 EPV below recommended 10: EPV={epv:.2f}")

    if 'Obese_Group' not in df.columns:
        errors.append("Obese_Group missing for per-group Cox sanity")
    else:
        present_groups = set(_as_numeric(df['Obese_Group']).dropna().astype(int).tolist())
        missing_groups = sorted({1, 2, 3, 4} - present_groups)
        if missing_groups:
            errors.append(f"Obese_Group missing groups: {missing_groups}")
        if min_events_per_group > 0:
            for group in sorted(present_groups & {1, 2, 3, 4}):
                group_events = int(((events == 1) & (_as_numeric(df['Obese_Group']) == group)).sum())
                if group_events < min_events_per_group:
                    errors.append(f"{endpoint}: Obese_Group {group} events below minimum: {group_events} < {min_events_per_group}")
    return _result(errors, warnings, total_events=total_events, epv=epv)


def validate_cohort_outputs(
    cohort_path: str = os.path.join('data', 'cohort_analytical.csv'),
    lag1y_path: str | None = os.path.join('data', 'cohort_analytical_lag1y.csv'),
    min_n: int = 500,
    min_total_events: int = 20,
    min_events_per_group: int = 0,
) -> ValidationResult:
    result = _result()
    if not os.path.exists(cohort_path):
        result.extend(_result([f"cohort file not found: {cohort_path}"]))
        return result

    try:
        df_full = pd.read_csv(cohort_path, encoding='utf-8-sig', dtype={'ID': 'string'})
    except Exception as exc:
        return _result([f"failed to read cohort file {cohort_path}: {exc}"])

    result.details['n_full'] = len(df_full)
    for check in (
        check_required_columns,
        check_identifier_integrity,
        lambda df: check_sample_size(df, min_n=min_n),
        check_covariate_domains,
        check_obese_group_consistency,
        check_all_event_time_logic,
        check_composite_endpoint_consistency,
    ):
        result.extend(check(df_full))

    for endpoint in ENDPOINTS:
        # Treat per-group event-count stop rules as a primary pre-Cox sanity gate
        # for the composite endpoint by default. Endpoint-specific sparse-event
        # decisions are still available through check_cox_preconditions(...),
        # but should not block a global file-validity run unless explicitly
        # evaluated endpoint-by-endpoint.
        result.extend(check_cox_preconditions(
            df_full,
            endpoint,
            min_total_events=min_total_events if endpoint == 'Any' else 0,
            min_events_per_group=min_events_per_group if endpoint == 'Any' else 0,
        ))

    if lag1y_path:
        if not os.path.exists(lag1y_path):
            result.extend(_result([f"lag1y cohort file not found: {lag1y_path}"]))
        else:
            try:
                df_lag = pd.read_csv(lag1y_path, encoding='utf-8-sig', dtype={'ID': 'string'})
                result.details['n_lag1y'] = len(df_lag)
                result.extend(check_required_columns(df_lag))
                result.extend(check_identifier_integrity(df_lag))
                result.extend(check_lag1y_subset_relationship(df_full, df_lag))
            except Exception as exc:
                result.extend(_result([f"failed to read lag1y cohort file {lag1y_path}: {exc}"]))

    result.ok = not result.errors
    return result


def _print_result(result: ValidationResult) -> None:
    if result.ok:
        print("[OK] Final analytical cohort QA passed.")
    else:
        print("[FAIL] Final analytical cohort QA failed.")
    if result.details:
        print("\nDetails:")
        for key, value in sorted(result.details.items()):
            print(f"  - {key}: {value}")
    if result.errors:
        print("\nErrors:")
        for err in result.errors:
            print(f"  - {err}")
    if result.warnings:
        print("\nWarnings:")
        for warn in result.warnings:
            print(f"  - {warn}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate DM_WHTR final analytical cohort CSV outputs before Cox analysis")
    parser.add_argument('--cohort', default=os.path.join('data', 'cohort_analytical.csv'), help='full analytical cohort CSV path')
    parser.add_argument('--lag1y', default=os.path.join('data', 'cohort_analytical_lag1y.csv'), help='lag-time sensitivity cohort CSV path')
    parser.add_argument('--min-n', type=int, default=500, help='minimum full cohort row count')
    parser.add_argument('--min-total-events', type=int, default=20, help='minimum Event_Any count for stop-the-line Cox sanity')
    parser.add_argument('--min-events-per-group', type=int, default=0, help='minimum events per Obese_Group for each endpoint; 0 disables stop rule')
    args = parser.parse_args()

    result = validate_cohort_outputs(args.cohort, args.lag1y, args.min_n, args.min_total_events, args.min_events_per_group)
    _print_result(result)
    return 0 if result.ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
