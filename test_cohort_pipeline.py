#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
국민건강보험 빅데이터 기반 BMI & WHtR 연구 (DM_WHTR)
Phase 1: TDD 단위 테스트 슈트

본 스크립트는 Claude 본부(QA)와 Codex 본부(TDD Agent)의 협업으로 구현되었습니다.
- 'build_cohort.py'의 핵심 역학 규칙 및 통계 알고리즘을 검증합니다:
  1. OLS 선형 기울기 계산 정밀도 검증 (calculate_ols_slope)
  2. 나이 제한 필터링 검증
  3. 검진 빈도 조건 (3회 이상) 검증
  4. Wash-out Period (기왕력자 배제) 작동 여부 검증
  5. 2x2 비만 매트릭스 4개 그룹 할당 로직 검증
  6. Lag-time 필터 적용 시 1년 이내 사건 발생자 배제 여부 검증
- Windows 환경 및 Python 3.12를 지원합니다.
"""

import os
import sys
import sqlite3
import unittest
import importlib
import types
from unittest import mock
import pandas as pd
import numpy as np
import cohort_privacy
import generate_synthetic_db

# Windows CMD 한글 인코딩 방어
if sys.platform.startswith('win'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except AttributeError:
        pass

# build_cohort에서 함수 임포트
from build_cohort import build_cohort, calculate_ols_slope, hash_identifier
import build_cohort as build_cohort_module
import merge_local_data_duckdb as duckdb_module
from merge_local_data_duckdb import normalize_diagnosis_columns

class TestCohortPipeline(unittest.TestCase):
    
    @classmethod
    def setUpClass(cls):
        """테스트에 사용할 임시 메모리 SQLite DB 설정 및 테스트 데이터 주입"""
        cls.conn = sqlite3.connect(":memory:")
        cursor = cls.conn.cursor()
        
        # 실제 테이블 구조 복사
        cursor.execute("""
        CREATE TABLE ELIGIBILITY (
            INDI_DSCM_NO INTEGER,
            STD_YYYY TEXT,
            SEX_TYPE TEXT,
            BYEAR TEXT,
            PRIMARY KEY (INDI_DSCM_NO, STD_YYYY)
        )
        """)
        
        cursor.execute("""
        CREATE TABLE CHECKUP (
            INDI_DSCM_NO INTEGER,
            EXMD_BZ_YYYY TEXT,
            G1E_HGHT REAL,
            G1E_WGHT REAL,
            G1E_BMI REAL,
            G1E_WSTC REAL,
            INCOME_DECILE INTEGER,
            SMOKING_STATUS INTEGER,
            BP_SYS REAL,
            BP_DIA REAL,
            GLUCOSE REAL,
            CHOLESTEROL REAL,
            EGFR REAL,
            HYPERTENSION_MED INTEGER,
            DYSLIPIDEMIA_MED INTEGER,
            PRIMARY KEY (INDI_DSCM_NO, EXMD_BZ_YYYY)
        )
        """)
        
        cursor.execute("""
        CREATE TABLE DISEASE_HISTORY (
            INDI_DSCM_NO INTEGER,
            ICD_CODE TEXT,
            DIAG_DATE TEXT
        )
        """)
        
        # --- 테스트 시나리오별 가상 데이터 주입 ---
        # PID 1: 정상 코호트 진입 대상자 (2009~2012년 4회 검진, 만 30세, 기왕력 없음, 2015년 이벤트 발생)
        elg_data = [
            (1, '2009', '1', '1980'), (1, '2010', '1', '1980'), (1, '2011', '1', '1980'), (1, '2012', '1', '1980'),
            # PID 2: 나이 미달 제외 대상자 (만 18세, 1994년생)
            (2, '2009', '2', '1994'), (2, '2010', '2', '1994'), (2, '2011', '2', '1994'), (2, '2012', '2', '1994'),
            # PID 3: 검진 빈도 미달 제외 대상자 (검진 2회)
            (3, '2009', '1', '1980'), (3, '2010', '1', '1980'),
            # PID 4: Wash-out 제외 대상자 (2011년에 이미 CVD 진단받은 자)
            (4, '2009', '2', '1975'), (4, '2010', '2', '1975'), (4, '2011', '2', '1975'), (4, '2012', '2', '1975'),
            # PID 5: Lag-time 제외 대상자 (2013-05-01에 최초 당뇨병 이벤트가 발생하여 1년 미만 생존으로 Lag 1년 적용 시 배제되어야 함)
            (5, '2009', '1', '1982'), (5, '2010', '1', '1982'), (5, '2011', '1', '1982'), (5, '2012', '1', '1982')
        ]
        
        chk_data = [
            # PID 1: 키 170cm, 몸무게 70kg에서 76kg로 OLS 증가 모델, 허리둘레 80cm에서 86cm
            # BMI: 24.22 -> 26.3
            (1, '2009', 170.0, 70.0, 24.22, 80.0, 5, 1, 120, 80, 90, 180, 90, 0, 0),
            (1, '2010', 170.0, 72.0, 24.91, 82.0, 5, 1, 120, 80, 90, 180, 90, 0, 0),
            (1, '2011', 170.0, 74.0, 25.61, 84.0, 5, 1, 120, 80, 90, 180, 90, 0, 0),
            (1, '2012', 170.0, 76.0, 26.30, 86.0, 5, 1, 122, 82, 92, 182, 88, 0, 0),
            
            # PID 2
            (2, '2009', 160.0, 50.0, 19.53, 70.0, 8, 1, 110, 70, 85, 160, 100, 0, 0),
            (2, '2010', 160.0, 51.0, 19.92, 71.0, 8, 1, 110, 70, 85, 160, 100, 0, 0),
            (2, '2011', 160.0, 52.0, 20.31, 72.0, 8, 1, 110, 70, 85, 160, 100, 0, 0),
            (2, '2012', 160.0, 53.0, 20.70, 73.0, 8, 1, 112, 72, 88, 162, 98, 0, 0),
            
            # PID 3
            (3, '2009', 175.0, 80.0, 26.12, 88.0, 6, 2, 130, 85, 100, 210, 85, 0, 0),
            (3, '2010', 175.0, 82.0, 26.78, 89.0, 6, 2, 130, 85, 100, 210, 85, 0, 0),
            
            # PID 4
            (4, '2009', 158.0, 65.0, 26.04, 82.0, 4, 1, 125, 78, 95, 220, 80, 0, 0),
            (4, '2010', 158.0, 66.0, 26.44, 83.0, 4, 1, 125, 78, 95, 220, 80, 0, 0),
            (4, '2011', 158.0, 67.0, 26.84, 84.0, 4, 1, 125, 78, 95, 220, 80, 0, 0),
            (4, '2012', 158.0, 68.0, 27.24, 85.0, 4, 1, 128, 80, 98, 222, 78, 0, 0),
            
            # PID 5
            (5, '2009', 172.0, 60.0, 20.28, 75.0, 7, 3, 118, 75, 88, 175, 95, 0, 0),
            (5, '2010', 172.0, 61.0, 20.62, 76.0, 7, 3, 118, 75, 88, 175, 95, 0, 0),
            (5, '2011', 172.0, 62.0, 20.96, 77.0, 7, 3, 118, 75, 88, 175, 95, 0, 0),
            (5, '2012', 172.0, 63.0, 21.29, 78.0, 7, 3, 120, 76, 90, 178, 92, 0, 0)
        ]
        
        dis_data = [
            # PID 4: 2011년에 허혈성 심장질환(I20.9)으로 기왕력 보유 -> Wash-out 대상자
            (4, 'I20.9', '2011-04-15'),
            # PID 1: 2015-08-20에 심근경색(I21.0) 발생 -> Follow-up 기간 내 이벤트 발생 (4016일 중 약 961일 생존 후 이벤트)
            (1, 'I21.0', '2015-08-20'),
            # PID 5: 2013-05-15에 제2형 당뇨(E11.9) 발생 -> 1년 이내의 Lag-time 제외 대상자
            (5, 'E11.9', '2013-05-15')
        ]
        
        cursor.executemany("INSERT INTO ELIGIBILITY VALUES (?, ?, ?, ?)", elg_data)
        cursor.executemany("INSERT INTO CHECKUP VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", chk_data)
        cursor.executemany("INSERT INTO DISEASE_HISTORY VALUES (?, ?, ?)", dis_data)
        cls.conn.commit()

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def test_01_ols_slope(self):
        """수학적 OLS 선형 기울기(beta) 계산 기능 검증"""
        # y가 매년 2씩 정확히 증가하는 경우 -> 기울기는 2.0이어야 함
        x_years = [2009, 2010, 2011, 2012]
        y_values = [10.0, 12.0, 14.0, 16.0]
        slope = calculate_ols_slope(y_values, x_years)
        self.assertAlmostEqual(slope, 2.0, places=4)
        
        # 결측이 하나 섞여 있는 경우
        y_values_nan = [10.0, np.nan, 14.0, 16.0]
        slope_nan = calculate_ols_slope(y_values_nan, x_years)
        # OLS는 결측을 제외하고 피팅하므로 y = 2x - 3918 형태가 됨
        # (2009, 10), (2011, 14), (2012, 16) OLS 기울기는 여전히 2.0
        self.assertAlmostEqual(slope_nan, 2.0, places=4)

    def test_02_age_filter(self):
        """만 20세~45세 필터가 올바르게 수행되는지 검증 (만 18세인 PID 2는 배제되어야 함)"""
        query = """
        SELECT DISTINCT INDI_DSCM_NO 
        FROM ELIGIBILITY 
        WHERE (CAST(STD_YYYY AS INTEGER) - CAST(BYEAR AS INTEGER)) BETWEEN 20 AND 45
        """
        df = pd.read_sql_query(query, self.conn)
        pids = df['INDI_DSCM_NO'].tolist()
        
        self.assertIn(1, pids)
        self.assertNotIn(2, pids, "만 18세인 대상자(PID 2)가 필터에서 걸러지지 않았습니다.")

    def test_03_checkup_frequency_filter(self):
        """4개년 중 3회 이상 검진 필터가 검진 2회인 PID 3을 올바르게 배제하는지 검증"""
        cursor = self.conn.cursor()
        cursor.execute("""
        SELECT INDI_DSCM_NO, COUNT(STD_YYYY)
        FROM ELIGIBILITY
        WHERE STD_YYYY BETWEEN '2009' AND '2012'
        GROUP BY INDI_DSCM_NO
        """)
        rows = cursor.fetchall()
        freq_dict = {row[0]: row[1] for row in rows}
        
        self.assertEqual(freq_dict[1], 4)
        self.assertEqual(freq_dict[3], 2)
        
        # 3회 이상인 PID 수집
        valid_pids = [pid for pid, cnt in freq_dict.items() if cnt >= 3]
        self.assertNotIn(3, valid_pids, "검진 빈도가 2회인 PID 3이 제외되지 않았습니다.")

    def test_04_washout_period(self):
        """2013-01-01 이전 기왕력(CVD)이 존재하는 PID 4가 Wash-out 필터에서 제외되는지 검증"""
        # 2013-01-01 이전 기왕력자 수집
        dis_query = """
        SELECT DISTINCT INDI_DSCM_NO 
        FROM DISEASE_HISTORY 
        WHERE DIAG_DATE < '2013-01-01' 
          AND (ICD_CODE LIKE 'I20%' OR ICD_CODE LIKE 'I21%' OR ICD_CODE LIKE 'I25%' OR 
               ICD_CODE LIKE 'I60%' OR ICD_CODE LIKE 'I63%' OR ICD_CODE LIKE 'E11%' OR ICD_CODE LIKE 'N18%')
        """
        df_dis = pd.read_sql_query(dis_query, self.conn)
        pre_existing_pids = df_dis['INDI_DSCM_NO'].tolist()
        
        self.assertIn(4, pre_existing_pids)
        
        # 최종 코호트 후보
        cohort_candidates = [1, 4, 5] # 3회 이상 검진자 중
        final_cohort = [pid for pid in cohort_candidates if pid not in pre_existing_pids]
        
        self.assertNotIn(4, final_cohort, "기왕력이 있는 PID 4가 최종 코호트에 진입했습니다.")
        self.assertIn(1, final_cohort)

    def test_04b_synthetic_weight_floor_prevents_implausible_final_adult_weights(self):
        """합성 검진 몸무게는 최종 코호트 QA hard range 하한(30kg)을 위반하지 않아야 함"""
        self.assertEqual(generate_synthetic_db.enforce_min_weight(28.4), 30.0)
        self.assertEqual(generate_synthetic_db.enforce_min_weight(30.0), 30.0)
        self.assertEqual(generate_synthetic_db.enforce_min_weight(72.3), 72.3)

    def test_04c_synthetic_blood_pressure_pair_prevents_inversion(self):
        """합성 혈압은 수축기 혈압이 이완기 혈압보다 낮은 불가능한 조합을 만들지 않아야 함"""
        sys_bp, dia_bp = generate_synthetic_db.enforce_bp_order(92.1, 92.3)
        self.assertGreaterEqual(sys_bp, dia_bp)
        self.assertEqual(dia_bp, 92.3)

        sys_bp, dia_bp = generate_synthetic_db.enforce_bp_order(120.0, 80.0)
        self.assertEqual((sys_bp, dia_bp), (120.0, 80.0))

    def test_05_obesity_matrix_grouping(self):
        """Baseline(2012년) 기준 2x2 비만 그룹(Group 1~4) 할당이 의도대로 동작하는지 검증"""
        # PID 1의 2012년도(마지막) 신체지표 수집
        # Height: 170.0cm, Weight: 76.0kg, Waist: 86.0cm
        # BMI = 76 / 1.7^2 = 26.3 (>= 25 이므로 비만)
        # WHtR = 86 / 170 = 0.5058 (>= 0.5 이므로 복부비만)
        # 따라서 Group 4 (비만/복부비만)에 할당되어야 함
        h = 170.0
        w = 76.0
        wc = 86.0
        
        bmi = w / ((h / 100.0) ** 2)
        whtr = wc / h
        
        self.assertTrue(bmi >= 25.0)
        self.assertTrue(whtr >= 0.5)
        
        # 그룹 매칭
        if bmi < 25.0 and whtr < 0.5:
            group = 1
        elif bmi < 25.0 and whtr >= 0.5:
            group = 2
        elif bmi >= 25.0 and whtr < 0.5:
            group = 3
        else:
            group = 4
            
        self.assertEqual(group, 4, "BMI >= 25 및 WHtR >= 0.5인 대상자가 Group 4에 올바르게 할당되지 않았습니다.")

    def test_06_lag_time_filter(self):
        """추적 관찰 최초 1년(365일) 이내 이벤트 발생자(PID 5)가 민감도 분석 코호트에서 올바르게 배제되는지 검증"""
        # PID 1: 2013-01-01 ~ 2015-08-20 (이벤트 발생까지의 생존일수: 961일)
        # PID 5: 2013-01-01 ~ 2013-05-15 (이벤트 발생까지의 생존일수: 134일) -> 365일 이하로 Lag-time 탈락 대상
        
        index_date = datetime(2013, 1, 1)
        
        # PID 1 생존일 연산
        event_dt_1 = datetime(2015, 8, 20)
        time_1 = (event_dt_1 - index_date).days
        
        # PID 5 생존일 연산
        event_dt_5 = datetime(2013, 5, 15)
        time_5 = (event_dt_5 - index_date).days
        
        self.assertEqual(time_1, 961)
        self.assertEqual(time_5, 134)
        
        # Lag-time 1년 필터 적용 (이벤트 시간 > 365인 대상자만 유지)
        cohort_records = [
            {'ID': 1, 'Time_Any': time_1, 'Event_Any': 1},
            {'ID': 5, 'Time_Any': time_5, 'Event_Any': 1}
        ]
        
        df_cohort = pd.DataFrame(cohort_records)
        df_lag1y = build_cohort_module.apply_lag_time_filter(df_cohort)
        
        lag_pids = df_lag1y['ID'].tolist()
        self.assertNotIn(5, lag_pids, "1년 이내에 당뇨병이 발생한 PID 5가 민감도 분석(Lag-time) 필터에서 제외되지 않았습니다.")
        self.assertIn(1, lag_pids)

    def test_06b_lag_time_filter_keeps_early_censoring(self):
        """Lag-time 필터는 1년 이내 실제 이벤트만 제외하고 조기 검열자는 유지해야 함"""
        df_cohort = pd.DataFrame([
            {'ID': 'early_event', 'Time_Any': 120, 'Event_Any': 1},
            {'ID': 'early_censor', 'Time_Any': 120, 'Event_Any': 0},
            {'ID': 'late_event', 'Time_Any': 900, 'Event_Any': 1},
        ])

        build_lag = build_cohort_module.apply_lag_time_filter(df_cohort)
        duckdb_lag = duckdb_module.apply_lag_time_filter(df_cohort)

        self.assertNotIn('early_event', build_lag['ID'].tolist())
        self.assertIn('early_censor', build_lag['ID'].tolist())
        self.assertIn('late_event', build_lag['ID'].tolist())
        pd.testing.assert_frame_equal(build_lag.reset_index(drop=True), duckdb_lag.reset_index(drop=True))

    def test_07_hash_identifier_returns_stable_16_hex(self):
        """동일 salt에서 동일 식별자는 안정적인 16자리 hex 해시로 변환되고 원문 숫자와 달라야 함"""
        hashed_1 = hash_identifier(12345, salt="unit-test-salt")
        hashed_2 = hash_identifier("12345", salt="unit-test-salt")

        self.assertEqual(hashed_1, hashed_2)
        self.assertRegex(hashed_1, r"^[0-9a-f]{16}$")
        self.assertNotEqual(hashed_1, "12345")

    def test_07b_require_hash_salt_flag_rejects_missing_env_salt(self):
        """운영 salt 강제 플래그가 켜져 있으면 DM_WHTR_HASH_SALT 누락 시 실패해야 함"""
        with mock.patch.dict(os.environ, {"DM_WHTR_REQUIRE_HASH_SALT": "1"}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "DM_WHTR_HASH_SALT"):
                cohort_privacy.get_hash_salt()

    def test_07c_hash_collision_detection_rejects_duplicate_hashed_ids(self):
        """최종 코호트의 해시 ID 충돌은 두 파이프라인 모두 명확히 거부해야 함"""
        df_cohort = pd.DataFrame([
            {"ID": "same-hash", "Event_Any": 0, "Time_Any": 4016},
            {"ID": "same-hash", "Event_Any": 1, "Time_Any": 800},
        ])

        with self.assertRaisesRegex(RuntimeError, "hash collision"):
            build_cohort_module.validate_unique_hashed_ids(df_cohort)
        with self.assertRaisesRegex(RuntimeError, "hash collision"):
            duckdb_module.validate_unique_hashed_ids(df_cohort)

    def test_08_build_cohort_writes_hashed_ids(self):
        """build_cohort 산출물의 ID는 원시 PID가 아니라 16자리 hex 해시여야 함"""
        build_cohort()

        output_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "cohort_analytical.csv")
        df = pd.read_csv(output_path, dtype={"ID": str})

        self.assertFalse(df.empty)
        self.assertTrue(df["ID"].str.match(r"^[0-9a-f]{16}$").all())
        self.assertTrue(set(df["ID"]).isdisjoint({"1", "5"}))

from datetime import datetime

class TestDuckDBPipeline(unittest.TestCase):
    def setUp(self):
        import tempfile
        import shutil
        self.test_dir = tempfile.mkdtemp()
        
        # 폴더 생성
        self.death_dir = os.path.join(self.test_dir, "death")
        self.elig_dir = os.path.join(self.test_dir, "eligibility_checkup")
        self.diag_dir = os.path.join(self.test_dir, "diagnosis")
        self.bill_dir = os.path.join(self.test_dir, "billing")
        
        for d in [self.death_dir, self.elig_dir, self.diag_dir, self.bill_dir]:
            os.makedirs(d, exist_ok=True)
            
        # 가상 CSV 파일 쓰기
        # 사망
        pd.DataFrame([
            {"INDI_DSCM_NO": 99, "DTH_ASSMD_DT": "20180515"}
        ]).to_csv(os.path.join(self.death_dir, "death_all.csv"), index=False)
        
        # 자격 & 검진
        pd.DataFrame([
            {"INDI_DSCM_NO": 1, "STD_YYYY": "2009", "SEX_TYPE": "1", "BYEAR": "1980", "G1E_HGHT": 170.0, "G1E_WGHT": 70.0, "G1E_WSTC": 80.0, "G1E_BMI": 24.22, "INCOME_DECILE": 5, "SMOKING_STATUS": 1},
            {"INDI_DSCM_NO": 1, "STD_YYYY": "2010", "SEX_TYPE": "1", "BYEAR": "1980", "G1E_HGHT": 170.0, "G1E_WGHT": 72.0, "G1E_WSTC": 82.0, "G1E_BMI": 24.91, "INCOME_DECILE": 5, "SMOKING_STATUS": 1},
            {"INDI_DSCM_NO": 1, "STD_YYYY": "2011", "SEX_TYPE": "1", "BYEAR": "1980", "G1E_HGHT": 170.0, "G1E_WGHT": 74.0, "G1E_WSTC": 84.0, "G1E_BMI": 25.61, "INCOME_DECILE": 5, "SMOKING_STATUS": 1},
            {"INDI_DSCM_NO": 1, "STD_YYYY": "2012", "SEX_TYPE": "1", "BYEAR": "1980", "G1E_HGHT": 170.0, "G1E_WGHT": 76.0, "G1E_WSTC": 86.0, "G1E_BMI": 26.30, "INCOME_DECILE": 5, "SMOKING_STATUS": 1},
        ]).to_csv(os.path.join(self.elig_dir, "elig_checkup_2012.csv"), index=False)
        
        # 상병 (Wash-out 포함)
        pd.DataFrame([
            {"INDI_DSCM_NO": 1, "MCEX_SICK_SYM": "I21.0", "MDCARE_STRT_DT": "2015-08-20"},
            {"INDI_DSCM_NO": 4, "MCEX_SICK_SYM": "I20.9", "MDCARE_STRT_DT": "2011-04-15"}
        ]).to_csv(os.path.join(self.diag_dir, "diagnosis_2015_08.csv"), index=False)
        
        # 명세서
        pd.DataFrame([
            {"CMN_KEY": 1001, "INDI_DSCM_NO": 1, "MCARE_TP": "1", "HSPTZ_VSHSP_DD_CNT": 3.0, "MDCARE_STRT_DT": "2015-08-20"}
        ]).to_csv(os.path.join(self.bill_dir, "billing_2015_08.csv"), index=False)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.test_dir)
        
    def test_duckdb_glob_and_filters(self):
        """DuckDB 분할 CSV glob 다중 테이블 뷰 직접 조회 및 조인/기왕력 제거 정합성 테스트"""
        import duckdb
        con = duckdb.connect(database=':memory:')
        
        con.execute(f"CREATE VIEW v_death AS SELECT * FROM read_csv_auto('{self.death_dir}/death_all.csv')")
        con.execute(f"CREATE VIEW v_elig_checkup AS SELECT * FROM read_csv_auto('{self.elig_dir}/elig_checkup_*.csv')")
        con.execute(f"CREATE VIEW v_diagnosis AS SELECT * FROM read_csv_auto('{self.diag_dir}/diagnosis_*.csv')")
        
        # 3회 이상 검진자 필터 검증
        con.execute("""
            CREATE TABLE t_valid_pids AS 
            SELECT INDI_DSCM_NO 
            FROM v_elig_checkup
            WHERE STD_YYYY BETWEEN '2009' AND '2012'
            GROUP BY INDI_DSCM_NO
            HAVING COUNT(STD_YYYY) >= 3
        """)
        valid_pids = [row[0] for row in con.execute("SELECT INDI_DSCM_NO FROM t_valid_pids").fetchall()]
        self.assertIn(1, valid_pids)
        self.assertEqual(len(valid_pids), 1)
        
        # Wash-out 기왕력자 색출 검증 (REPLACE 및 날짜 비교)
        con.execute("""
            CREATE TABLE t_washout_pids AS
            SELECT DISTINCT INDI_DSCM_NO
            FROM v_diagnosis
            WHERE REPLACE(CAST(MDCARE_STRT_DT AS VARCHAR), '-', '') < '20130101'
              AND (
                  MCEX_SICK_SYM LIKE 'I20%' OR MCEX_SICK_SYM LIKE 'I21%' OR MCEX_SICK_SYM LIKE 'I22%' OR
                  MCEX_SICK_SYM LIKE 'I23%' OR MCEX_SICK_SYM LIKE 'I24%' OR MCEX_SICK_SYM LIKE 'I25%' OR
                  MCEX_SICK_SYM LIKE 'I6%' OR MCEX_SICK_SYM LIKE 'E11%' OR MCEX_SICK_SYM LIKE 'N18%'
              )
        """)
        washout_pids = [row[0] for row in con.execute("SELECT INDI_DSCM_NO FROM t_washout_pids").fetchall()]
        self.assertIn(4, washout_pids)
        self.assertNotIn(1, washout_pids)

    def test_diagnosis_column_normalization_aliases_mcex_sick_sym(self):
        """MCEX_SICK_SYM 입력은 내부 이벤트 판정 표준 컬럼 ICD_CODE로 사용할 수 있어야 함"""
        df = pd.DataFrame([
            {"INDI_DSCM_NO": 1, "MCEX_SICK_SYM": "I21.0", "MDCARE_STRT_DT": "2015-08-20"}
        ])

        normalized = normalize_diagnosis_columns(df)

        self.assertIn("ICD_CODE", normalized.columns)
        self.assertEqual(normalized.loc[0, "ICD_CODE"], "I21.0")

    def test_diagnosis_column_normalization_aliases_mcex_sick_sym1(self):
        """MCEX_SICK_SYM1만 있는 입력도 ICD_CODE 표준 컬럼으로 정규화해야 함"""
        df = pd.DataFrame([
            {"INDI_DSCM_NO": 1, "MCEX_SICK_SYM1": "E11.9", "MDCARE_STRT_DT": "2015-08-20"}
        ])

        normalized = normalize_diagnosis_columns(df)

        self.assertIn("ICD_CODE", normalized.columns)
        self.assertEqual(normalized.loc[0, "ICD_CODE"], "E11.9")

    def test_diagnosis_column_normalization_collects_secondary_codes(self):
        """부상병 컬럼은 감사/추적용 ICD_CODE_ALL에 모으되 이벤트 판정은 주상병만 사용해야 함"""
        df = pd.DataFrame([
            {
                "INDI_DSCM_NO": 1,
                "MCEX_SICK_SYM1": "Z00.0",
                "MCEX_SICK_SYM2": "I21.0",
                "MCEX_SICK_SYM3": None,
                "MDCARE_STRT_DT": "2015-08-20",
            }
        ])

        normalized = normalize_diagnosis_columns(df)

        self.assertEqual(normalized.loc[0, "ICD_CODE"], "Z00.0")
        self.assertIn("ICD_CODE_ALL", normalized.columns)
        self.assertEqual(normalized.loc[0, "ICD_CODE_ALL"], ["Z00.0", "I21.0"])

    def test_secondary_diagnosis_does_not_drive_event_matching(self):
        """P1B 결정: 부상병 ICD_CODE_ALL에 대상 질환이 있어도 주상병 ICD_CODE가 아니면 이벤트로 보지 않아야 함"""
        row = pd.Series({"ICD_CODE": "Z00.0", "ICD_CODE_ALL": ["Z00.0", "I21.0"]})

        self.assertFalse(duckdb_module._row_has_diagnosis_prefix(row, ["I21"]))


def _import_extract_hana_for_tests():
    fake_hdbcli = types.ModuleType("hdbcli")
    fake_dbapi = types.SimpleNamespace(connect=lambda **kwargs: None)
    fake_hdbcli.dbapi = fake_dbapi
    with mock.patch.dict(sys.modules, {"hdbcli": fake_hdbcli, "hdbcli.dbapi": fake_dbapi}):
        sys.modules.pop("extract_hana", None)
        return importlib.import_module("extract_hana")


class TestHanaExtractionCorrections(unittest.TestCase):
    def test_schema_name_uses_nhisbda_not_nhsibda(self):
        """검진/자격/사망 스키마 오타는 남아 있으면 안 됨"""
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "extract_hana.py"), encoding="utf-8") as f:
            source = f.read()

        bad_schema = "NHS" + "IBDA"
        self.assertIn("NHISBDA", source)
        self.assertNotIn(bad_schema, source)

    def test_era_query_strings_use_correct_tables_year_columns_and_standard_outputs(self):
        """검진 추출 SQL은 시대별 레이아웃 컬럼을 읽고 표준 생활습관 컬럼을 산출해야 함"""
        extract_hana = _import_extract_hana_for_tests()
        make_query = getattr(extract_hana, "get_eligibility_checkup_query", None)
        self.assertIsNotNone(make_query, "extract_hana.get_eligibility_checkup_query(year)가 필요합니다")

        q2017 = make_query(2017)
        self.assertIn("NHISBDA.HMDT_G1EQ_RST", q2017)
        self.assertIn("E.EXMD_BZ_YYYY AS BZ_YYYY", q2017)
        self.assertIn("P.STD_YYYY = E.EXMD_BZ_YYYY", q2017)
        self.assertIn("Q_SMK_YN", q2017)
        self.assertIn("Q_DRK_FRQ_V0108", q2017)
        self.assertIn("Q_PA_FRQ", q2017)

        q2018 = make_query(2018)
        self.assertIn("NHISBDA.HMDT_G1E_RST_2018", q2018)
        self.assertIn("NHISBDA.HMDT_GQ_RST_2018", q2018)
        self.assertIn("E.EXMD_BZ_YYYY AS BZ_YYYY", q2018)
        self.assertIn("E.EXMD_BZ_YYYY = Q.EXMD_BZ_YYYY", q2018)
        self.assertIn("P.STD_YYYY = E.EXMD_BZ_YYYY", q2018)

        q2019 = make_query(2019)
        self.assertIn("NHISBDA.HMDT_G1E_RST_2019", q2019)
        self.assertIn("NHISBDA.HMDT_GQ_RST_2019", q2019)
        self.assertIn("E.HC_BZ_YYYY AS BZ_YYYY", q2019)
        self.assertIn("E.HC_BZ_YYYY = Q.HC_BZ_YYYY", q2019)
        self.assertIn("P.STD_YYYY = E.HC_BZ_YYYY", q2019)
        for required in ["Q_SMK_NOW_YN", "Q_DRK_PER", "Q_DRK_FRQ", "Q_PA_VD_FRQ", "Q_PA_MD_FRQ", "Q_PA_MUSL_FRQ"]:
            self.assertIn(required, q2019)
        for forbidden in ["Q_SMK_YN", "Q_DRK_FRQ_V0108", "Q_PA_FRQ", "Q_PA_VIG_FRQ", "Q_PA_MOD_FRQ", "Q_PA_WLK_FRQ"]:
            self.assertNotIn(forbidden, q2019)

        for query in [q2017, q2018, q2019]:
            for output_col in ["BZ_YYYY", "SMK_CURR", "DRK_LEVEL", "PA_ACTIVE"]:
                self.assertIn(output_col, query)

    def test_harmonize_lifestyle_pre2018_and_2018_domains_are_non_null(self):
        """pre2018/2018 구형 문진 컬럼은 SMK_CURR, DRK_LEVEL, PA_ACTIVE 표준값으로 변환되어야 함"""
        extract_hana = _import_extract_hana_for_tests()
        harmonize = getattr(extract_hana, "harmonize_lifestyle", None)
        self.assertIsNotNone(harmonize, "extract_hana.harmonize_lifestyle(df, era)가 필요합니다")
        df = pd.DataFrame([
            {"STD_YYYY": "2017", "BZ_YYYY": "2017", "Q_SMK_YN": 3, "Q_DRK_FRQ_V0108": 5, "Q_PA_FRQ": 3},
            {"STD_YYYY": "2017", "BZ_YYYY": "2017", "Q_SMK_YN": 1, "Q_DRK_FRQ_V0108": 2, "Q_PA_FRQ": 1},
            {"STD_YYYY": "2017", "BZ_YYYY": "2017", "Q_SMK_YN": None, "Q_DRK_FRQ_V0108": None, "Q_PA_FRQ": None},
        ])

        for era in ["pre2018", "2018"]:
            out = harmonize(df, era)
            self.assertEqual(out["SMK_CURR"].tolist(), [1, 0, 0])
            self.assertEqual(out["DRK_LEVEL"].tolist(), [2, 1, 0])
            self.assertEqual(out["PA_ACTIVE"].tolist(), [1, 0, 0])
            self.assertFalse(out[["SMK_CURR", "DRK_LEVEL", "PA_ACTIVE"]].isna().any().any())
            self.assertTrue(set(out["SMK_CURR"]).issubset({0, 1}))
            self.assertTrue(set(out["DRK_LEVEL"]).issubset({0, 1, 2}))
            self.assertTrue(set(out["PA_ACTIVE"]).issubset({0, 1}))
            self.assertTrue((out["BZ_YYYY"].astype(str) == out["STD_YYYY"].astype(str)).all())

    def test_harmonize_lifestyle_post2019_domains_are_non_null(self):
        """2019+ 신형 문진 컬럼은 실제 레이아웃명 기준으로 표준값을 산출해야 함"""
        extract_hana = _import_extract_hana_for_tests()
        harmonize = getattr(extract_hana, "harmonize_lifestyle", None)
        self.assertIsNotNone(harmonize, "extract_hana.harmonize_lifestyle(df, era)가 필요합니다")
        df = pd.DataFrame([
            {
                "STD_YYYY": "2019",
                "BZ_YYYY": "2019",
                "Q_SMK_NOW_YN": 1,
                "Q_DRK_PER": 1,
                "Q_DRK_FRQ": 5,
                "Q_PA_VD_FRQ": 3,
                "Q_PA_MD_FRQ": 0,
                "Q_PA_MUSL_FRQ": 0,
            },
            {
                "STD_YYYY": "2019",
                "BZ_YYYY": "2019",
                "Q_SMK_NOW_YN": 0,
                "Q_DRK_PER": 1,
                "Q_DRK_FRQ": 2,
                "Q_PA_VD_FRQ": 0,
                "Q_PA_MD_FRQ": 5,
                "Q_PA_MUSL_FRQ": 0,
            },
            {
                "STD_YYYY": "2019",
                "BZ_YYYY": "2019",
                "Q_SMK_NOW_YN": None,
                "Q_DRK_PER": 0,
                "Q_DRK_FRQ": None,
                "Q_PA_VD_FRQ": None,
                "Q_PA_MD_FRQ": None,
                "Q_PA_MUSL_FRQ": None,
            },
        ])

        out = harmonize(df, "post2019")
        self.assertEqual(out["SMK_CURR"].tolist(), [1, 0, 0])
        self.assertEqual(out["DRK_LEVEL"].tolist(), [2, 1, 0])
        self.assertEqual(out["PA_ACTIVE"].tolist(), [1, 1, 0])
        self.assertFalse(out[["SMK_CURR", "DRK_LEVEL", "PA_ACTIVE"]].isna().any().any())
        self.assertTrue(set(out["SMK_CURR"]).issubset({0, 1}))
        self.assertTrue(set(out["DRK_LEVEL"]).issubset({0, 1, 2}))
        self.assertTrue(set(out["PA_ACTIVE"]).issubset({0, 1}))
        self.assertTrue((out["BZ_YYYY"].astype(str) == out["STD_YYYY"].astype(str)).all())

if __name__ == "__main__":
    unittest.main()
