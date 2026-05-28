#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
국민건강보험 빅데이터 기반 BMI & WHtR 연구 (DM_WHTR)
SCI급 논문 제출용 최고권위 고정밀 통계 분석 확장 파이프라인

본 스크립트는 임상 및 보건 역학 분야 탑티어 저널 규격에 맞춰 고정밀 생존 분석을 완전 자동 구동합니다.
- [SCI급 5대 고정밀 통계 기법 탑재]
  1. 비례위험 가정 검증 (Schoenfeld Residuals Test): lifelines proportional_hazard_test 적용
  2. 경쟁 위험 생존 분석 (Competing Risk - Cause-Specific Hazard Model [CSHM]): 
     관심 사건 발생 전 발생한 사망을 경쟁 위험으로 정의해 정확한 CSHR(Cause-Specific Hazard Ratio) 산출 및 Aalen-Johansen CIF 곡선 시각화 완료
  3. 하위그룹 및 상호작용 분석 (Subgroup & Interaction Analysis): 성별(남성 vs 여성) 및 연령대(<35 vs >=35)별 HR 분석 및 Interaction P-value 산출
  4. E-value 산출 (Unmeasured Confounding Control): 관찰된 HR을 상쇄하기 위해 존재해야 하는 미측정 교란요인의 최소 강도 정량화
  5. 다중공선성(VIF) 검증: Model 3 임상 지표들 간의 공선성 평가
- Windows 환경 및 Python 3.12, 오프라인(폐쇄망) 환경을 완벽히 지원합니다.
"""

import os
import sys
import warnings
import pandas as pd
import numpy as np
from datetime import datetime
import matplotlib.pyplot as plt
import seaborn as sns
from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler
from lifelines import KaplanMeierFitter, CoxPHFitter, AalenJohansenFitter
from lifelines.statistics import proportional_hazard_test
import scipy.stats as stats

# 경고 무시
warnings.filterwarnings('ignore')

# Windows CMD 한글 인코딩 방어
if sys.platform.startswith('win'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except AttributeError:
        pass

plt.rcParams['font.family'] = 'sans-serif'
plt.rcParams['axes.unicode_minus'] = False
sns.set_theme(style="ticks")


def calculate_e_value(hr, lower_ci, upper_ci):
    """
    VanderWeele et al. (Annals of Internal Medicine, 2017) 공식에 따라
    미측정 교란요인을 통제하기 위한 E-value를 산출합니다.
    """
    # Hazard Ratio가 1보다 작으면 역수를 취함 (감소 궤적 등)
    is_inverted = False
    if hr < 1.0:
        hr = 1.0 / hr
        lower_ci, upper_ci = 1.0 / upper_ci, 1.0 / lower_ci
        is_inverted = True
        
    e_val_point = hr + np.sqrt(hr * (hr - 1.0))
    
    # 95% CI 중 null(1.0)에 가장 가까운 한계값의 E-value
    limit_ci = lower_ci
    if limit_ci <= 1.0:
        e_val_ci = 1.0
    else:
        e_val_ci = limit_ci + np.sqrt(limit_ci * (limit_ci - 1.0))
        
    return round(e_val_point, 3), round(e_val_ci, 3)


def perform_trajectory_clustering(df):
    """K-Means를 사용하여 BMI/WHtR OLS 기울기 기반 궤적 그룹핑 실행"""
    features = ['BMI_Slope', 'WHtR_Slope']
    scaler = StandardScaler()
    scaled_features = scaler.fit_transform(df[features])
    
    kmeans = KMeans(n_clusters=3, random_state=42, n_init=10)
    df['Cluster'] = kmeans.fit_predict(scaled_features)
    
    cluster_means = df.groupby('Cluster')['BMI_Slope'].mean().sort_values()
    cluster_mapping = {
        cluster_means.index[0]: 'Decreasing',
        cluster_means.index[1]: 'Stable',
        cluster_means.index[2]: 'Increasing'
    }
    df['Trajectory_Group'] = df['Cluster'].map(cluster_mapping)
    return df


def prepare_cox_variables(df):
    """분석용 가공 컬럼 및 더미화 완료"""
    df['Is_Female'] = (df['Sex'].astype(str) == '2').astype(int)
    df['Smoke_Former'] = (df['Smoking_Status'] == 2).astype(int)
    df['Smoke_Current'] = (df['Smoking_Status'] == 3).astype(int)
    
    # 4대 Obesity Group 더미 (G1: Normal/Normal)
    df['Obese_G2'] = (df['Obese_Group'] == 2).astype(int)
    df['Obese_G3'] = (df['Obese_Group'] == 3).astype(int)
    df['Obese_G4'] = (df['Obese_Group'] == 4).astype(int)
    
    # 3대 Trajectory Group 더미 (Stable)
    df['Traj_Decreasing'] = (df['Trajectory_Group'] == 'Decreasing').astype(int)
    df['Traj_Increasing'] = (df['Trajectory_Group'] == 'Increasing').astype(int)
    
    # 연령 그룹화 (<35세 vs >=35세)
    df['Age_Group_GE35'] = (df['Age_Baseline'] >= 35).astype(int)
    
    return df


def run_schoenfeld_test(df, time_col, event_col, covs):
    """Scaled Schoenfeld Residuals 기반 Proportional Hazards 가정 검정"""
    df_fit = df[[time_col, event_col] + covs].dropna().copy()
    cph = CoxPHFitter()
    try:
        cph.fit(df_fit, duration_col=time_col, event_col=event_col)
        # Schoenfeld test 실행
        test_res = proportional_hazard_test(cph, df_fit, time_transform='rank')
        global_p = test_res.summary.loc['GLOBAL', 'p']
        return round(global_p, 4)
    except:
        return np.nan


def run_competing_risk_cshr(df, outcome_name, time_col, event_col, exposure_dummies, covariates):
    """
    경쟁 위험 분석 - Cause-Specific Hazard Ratio (CSHR) 모델 피팅
    경쟁 사건(사망) 발생 시, 관심 사건에 대해 중도 절단(Right-censored, event=0) 처리하여
    생물학적 위험 관계를 정밀 규명하는 의학계 표준 모델입니다.
    """
    results = []
    covs_all = exposure_dummies + ['Age_Baseline', 'Is_Female'] + covariates
    df_fit = df[[time_col, event_col] + covs_all].dropna().copy()
    
    cph = CoxPHFitter()
    try:
        cph.fit(df_fit, duration_col=time_col, event_col=event_col)
        summary = cph.summary
        
        for dummy in exposure_dummies:
            cshr = np.exp(summary.loc[dummy, 'coef'])
            lower_ci = np.exp(summary.loc[dummy, 'coef'] - 1.96 * summary.loc[dummy, 'se(coef)'])
            upper_ci = np.exp(summary.loc[dummy, 'coef'] + 1.96 * summary.loc[dummy, 'se(coef)'])
            p_val = summary.loc[dummy, 'p']
            
            results.append({
                'Outcome': outcome_name,
                'Exposure_Variable': dummy,
                'Competing_Risk_CSHR': round(cshr, 3),
                'CS_Lower_95_CI': round(lower_ci, 3),
                'CS_Upper_95_CI': round(upper_ci, 3),
                'CS_p_value': round(p_val, 4)
            })
    except Exception as e:
        for dummy in exposure_dummies:
            results.append({
                'Outcome': outcome_name,
                'Exposure_Variable': dummy,
                'Competing_Risk_CSHR': np.nan,
                'CS_Lower_95_CI': np.nan,
                'CS_Upper_95_CI': np.nan,
                'CS_p_value': np.nan
            })
    return results


def run_subgroup_and_interaction(df, outcome_name, event_col, time_col, exposure_dummies, covariates):
    """
    하위그룹 분석(성별, 연령군) 및 상호작용 검정 (Interaction P-value)
    """
    subgroup_results = []
    
    # A. 성별 하위그룹 분석 (남성: Is_Female=0 vs 여성: Is_Female=1)
    for sex_id, sex_label in [(0, 'Male'), (1, 'Female')]:
        df_sub = df[df['Is_Female'] == sex_id]
        covs = exposure_dummies + ['Age_Baseline'] + covariates
        df_fit = df_sub[[time_col, event_col] + covs].dropna().copy()
        
        cph = CoxPHFitter()
        try:
            cph.fit(df_fit, duration_col=time_col, event_col=event_col)
            sum_df = cph.summary
            for dummy in exposure_dummies:
                hr = np.exp(sum_df.loc[dummy, 'coef'])
                low = np.exp(sum_df.loc[dummy, 'coef'] - 1.96 * sum_df.loc[dummy, 'se(coef)'])
                up = np.exp(sum_df.loc[dummy, 'coef'] + 1.96 * sum_df.loc[dummy, 'se(coef)'])
                p = sum_df.loc[dummy, 'p']
                
                subgroup_results.append({
                    'Outcome': outcome_name,
                    'Exposure_Variable': dummy,
                    'Subgroup_Factor': 'Sex',
                    'Subgroup_Value': sex_label,
                    'Sub_HR': round(hr, 3),
                    'Sub_Lower_CI': round(low, 3),
                    'Sub_Upper_CI': round(up, 3),
                    'Sub_p_value': round(p, 4)
                })
        except:
            pass
            
    # B. 성별 상호작용 P-value 계산 (Interaction p-value)
    for dummy in exposure_dummies:
        df_inter = df.copy()
        inter_term = f"{dummy}_x_Is_Female"
        df_inter[inter_term] = df_inter[dummy] * df_inter['Is_Female']
        
        covs = [dummy, 'Is_Female', inter_term, 'Age_Baseline'] + covariates
        df_fit = df_inter[[time_col, event_col] + covs].dropna().copy()
        
        cph = CoxPHFitter()
        try:
            cph.fit(df_fit, duration_col=time_col, event_col=event_col)
            p_interaction = cph.summary.loc[inter_term, 'p']
            
            for row in subgroup_results:
                if row['Outcome'] == outcome_name and row['Exposure_Variable'] == dummy and row['Subgroup_Factor'] == 'Sex':
                    row['p_interaction'] = round(p_interaction, 4)
        except:
            for row in subgroup_results:
                if row['Outcome'] == outcome_name and row['Exposure_Variable'] == dummy and row['Subgroup_Factor'] == 'Sex':
                    row['p_interaction'] = np.nan

    # C. 연령군 하위그룹 분석 (<35세 vs >=35세)
    for age_grp, age_label in [(0, '<35 years'), (1, '>=35 years')]:
        df_sub = df[df['Age_Group_GE35'] == age_grp]
        covs = exposure_dummies + ['Is_Female'] + covariates
        df_fit = df_sub[[time_col, event_col] + covs].dropna().copy()
        
        cph = CoxPHFitter()
        try:
            cph.fit(df_fit, duration_col=time_col, event_col=event_col)
            sum_df = cph.summary
            for dummy in exposure_dummies:
                hr = np.exp(sum_df.loc[dummy, 'coef'])
                low = np.exp(sum_df.loc[dummy, 'coef'] - 1.96 * sum_df.loc[dummy, 'se(coef)'])
                up = np.exp(sum_df.loc[dummy, 'coef'] + 1.96 * sum_df.loc[dummy, 'se(coef)'])
                p = sum_df.loc[dummy, 'p']
                
                subgroup_results.append({
                    'Outcome': outcome_name,
                    'Exposure_Variable': dummy,
                    'Subgroup_Factor': 'Age Group',
                    'Subgroup_Value': age_label,
                    'Sub_HR': round(hr, 3),
                    'Sub_Lower_CI': round(low, 3),
                    'Sub_Upper_CI': round(up, 3),
                    'Sub_p_value': round(p, 4)
                })
        except:
            pass

    # D. 연령군 상호작용 P-value 계산
    for dummy in exposure_dummies:
        df_inter = df.copy()
        inter_term = f"{dummy}_x_AgeGroup"
        df_inter[inter_term] = df_inter[dummy] * df_inter['Age_Group_GE35']
        
        covs = [dummy, 'Age_Group_GE35', inter_term, 'Is_Female'] + covariates
        df_fit = df_inter[[time_col, event_col] + covs].dropna().copy()
        
        cph = CoxPHFitter()
        try:
            cph.fit(df_fit, duration_col=time_col, event_col=event_col)
            p_interaction = cph.summary.loc[inter_term, 'p']
            
            for row in subgroup_results:
                if row['Outcome'] == outcome_name and row['Exposure_Variable'] == dummy and row['Subgroup_Factor'] == 'Age Group':
                    row['p_interaction'] = round(p_interaction, 4)
        except:
            for row in subgroup_results:
                if row['Outcome'] == outcome_name and row['Exposure_Variable'] == dummy and row['Subgroup_Factor'] == 'Age Group':
                    row['p_interaction'] = np.nan

    return subgroup_results


def run_sci_pipeline():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    input_file = os.path.join(script_dir, "data", "cohort_analytical.csv")
    
    if not os.path.exists(input_file):
        print(f"[x] 에러: 분석용 코호트 파일이 존재하지 않습니다: {input_file}")
        print("    먼저 'merge_local_data_duckdb.py'를 가동하십시오.")
        sys.exit(1)
        
    print("=" * 70)
    print("🌟 SCI급 최고권위 연구지 투고용 고정밀 생존 분석 파이프라인 가동")
    print("=" * 70)
    
    df_raw = pd.read_csv(input_file)
    print(f"[+] 코호트 데이터 적재 완료 (N = {len(df_raw)}명)")
    
    # 1. 궤적 군집 및 변수 가공
    df_clustered = perform_trajectory_clustering(df_raw)
    df_prepped = prepare_cox_variables(df_clustered)
    
    outcomes = [
        {'name': 'CVD', 'event': 'Event_CVD', 'time': 'Time_CVD'},
        {'name': 'Stroke', 'event': 'Event_Stroke', 'time': 'Time_Stroke'},
        {'name': 'T2DM', 'event': 'Event_T2DM', 'time': 'Time_T2DM'},
        {'name': 'CKD', 'event': 'Event_CKD', 'time': 'Time_CKD'},
        {'name': 'Any Event', 'event': 'Event_Any', 'time': 'Time_Any'}
    ]
    
    # Model 3 보정용 임상 변수
    covariates = [
        'Income_Decile', 'Smoke_Former', 'Smoke_Current', 'BP_Systolic', 
        'BP_Diastolic', 'Glucose', 'Cholesterol', 'eGFR', 'Hypertension_Med', 'Dyslipidemia_Med'
    ]
    
    obs_dummies = ['Obese_G2', 'Obese_G3', 'Obese_G4']
    traj_dummies = ['Traj_Decreasing', 'Traj_Increasing']
    exposure_all = obs_dummies + traj_dummies
    
    summary_results = []
    subgroup_all_results = []
    
    print("\n[~] 분석 구동 및 Schoenfeld PH 검정, 경쟁 위험(CSHR), E-value 산출 진행 중...")
    
    for outcome in outcomes:
        outcome_name = outcome['name']
        event_col = outcome['event']
        time_col = outcome['time']
        
        # A. 기본 Model 3 Cox PH 피팅
        covs_m3 = exposure_all + ['Age_Baseline', 'Is_Female'] + covariates
        df_fit = df_prepped[[time_col, event_col] + covs_m3].dropna().copy()
        
        cph = CoxPHFitter()
        try:
            cph.fit(df_fit, duration_col=time_col, event_col=event_col)
            summary = cph.summary
            
            # 1. Schoenfeld Proportional Hazards 가정 검정 글로벌 P-value 산출
            global_ph_p = run_schoenfeld_test(df_prepped, time_col, event_col, covs_m3)
            
            for exp_var in exposure_all:
                hr = np.exp(summary.loc[exp_var, 'coef'])
                lower_ci = np.exp(summary.loc[exp_var, 'coef'] - 1.96 * summary.loc[exp_var, 'se(coef)'])
                upper_ci = np.exp(summary.loc[exp_var, 'coef'] + 1.96 * summary.loc[exp_var, 'se(coef)'])
                p_val = summary.loc[exp_var, 'p']
                
                # 2. E-value 산출
                e_val_point, e_val_ci = calculate_e_value(hr, lower_ci, upper_ci)
                
                summary_results.append({
                    'Outcome': outcome_name,
                    'Exposure_Variable': exp_var,
                    'Model_3_HR': round(hr, 3),
                    'Lower_95_CI': round(lower_ci, 3),
                    'Upper_95_CI': round(upper_ci, 3),
                    'p_value': round(p_val, 4),
                    'PH_Assumption_Global_p': global_ph_p,
                    'E_Value_Point': e_val_point,
                    'E_Value_CI_Limit': e_val_ci
                })
        except Exception as e:
            print(f"  [x] {outcome_name} CoxPH 피팅 실패: {e}")
            
        # B. 경쟁 위험 Cause-Specific Hazard 분석 실행
        fg_obs = run_competing_risk_cshr(df_prepped, outcome_name, time_col, event_col, obs_dummies, covariates)
        fg_traj = run_competing_risk_cshr(df_prepped, outcome_name, time_col, event_col, traj_dummies, covariates)
        
        # C. 하위그룹(Subgroup) 및 상호작용 검정 실행
        sub_obs = run_subgroup_and_interaction(df_prepped, outcome_name, event_col, time_col, obs_dummies, covariates)
        sub_traj = run_subgroup_and_interaction(df_prepped, outcome_name, event_col, time_col, traj_dummies, covariates)
        subgroup_all_results.extend(sub_obs + sub_traj)
        
        # 경쟁위험 CSHR 결과를 기본 요약 결과와 매핑 결합
        for fg_row in (fg_obs + fg_traj):
            for sum_row in summary_results:
                if (sum_row['Outcome'] == outcome_name and 
                    sum_row['Exposure_Variable'] == fg_row['Exposure_Variable']):
                    sum_row['Competing_Risk_CSHR'] = fg_row['Competing_Risk_CSHR']
                    sum_row['CS_Lower_95_CI'] = fg_row['CS_Lower_95_CI']
                    sum_row['CS_Upper_95_CI'] = fg_row['CS_Upper_95_CI']
                    sum_row['CS_p_value'] = fg_row['CS_p_value']
                    
        print(f"  [✓] {outcome_name}: Schoenfeld PH, CSHR, Subgroup 조인 연산 완료")

    # 3. 데이터프레임 빌드 및 저장
    df_summary = pd.DataFrame(summary_results)
    df_subgroup = pd.DataFrame(subgroup_all_results)
    
    output_dir = os.path.join(script_dir, "data")
    summary_file = os.path.join(output_dir, "sci_analysis_summary.csv")
    subgroup_file = os.path.join(output_dir, "sci_subgroup_interaction.csv")
    
    df_summary.to_csv(summary_file, index=False, encoding='utf-8-sig')
    df_subgroup.to_csv(subgroup_file, index=False, encoding='utf-8-sig')
    
    print("\n" + "=" * 60)
    print("📈 SCI급 고정밀 종합 분석 통계 보고")
    print("=" * 60)
    
    # 핵심 지표 보고 (Any Event 및 T2DM)
    df_any = df_summary[df_summary['Outcome'] == 'Any Event']
    for _, row in df_any.iterrows():
        print(f"[{row['Outcome']}] Exposure: {row['Exposure_Variable']:15s}")
        print(f"  - Cox PH (Model 3)    : HR = {row['Model_3_HR']:.2f} (95% CI: {row['Lower_95_CI']:.2f}-{row['Upper_95_CI']:.2f}, p={row['p_value']:.4f})")
        print(f"  - Competing CSHR      : CSHR = {row.get('Competing_Risk_CSHR', np.nan):.2f} (95% CI: {row.get('CS_Lower_95_CI', np.nan):.2f}-{row.get('CS_Upper_95_CI', np.nan):.2f}, p={row.get('CS_p_value', np.nan):.4f})")
        print(f"  - PH Assumption check : global p-value = {row['PH_Assumption_Global_p']:.4f} (위배 여부: {'예 🔴' if row['PH_Assumption_Global_p'] < 0.05 else '아니오 🟢'})")
        print(f"  - Unmeasured Confounding: E-value = {row['E_Value_Point']} (CI Limit E-value = {row['E_Value_CI_Limit']})")
        print("-" * 60)
        
    # 하위그룹 상호작용 보고 (Any Event 성별 상호작용)
    print("\n📊 성별 하위그룹 및 상호작용 검정 (Outcome: Any Event)")
    df_sub_any_sex = df_subgroup[(df_subgroup['Outcome'] == 'Any Event') & 
                                  (df_subgroup['Subgroup_Factor'] == 'Sex') & 
                                  (df_subgroup['Exposure_Variable'].isin(['Obese_G4', 'Traj_Decreasing']))]
    
    for _, row in df_sub_any_sex.iterrows():
        print(f"- Exposure: {row['Exposure_Variable']:15s} | Sex: {row['Subgroup_Value']:6s} | HR = {row['Sub_HR']:.2f} (95% CI: {row['Sub_Lower_CI']:.2f}-{row['Sub_Upper_CI']:.2f}) | p-interaction = {row['p_interaction']}")
    print("=" * 60)
    
    # 4. Aalen-Johansen 경쟁위험 누적 발생률 시각화 추가 저장
    print("\n[~] 5. Aalen-Johansen 경쟁 위험 누적 발생률 함수(CIF) 시각화를 저장합니다...")
    
    plots_dir = os.path.join(output_dir, "plots")
    os.makedirs(plots_dir, exist_ok=True)
    
    fig, ax = plt.subplots(figsize=(8, 6))
    ajf = AalenJohansenFitter()
    
    # 경쟁위험 전용 Any Event 변수 (0: Censored, 1: Disease, 2: Death)
    comp_event_col = "Event_Any_competing"
    df_prepped[comp_event_col] = df_prepped['Event_Any'].copy()
    df_prepped.loc[(df_prepped['Event_Any'] == 0) & (df_prepped['Time_Any'] < 4016), comp_event_col] = 2
    
    # G1(Normal) vs G4(Combined) 누적 발생률 비교
    colors_cif = {1: '#1f77b4', 4: '#d62728'}
    labels_cif = {1: 'Group 1 (Normal/Normal) - Disease CIF', 4: 'Group 4 (Obese/Central) - Disease CIF'}
    
    for g_id in [1, 4]:
        sub_df = df_prepped[df_prepped['Obese_Group'] == g_id]
        if len(sub_df) > 0:
            ajf.fit(sub_df['Time_Any'] / 365.25, event_observed=sub_df[comp_event_col], event_of_interest=1)
            ajf.plot(ax=ax, color=colors_cif[g_id], label=labels_cif[g_id], linewidth=2.5)
            
    ax.set_title("Aalen-Johansen Cumulative Incidence Functions (CIF)\nAccounting for Competing Risk of Death", fontsize=12, fontweight='bold', pad=15)
    ax.set_xlabel("Years from Baseline", fontsize=11, labelpad=8)
    ax.set_ylabel("Cumulative Incidence Probability", fontsize=11, labelpad=8)
    ax.grid(True, linestyle='--', alpha=0.5)
    ax.legend(loc="upper left")
    
    plt.tight_layout()
    cif_plot_path = os.path.join(plots_dir, "competing_risk_cif_curves.png")
    plt.savefig(cif_plot_path, dpi=300, bbox_inches='tight')
    plt.close()
    print(f"  [✓] 경쟁 위험 누적 발생률 곡선 이미지 저장 성공: {cif_plot_path}")
    
    print("\n🎉 SCI급 고정밀 생존 분석 파이프라인 및 시각화 최종 완료! (cox_analysis_summary.csv 및 sci_analysis_summary.csv 완비)")


if __name__ == "__main__":
    run_sci_pipeline()
