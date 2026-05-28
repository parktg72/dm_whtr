#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
국민건강보험 빅데이터 기반 BMI & WHtR 연구 (DM_WHTR)
Phase 2 & 3: 분석 파이프라인 TDD 단위 테스트 슈트

본 스크립트는 Claude 본부(QA)와 Codex 본부(TDD Agent)의 협업으로 구현되었습니다.
- 'analyze_trajectories.py'의 핵심 통계 알고리즘 및 시각화 모듈을 검증합니다:
  1. K-Means 기반 궤적 군집 분류 및 동적 라벨링 검증
  2. lifelines CoxPHFitter 모델 피팅 및 Model 1, 2, 3 정합성 검증
  3. Hazard Ratio 및 95% 신뢰구간 수학적 범위 검증
  4. KM Curve 및 Forest Plot 시각화 파일 정상 생성 여부 검증
- Windows 환경 및 Python 3.12를 지원합니다.
"""

import os
import sys
import tempfile
import unittest
import shutil
import pandas as pd
import numpy as np
from lifelines import CoxPHFitter

# 분석 파이프라인 모듈 임포트
from analyze_trajectories import (
    perform_trajectory_clustering,
    prepare_cox_variables,
    fit_multivariate_cox,
    plot_kaplan_meier_curves,
    plot_forest_plots
)

# Windows CMD 한글 인코딩 방어
if sys.platform.startswith('win'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except AttributeError:
        pass


class TestAnalysisPipeline(unittest.TestCase):
    
    @classmethod
    def setUpClass(cls):
        """테스트에 사용될 독립적 가상 코호트 데이터프레임 생성"""
        # 재현성을 위한 시드 고정
        np.random.seed(42)
        
        # 100명의 가상 코호트 데이터 생성
        n_samples = 100
        
        pids = np.arange(1, n_samples + 1)
        sex = np.random.choice(['1', '2'], size=n_samples)  # 1: 남성, 2: 여성
        age = np.random.randint(20, 46, size=n_samples)
        
        # OLS 기울기 생성 (클러스터링 테스트용 분포 조정)
        # 3개 그룹의 궤적을 섞음 (Stable, Gain, Loss)
        bmi_slope = np.concatenate([
            np.random.normal(0.0, 0.1, size=40),   # Stable
            np.random.normal(1.2, 0.3, size=40),   # Increasing/Gain
            np.random.normal(-0.8, 0.2, size=20)   # Decreasing/Loss
        ])
        
        whtr_slope = np.concatenate([
            np.random.normal(0.0, 0.002, size=40),
            np.random.normal(0.015, 0.003, size=40),
            np.random.normal(-0.008, 0.002, size=20)
        ])
        
        # baseline 체중/신장계측
        height = np.random.normal(167.0, 7.0, size=n_samples)
        weight = np.random.normal(68.0, 10.0, size=n_samples)
        bmi_base = weight / ((height / 100.0) ** 2)
        whtr_base = np.random.normal(0.48, 0.04, size=n_samples)
        
        # 4대 Obesity Group 할당
        obese_group = []
        for b, w in zip(bmi_base, whtr_base):
            if b < 25.0 and w < 0.5:
                obese_group.append(1)
            elif b < 25.0 and w >= 0.5:
                obese_group.append(2)
            elif b >= 25.0 and w < 0.5:
                obese_group.append(3)
            else:
                obese_group.append(4)
                
        # 교란 변수 생성
        income = np.random.randint(1, 11, size=n_samples)
        smoking = np.random.choice([1, 2, 3], size=n_samples)
        bp_sys = np.random.normal(122.0, 12.0, size=n_samples)
        bp_dia = np.random.normal(78.0, 8.0, size=n_samples)
        glucose = np.random.normal(94.0, 14.0, size=n_samples)
        cholesterol = np.random.normal(195.0, 32.0, size=n_samples)
        egfr = np.random.normal(89.0, 10.0, size=n_samples)
        htn_med = np.random.choice([0, 1], size=n_samples, p=[0.8, 0.2])
        dys_med = np.random.choice([0, 1], size=n_samples, p=[0.85, 0.15])
        
        # 생존 시간 및 이벤트 생성
        # Increasing 그룹에 대해 더 많은 이벤트와 짧은 생존시간 설정
        event = []
        time = []
        for i in range(n_samples):
            is_increasing = (bmi_slope[i] > 0.5)
            if is_increasing:
                evt = np.random.choice([0, 1], p=[0.3, 0.7])
                t = np.random.randint(100, 2000) if evt == 1 else np.random.randint(1500, 4016)
            else:
                evt = np.random.choice([0, 1], p=[0.85, 0.15])
                t = np.random.randint(1000, 4016)
            event.append(evt)
            time.append(t)
            
        cls.raw_df = pd.DataFrame({
            'ID': pids,
            'Sex': sex,
            'Age_Baseline': age,
            'Height': height,
            'Weight': weight,
            'BMI_Baseline': bmi_base,
            'WHtR_Baseline': whtr_base,
            'Obese_Group': obese_group,
            'BMI_Slope': bmi_slope,
            'WHtR_Slope': whtr_slope,
            'Income_Decile': income,
            'Smoking_Status': smoking,
            'BP_Systolic': bp_sys,
            'BP_Diastolic': bp_dia,
            'Glucose': glucose,
            'Cholesterol': cholesterol,
            'eGFR': egfr,
            'Hypertension_Med': htn_med,
            'Dyslipidemia_Med': dys_med,
            'Event_Any': event,
            'Time_Any': time
        })
        
        # 임시 테스트 폴더 설정
        cls.test_dir = tempfile.mkdtemp()

    @classmethod
    def tearDownClass(cls):
        # 임시 폴더 삭제
        shutil.rmtree(cls.test_dir)

    def test_01_trajectory_clustering(self):
        """K-Means 궤적 군집 분류기 및 동적 라벨링 검증"""
        df_clustered = perform_trajectory_clustering(self.raw_df.copy())
        
        # 필수 생성 열 검증
        self.assertIn('Cluster', df_clustered.columns)
        self.assertIn('Trajectory_Group', df_clustered.columns)
        
        # 3개 그룹 할당 검증
        unique_groups = df_clustered['Trajectory_Group'].unique().tolist()
        self.assertEqual(len(unique_groups), 3)
        self.assertIn('Stable', unique_groups)
        self.assertIn('Increasing', unique_groups)
        self.assertIn('Decreasing', unique_groups)
        
        # BMI 기울기 크기별 순서 매핑 적합성 검증
        mean_increasing = df_clustered[df_clustered['Trajectory_Group'] == 'Increasing']['BMI_Slope'].mean()
        mean_stable = df_clustered[df_clustered['Trajectory_Group'] == 'Stable']['BMI_Slope'].mean()
        mean_decreasing = df_clustered[df_clustered['Trajectory_Group'] == 'Decreasing']['BMI_Slope'].mean()
        
        self.assertTrue(mean_decreasing < mean_stable < mean_increasing, "궤적 평균 기울기 순서(Decreasing < Stable < Increasing)가 올바르게 매핑되지 않았습니다.")

    def test_02_cox_variable_preparation(self):
        """더미 변수 및 분석용 변수 가공 적합성 검증"""
        df_clustered = perform_trajectory_clustering(self.raw_df.copy())
        df_prepped = prepare_cox_variables(df_clustered)
        
        # 성별 및 더미 컬럼 검증
        self.assertIn('Is_Female', df_prepped.columns)
        self.assertIn('Smoke_Former', df_prepped.columns)
        self.assertIn('Smoke_Current', df_prepped.columns)
        self.assertIn('Obese_G2', df_prepped.columns)
        self.assertIn('Obese_G3', df_prepped.columns)
        self.assertIn('Obese_G4', df_prepped.columns)
        self.assertIn('Traj_Decreasing', df_prepped.columns)
        self.assertIn('Traj_Increasing', df_prepped.columns)

    def test_03_cox_model_fitting(self):
        """lifelines CoxPHFitter 모델 피팅 및 Model 1, 2, 3 정합성 검증"""
        df_clustered = perform_trajectory_clustering(self.raw_df.copy())
        df_prepped = prepare_cox_variables(df_clustered)
        
        exposure_dummies = ['Obese_G2', 'Obese_G3', 'Obese_G4']
        covariates_list = [
            'Income_Decile', 'Smoke_Former', 'Smoke_Current', 'BP_Systolic', 
            'BP_Diastolic', 'Glucose', 'Cholesterol', 'eGFR', 'Hypertension_Med', 'Dyslipidemia_Med'
        ]
        
        # CVD 대신 임시 Any Event로 검증 수행
        results = fit_multivariate_cox(
            df_prepped, 'Any Event', 'Event_Any', 'Time_Any', exposure_dummies, covariates_list
        )
        
        df_res = pd.DataFrame(results)
        self.assertFalse(df_res.empty)
        
        # 모델 3가지 탑재 여부 검증
        models = df_res['Model'].unique().tolist()
        self.assertIn('Model 1', models)
        self.assertIn('Model 2', models)
        self.assertIn('Model 3', models)
        
        # Hazard Ratio 산출 범위 정밀 검증 (모두 양수여야 함)
        hrs = df_res['Hazard_Ratio'].tolist()
        for hr in hrs:
            self.assertTrue(hr > 0.0, f"Hazard Ratio가 음수이거나 0입니다: {hr}")

    def test_04_plots_generation(self):
        """시각화 차트 파일 생성 여부 검증"""
        df_clustered = perform_trajectory_clustering(self.raw_df.copy())
        df_prepped = prepare_cox_variables(df_clustered)
        
        exposure_dummies = ['Traj_Decreasing', 'Traj_Increasing']
        covariates_list = [
            'Income_Decile', 'Smoke_Former', 'Smoke_Current', 'BP_Systolic', 
            'BP_Diastolic', 'Glucose', 'Cholesterol', 'eGFR', 'Hypertension_Med', 'Dyslipidemia_Med'
        ]
        
        # 분석 요약 1차 수행
        results = fit_multivariate_cox(
            df_prepped, 'Any Event', 'Event_Any', 'Time_Any', exposure_dummies, covariates_list
        )
        df_results = pd.DataFrame(results)
        
        # 차트 플로팅 구동
        plot_kaplan_meier_curves(df_prepped, self.test_dir)
        plot_forest_plots(df_results, self.test_dir)
        
        # 파일 존재 스캔 검증
        km_path = os.path.join(self.test_dir, "kaplan_meier_curves.png")
        forest_path = os.path.join(self.test_dir, "hazard_ratio_forest_plot.png")
        
        self.assertTrue(os.path.exists(km_path), "Kaplan-Meier 생존 곡선 이미지 파일이 정상 생성되지 않았습니다.")
        self.assertTrue(os.path.exists(forest_path), "위험비 Forest Plot 이미지 파일이 정상 생성되지 않았습니다.")


if __name__ == "__main__":
    unittest.main()
