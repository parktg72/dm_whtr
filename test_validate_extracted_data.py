#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TDD tests for validate_extracted_data.py raw HANA extraction QA."""

import os
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from validate_extracted_data import (
    REQUIRED_COLUMNS,
    ValidationResult,
    check_bz_yyyy_matches_std_yyyy,
    check_categorical_domain,
    check_file_completeness,
    check_lifestyle_harmonization,
    check_null_rates,
    check_numeric_plausibility,
    check_pk_uniqueness,
    check_schema_columns,
    validate_raw_dataset,
)


class TestRawDataCompletenessAndSchema(unittest.TestCase):
    def _write_csv(self, path, rows, columns=None):
        path.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(rows, columns=columns).to_csv(path, index=False, encoding="utf-8-sig")

    def test_file_completeness_reports_missing_expected_raw_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw_dir = Path(tmp)
            self._write_csv(raw_dir / "death" / "death_all.csv", [{"INDI_DSCM_NO": 1, "DTH_ASSMD_DT": "20200101"}])
            self._write_csv(raw_dir / "eligibility_checkup" / "elig_checkup_2009.csv", [{"INDI_DSCM_NO": 1}])
            self._write_csv(raw_dir / "diagnosis" / "diagnosis_2009_01.csv", [{"INDI_DSCM_NO": 1}])
            self._write_csv(raw_dir / "billing" / "billing_2009_01.csv", [{"INDI_DSCM_NO": 1}])

            missing = check_file_completeness(str(raw_dir), 2009, 2009)

            self.assertIn("diagnosis/diagnosis_2009_02.csv", missing)
            self.assertIn("billing/billing_2009_12.csv", missing)
            self.assertNotIn("death/death_all.csv", missing)
            self.assertNotIn("eligibility_checkup/elig_checkup_2009.csv", missing)

    def test_schema_columns_accept_icd_code_alias_for_diagnosis(self):
        df = pd.DataFrame(columns=["INDI_DSCM_NO", "ICD_CODE", "MDCARE_STRT_DT"])

        missing = check_schema_columns(df, REQUIRED_COLUMNS["diagnosis"], "diagnosis/sample.csv")

        self.assertEqual([], missing)

    def test_schema_columns_report_missing_required_columns(self):
        df = pd.DataFrame(columns=["INDI_DSCM_NO", "STD_YYYY", "G1E_HGHT"])

        missing = check_schema_columns(df, REQUIRED_COLUMNS["eligibility_checkup"], "eligibility_checkup/elig_checkup_2009.csv")

        self.assertIn("SEX_TYPE", missing)
        self.assertIn("BZ_YYYY", missing)


class TestRawDataPlausibility(unittest.TestCase):
    def test_numeric_plausibility_fails_when_outlier_fraction_exceeds_threshold(self):
        df = pd.DataFrame({"G1E_HGHT": [170, 171, 172, 80]})

        result = check_numeric_plausibility(df, "G1E_HGHT", 100, 250, max_outlier_frac=0.01)

        self.assertFalse(result.ok)
        self.assertEqual(1, result.count)
        self.assertAlmostEqual(0.25, result.fraction)

    def test_categorical_domain_reports_invalid_values_without_nan_noise(self):
        df = pd.DataFrame({"DRK_LEVEL": [0, 1, 2, 9, None]})

        invalid = check_categorical_domain(df, "DRK_LEVEL", {0, 1, 2})

        self.assertEqual([9], invalid)

    def test_null_rates_report_columns_above_threshold_and_id_null_is_failure(self):
        df = pd.DataFrame({"INDI_DSCM_NO": [1, None, 3], "G1E_WSTC": [80, None, None]})

        result = check_null_rates(df, ["INDI_DSCM_NO", "G1E_WSTC"], max_null_frac=0.20)

        self.assertFalse(result.ok)
        self.assertAlmostEqual(1 / 3, result.details["INDI_DSCM_NO"])
        self.assertAlmostEqual(2 / 3, result.details["G1E_WSTC"])


class TestRawDataConsistency(unittest.TestCase):
    def test_pk_uniqueness_counts_duplicate_composite_keys(self):
        df = pd.DataFrame({"INDI_DSCM_NO": [1, 1, 2], "STD_YYYY": ["2009", "2009", "2009"]})

        duplicates = check_pk_uniqueness(df, ["INDI_DSCM_NO", "STD_YYYY"])

        self.assertEqual(2, duplicates)

    def test_bz_yyyy_mismatch_is_counted_after_string_normalization(self):
        df = pd.DataFrame({"STD_YYYY": [2009, "2010", "2011"], "BZ_YYYY": [2009, "2011", " 2011 "]})

        mismatches = check_bz_yyyy_matches_std_yyyy(df)

        self.assertEqual(1, mismatches)

    def test_lifestyle_harmonization_requires_non_null_valid_domains(self):
        df = pd.DataFrame({"SMK_CURR": [0, 1], "DRK_LEVEL": [0, 3], "PA_ACTIVE": [1, None]})

        problems = check_lifestyle_harmonization(df)

        self.assertIn("DRK_LEVEL invalid values: [3]", problems)
        self.assertIn("PA_ACTIVE has null values", problems)


class TestValidateRawDatasetIntegration(unittest.TestCase):
    def _write_csv(self, path, rows, columns=None):
        path.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(rows, columns=columns).to_csv(path, index=False, encoding="utf-8-sig")

    def test_validate_raw_dataset_returns_structured_failure_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw_dir = Path(tmp)
            self._write_csv(raw_dir / "death" / "death_all.csv", [{"INDI_DSCM_NO": 1, "DTH_ASSMD_DT": "20200101"}])
            self._write_csv(raw_dir / "eligibility_checkup" / "elig_checkup_2009.csv", [{
                "INDI_DSCM_NO": 1,
                "STD_YYYY": 2009,
                "SEX_TYPE": 1,
                "BYEAR": 1980,
                "BZ_YYYY": 2010,
                "G1E_HGHT": 170,
                "G1E_WGHT": 70,
                "G1E_WSTC": 80,
                "G1E_BMI": 24.2,
                "SMK_CURR": 0,
                "DRK_LEVEL": 1,
                "PA_ACTIVE": 1,
            }])
            for month in range(1, 13):
                self._write_csv(raw_dir / "diagnosis" / f"diagnosis_2009_{month:02d}.csv", [{
                    "INDI_DSCM_NO": 1,
                    "ICD_CODE": "E119",
                    "MDCARE_STRT_DT": "20090101",
                }])
                self._write_csv(raw_dir / "billing" / f"billing_2009_{month:02d}.csv", [{
                    "CMN_KEY": f"K{month}",
                    "INDI_DSCM_NO": 1,
                    "MCARE_TP": "1",
                    "MDCARE_STRT_DT": "20090101",
                }])

            report = validate_raw_dataset(str(raw_dir), 2009, 2009)

            self.assertIsInstance(report, ValidationResult)
            self.assertFalse(report.ok)
            self.assertTrue(any("BZ_YYYY != STD_YYYY" in msg for msg in report.errors))
            self.assertEqual([], report.warnings)

    def test_validate_raw_dataset_allows_empty_schema_only_diagnosis_billing_and_death_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw_dir = Path(tmp)
            self._write_csv(raw_dir / "death" / "death_all.csv", [], columns=["INDI_DSCM_NO", "DTH_ASSMD_DT"])
            self._write_csv(raw_dir / "eligibility_checkup" / "elig_checkup_2009.csv", [{
                "indi_dscm_no": 1,
                "std_yyyy": 2009,
                "sex_type": 1,
                "byear": 1980,
                "bz_yyyy": 2009.0,
                "g1e_hght": 170,
                "g1e_wght": 70,
                "g1e_wstc": 80,
                "g1e_bmi": 24.2,
                "smk_curr": 0,
                "drk_level": 1,
                "pa_active": 1,
            }])
            for month in range(1, 13):
                self._write_csv(
                    raw_dir / "diagnosis" / f"diagnosis_2009_{month:02d}.csv",
                    [],
                    columns=["INDI_DSCM_NO", "ICD_CODE", "MDCARE_STRT_DT"],
                )
                self._write_csv(
                    raw_dir / "billing" / f"billing_2009_{month:02d}.csv",
                    [],
                    columns=["CMN_KEY", "INDI_DSCM_NO", "MCARE_TP", "MDCARE_STRT_DT"],
                )
            self._write_csv(raw_dir / "diagnosis" / "diagnosis_2024_01.csv", [{"BROKEN": "stale extra file"}])

            report = validate_raw_dataset(str(raw_dir), 2009, 2009)

            self.assertTrue(report.ok, report.errors)


if __name__ == "__main__":
    unittest.main(verbosity=2)
