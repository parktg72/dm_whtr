#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DM_WHTR final analytical cohort output validator tests.

These tests define the stop-the-line QA gate that must pass after
merge_local_data_duckdb.py/build_cohort.py and before analyze_trajectories.py.
"""

import os
import tempfile
import unittest

import pandas as pd

from validate_cohort_output import (
    EXPECTED_FOLLOWUP_DAYS,
    ValidationResult,
    check_cox_preconditions,
    check_event_time_logic,
    check_identifier_integrity,
    check_lag1y_subset_relationship,
    check_obese_group_consistency,
    check_required_columns,
    check_covariate_domains,
    check_composite_endpoint_consistency,
    validate_cohort_outputs,
)


def make_valid_cohort(n=80):
    rows = []
    group_specs = {
        1: (22.0, 0.45),
        2: (23.0, 0.55),
        3: (27.0, 0.45),
        4: (28.0, 0.56),
    }
    for i in range(n):
        group = (i % 4) + 1
        bmi, whtr = group_specs[group]
        has_cvd = i < 24
        has_t2dm = 24 <= i < 48
        time_cvd = 700 + i if has_cvd else EXPECTED_FOLLOWUP_DAYS
        time_t2dm = 900 + i if has_t2dm else EXPECTED_FOLLOWUP_DAYS
        any_event = int(has_cvd or has_t2dm)
        any_time = min([t for e, t in [(has_cvd, time_cvd), (has_t2dm, time_t2dm)] if e]) if any_event else EXPECTED_FOLLOWUP_DAYS
        rows.append({
            'ID': f'{i:016x}',
            'Sex': 1 if i % 2 == 0 else 2,
            'Age_Baseline': 20 + (i % 26),
            'Baseline_Year': 2009 + (i % 4),
            'Height': 160.0 + (i % 20),
            'Weight': 65.0 + (i % 25),
            'BMI_Baseline': bmi,
            'Waist_Baseline': round((160.0 + (i % 20)) * whtr, 1),
            'WHtR_Baseline': whtr,
            'Obese_Group': group,
            'BMI_Slope': -0.2 + (i % 5) * 0.1,
            'WHtR_Slope': -0.003 + (i % 5) * 0.001,
            'Waist_Slope': -0.5 + (i % 5) * 0.2,
            'Income_Decile': (i % 10) + 1,
            'Smoking_Status': (i % 3) + 1,
            'BP_Systolic': 110.0 + (i % 35),
            'BP_Diastolic': 70.0 + (i % 20),
            'Glucose': 85.0 + (i % 45),
            'Cholesterol': 170.0 + (i % 80),
            'eGFR': 60.0 + (i % 65),
            'Hypertension_Med': i % 2,
            'Dyslipidemia_Med': (i + 1) % 2,
            'Event_CVD': int(has_cvd),
            'Time_CVD': time_cvd,
            'Event_Stroke': 0,
            'Time_Stroke': EXPECTED_FOLLOWUP_DAYS,
            'Event_T2DM': int(has_t2dm),
            'Time_T2DM': time_t2dm,
            'Event_CKD': 0,
            'Time_CKD': EXPECTED_FOLLOWUP_DAYS,
            'Event_Any': any_event,
            'Time_Any': any_time,
        })
    return pd.DataFrame(rows)


class TestColumnAndIdentifierIntegrity(unittest.TestCase):
    def test_required_columns_reports_missing_columns(self):
        df = make_valid_cohort().drop(columns=['WHtR_Baseline', 'Event_Any'])
        result = check_required_columns(df)
        self.assertIsInstance(result, ValidationResult)
        self.assertFalse(result.ok)
        self.assertIn('WHtR_Baseline', '\n'.join(result.errors))
        self.assertIn('Event_Any', '\n'.join(result.errors))

    def test_identifier_integrity_rejects_duplicate_or_raw_like_ids(self):
        df = make_valid_cohort()
        df.loc[1, 'ID'] = df.loc[0, 'ID']
        df.loc[2, 'ID'] = '123456789'
        result = check_identifier_integrity(df)
        self.assertFalse(result.ok)
        joined = '\n'.join(result.errors)
        self.assertIn('duplicate', joined.lower())
        self.assertIn('16', joined)


class TestEventTimeAndCompositeLogic(unittest.TestCase):
    def test_event_time_logic_rejects_invalid_event_values_and_times(self):
        df = make_valid_cohort()
        df.loc[0, 'Event_CVD'] = 2
        df.loc[1, 'Time_CVD'] = 0
        df.loc[2, 'Time_CVD'] = EXPECTED_FOLLOWUP_DAYS + 1
        df.loc[4, 'Event_CVD'] = 1
        df.loc[4, 'Time_CVD'] = EXPECTED_FOLLOWUP_DAYS
        result = check_event_time_logic(df, 'CVD')
        self.assertFalse(result.ok)
        self.assertGreaterEqual(len(result.errors), 4)

    def test_composite_endpoint_consistency_checks_any_event_and_min_time(self):
        df = make_valid_cohort()
        df.loc[0, 'Event_Any'] = 0
        df.loc[1, 'Time_Any'] = df.loc[1, 'Time_CVD'] + 100
        result = check_composite_endpoint_consistency(df)
        self.assertFalse(result.ok)
        self.assertIn('Event_Any', '\n'.join(result.errors))
        self.assertIn('Time_Any', '\n'.join(result.errors))

    def test_event_time_logic_allows_death_censoring_for_non_events(self):
        df = make_valid_cohort()
        df.loc[0, 'Event_CVD'] = 0
        df.loc[0, 'Time_CVD'] = 200
        result = check_event_time_logic(df, 'CVD')
        self.assertTrue(result.ok, result.errors)

    def test_composite_no_event_time_can_equal_component_censoring_minimum(self):
        df = make_valid_cohort()
        for ep in ['CVD', 'Stroke', 'T2DM', 'CKD']:
            df.loc[0, f'Event_{ep}'] = 0
            df.loc[0, f'Time_{ep}'] = 300
        df.loc[0, 'Event_Any'] = 0
        df.loc[0, 'Time_Any'] = 300
        result = check_composite_endpoint_consistency(df)
        self.assertTrue(result.ok, result.errors)


class TestExposureAndCovariateDomains(unittest.TestCase):
    def test_obese_group_consistency_rejects_mismatched_bmi_whtr_group(self):
        df = make_valid_cohort()
        df.loc[0, 'BMI_Baseline'] = 30.0
        df.loc[0, 'WHtR_Baseline'] = 0.60
        df.loc[0, 'Obese_Group'] = 1
        result = check_obese_group_consistency(df)
        self.assertFalse(result.ok)
        self.assertIn('Obese_Group', '\n'.join(result.errors))

    def test_covariate_domains_reject_nulls_out_of_range_and_bp_inversion(self):
        df = make_valid_cohort()
        df.loc[0, 'Age_Baseline'] = 19
        df.loc[1, 'Sex'] = 3
        df['Income_Decile'] = df['Income_Decile'].astype(float)
        df.loc[2, 'Income_Decile'] = 10.5
        df.loc[3, 'BP_Systolic'] = 70
        df.loc[3, 'BP_Diastolic'] = 90
        df.loc[4, 'Glucose'] = None
        result = check_covariate_domains(df)
        self.assertFalse(result.ok)
        joined = '\n'.join(result.errors)
        self.assertIn('Age_Baseline', joined)
        self.assertIn('Sex', joined)
        self.assertIn('Income_Decile', joined)
        self.assertIn('BP_Systolic', joined)
        self.assertIn('missing', joined.lower())

    def test_covariate_domains_reject_nonfinite_or_extreme_trajectory_slopes(self):
        df = make_valid_cohort()
        df.loc[0, 'BMI_Slope'] = float('inf')
        df['WHtR_Slope'] = df['WHtR_Slope'].astype(object)
        df.loc[1, 'WHtR_Slope'] = 'not-a-number'
        df.loc[2, 'Waist_Slope'] = 100.0
        result = check_covariate_domains(df)
        self.assertFalse(result.ok)
        joined = '\n'.join(result.errors)
        self.assertIn('BMI_Slope', joined)
        self.assertIn('WHtR_Slope', joined)
        self.assertIn('Waist_Slope', joined)


class TestLagAndCoxPreconditions(unittest.TestCase):
    def test_lag1y_subset_relationship_enforces_expected_removals_only(self):
        df_full = make_valid_cohort()
        early_event_id = df_full.loc[0, 'ID']
        df_full.loc[0, ['Event_Any', 'Time_Any']] = [1, 100]
        df_full.loc[1, ['Event_Any', 'Time_Any']] = [0, 100]  # early censoring/non-event must remain
        df_lag = df_full[df_full['ID'] != early_event_id].copy()
        result = check_lag1y_subset_relationship(df_full, df_lag)
        self.assertTrue(result.ok)

        bad_lag = df_lag[df_lag['ID'] != df_full.loc[1, 'ID']].copy()
        bad_lag.loc[len(bad_lag)] = df_full.iloc[0]
        bad_lag.loc[bad_lag.index[-1], 'ID'] = 'ffffffffffffffff'
        result = check_lag1y_subset_relationship(df_full, bad_lag)
        self.assertFalse(result.ok)
        joined = '\n'.join(result.errors)
        self.assertIn('subset', joined.lower())
        self.assertIn('early', joined.lower())

    def test_cox_preconditions_fail_for_sparse_events_and_missing_groups(self):
        df = make_valid_cohort(n=40)
        df.loc[:, 'Event_CVD'] = 0
        df.loc[:5, 'Event_CVD'] = 1
        df = df[df['Obese_Group'] != 4].copy()
        result = check_cox_preconditions(df, 'CVD', min_total_events=20, min_events_per_group=3)
        self.assertFalse(result.ok)
        joined = '\n'.join(result.errors)
        self.assertIn('events', joined.lower())
        self.assertIn('Obese_Group', joined)


class TestValidateCohortOutputsIntegration(unittest.TestCase):
    def test_validate_cohort_outputs_reads_files_and_returns_structured_success(self):
        df_full = make_valid_cohort()
        df_lag = df_full.copy()
        with tempfile.TemporaryDirectory() as tmp:
            full_path = os.path.join(tmp, 'cohort_analytical.csv')
            lag_path = os.path.join(tmp, 'cohort_analytical_lag1y.csv')
            df_full.to_csv(full_path, index=False, encoding='utf-8-sig')
            df_lag.to_csv(lag_path, index=False, encoding='utf-8-sig')
            result = validate_cohort_outputs(full_path, lag_path, min_n=20, min_total_events=20, min_events_per_group=3)
        self.assertTrue(result.ok)
        self.assertEqual(result.details['n_full'], len(df_full))
        self.assertEqual(result.details['n_lag1y'], len(df_lag))

    def test_validate_cohort_outputs_reports_missing_file_and_content_failures(self):
        df_full = make_valid_cohort().drop(columns=['ID'])
        with tempfile.TemporaryDirectory() as tmp:
            full_path = os.path.join(tmp, 'cohort_analytical.csv')
            missing_lag_path = os.path.join(tmp, 'cohort_analytical_lag1y.csv')
            df_full.to_csv(full_path, index=False, encoding='utf-8-sig')
            result = validate_cohort_outputs(full_path, missing_lag_path, min_n=20)
        self.assertFalse(result.ok)
        joined = '\n'.join(result.errors)
        self.assertIn('ID', joined)
        self.assertIn('not found', joined.lower())

    def test_validate_cohort_outputs_preserves_all_digit_leading_zero_hex_ids(self):
        df_full = make_valid_cohort()
        df_full.loc[0, 'ID'] = '0000000000000080'
        df_lag = df_full.copy()
        with tempfile.TemporaryDirectory() as tmp:
            full_path = os.path.join(tmp, 'cohort_analytical.csv')
            lag_path = os.path.join(tmp, 'cohort_analytical_lag1y.csv')
            df_full.to_csv(full_path, index=False, encoding='utf-8-sig')
            df_lag.to_csv(lag_path, index=False, encoding='utf-8-sig')
            result = validate_cohort_outputs(full_path, lag_path, min_n=20, min_total_events=20, min_events_per_group=3)
        self.assertTrue(result.ok, result.errors)

    def test_validate_cohort_outputs_returns_structured_error_for_fractional_event_any(self):
        df_full = make_valid_cohort()
        df_full['Event_Any'] = df_full['Event_Any'].astype(float)
        df_full.loc[0, 'Event_Any'] = 1.5
        df_lag = df_full.copy()
        with tempfile.TemporaryDirectory() as tmp:
            full_path = os.path.join(tmp, 'cohort_analytical.csv')
            lag_path = os.path.join(tmp, 'cohort_analytical_lag1y.csv')
            df_full.to_csv(full_path, index=False, encoding='utf-8-sig')
            df_lag.to_csv(lag_path, index=False, encoding='utf-8-sig')
            result = validate_cohort_outputs(full_path, lag_path, min_n=20)
        self.assertFalse(result.ok)
        self.assertIn('Event_Any', '\n'.join(result.errors))


if __name__ == '__main__':
    unittest.main()
