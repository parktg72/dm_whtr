#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
국민건강보험 빅데이터 기반 BMI & WHtR 연구 (DM_WHTR)
Phase 2 & 3: 종단적 궤적 군집화 및 다변량 Cox 생존 분석 파이프라인

본 스크립트는 AGY 본부(DevOps), Claude 본부(QA/Arch), Codex 본부(Logic Builder)의 협업으로 구현되었습니다.
- [Phase 2] K-Means 클러스터링을 활용해 BMI & WHtR 4개년 선형 기울기를 3대 궤적 군집(Stable, Increasing, Decreasing)으로 분류합니다.
- [Phase 3] 다변량 Cox Proportional Hazards Model을 구동하여 5대 질병 아웃컴에 대한 위험비(HR)를 산출합니다.
  - Model 1: 비보정 (Exposure 단독)
  - Model 2: 연령, 성별 보정
  - Model 3: 임상 및 약물 복용 이력 등 교란변수 전체 보정
- [시각화] Kaplan-Meier 생존 곡선 및 Model 3 위험비 Forest Plot을 학술지 표준 테마로 시각화 출력합니다.
- Windows 환경 및 Python 3.12 한글 인코딩(`utf-8-sig`)을 완벽하게 지원합니다.
"""

import os
import sys
import warnings
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler
from lifelines import KaplanMeierFitter, CoxPHFitter
from lifelines.statistics import logrank_test

# 경고 무시 (학술 분석 시 깔끔한 콘솔 출력을 위함)
warnings.filterwarnings('ignore')

# Windows CMD 한글 인코딩 방어
if sys.platform.startswith('win'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except AttributeError:
        pass

# matplotlib 폰트 설정 (학술용 기본 sans-serif 설정)
plt.rcParams['font.family'] = 'sans-serif'
plt.rcParams['axes.unicode_minus'] = False
sns.set_theme(style="ticks")


def perform_trajectory_clustering(df):
    """
    K-Means 클러스터링을 활용하여 BMI 및 WHtR 기울기를 3대 궤적 그룹으로 군집화합니다.
    시드 고정을 통해 일관된 궤적 할당을 지원하며, 기울기 평균을 정렬하여 동적으로 라벨링합니다.
    """
    print("\n[~] 1. BMI 및 WHtR 종단 궤적 클러스터링(Phase 2)을 시작합니다...")
    
    # 1. 클러스터링 피처 선택 및 표준화
    features = ['BMI_Slope', 'WHtR_Slope']
    scaler = StandardScaler()
    scaled_features = scaler.fit_transform(df[features])
    
    # 2. KMeans 구동 (Stable, Increasing, Decreasing 3개 군집)
    kmeans = KMeans(n_clusters=3, random_state=42, n_init=10)
    df['Cluster'] = kmeans.fit_predict(scaled_features)
    
    # 3. 클러스터 레이블이 항상 일치하도록 BMI 기울기 평균 기준으로 정렬 및 라벨링
    # 가장 평균 기울기가 큰 것 -> Increasing
    # 가장 평균 기울기가 작은 것 -> Decreasing
    # 중간 -> Stable
    cluster_means = df.groupby('Cluster')['BMI_Slope'].mean().sort_values()
    cluster_mapping = {
        cluster_means.index[0]: 'Decreasing',
        cluster_means.index[1]: 'Stable',
        cluster_means.index[2]: 'Increasing'
    }
    
    df['Trajectory_Group'] = df['Cluster'].map(cluster_mapping)
    print("[+] 궤적 군집 분류 성공 (K-Means K=3):")
    for group in ['Decreasing', 'Stable', 'Increasing']:
        count = (df['Trajectory_Group'] == group).sum()
        mean_bmi_s = df[df['Trajectory_Group'] == group]['BMI_Slope'].mean()
        mean_whtr_s = df[df['Trajectory_Group'] == group]['WHtR_Slope'].mean()
        print(f"  * {group:10s} Trajectory: {count:4d}명 (BMI Slope Mean: {mean_bmi_s:.4f}, WHtR Slope Mean: {mean_whtr_s:.5f})")
        
    return df


def prepare_cox_variables(df):
    """
    Cox Proportional Hazards Model에 투입할 더미 변수 및 분석용 가공 컬럼을 준비합니다.
    """
    # 성별 범주화: '1' -> Male (0), '2' -> Female (1)
    df['Is_Female'] = (df['Sex'].astype(str) == '2').astype(int)
    
    # 흡연 상태 범주화 (1: 비흡연(Reference), 2: 과거, 3: 현재)
    df['Smoke_Former'] = (df['Smoking_Status'] == 2).astype(int)
    df['Smoke_Current'] = (df['Smoking_Status'] == 3).astype(int)
    
    # 4대 Obesity Group 더미 변수화 (Group 1: Normal/Normal(Reference))
    df['Obese_G2'] = (df['Obese_Group'] == 2).astype(int)
    df['Obese_G3'] = (df['Obese_Group'] == 3).astype(int)
    df['Obese_G4'] = (df['Obese_Group'] == 4).astype(int)
    
    # 3대 Trajectory Group 더미 변수화 (Stable(Reference))
    df['Traj_Decreasing'] = (df['Trajectory_Group'] == 'Decreasing').astype(int)
    df['Traj_Increasing'] = (df['Trajectory_Group'] == 'Increasing').astype(int)
    
    return df


def fit_multivariate_cox(df, outcome_name, event_col, time_col, exposure_dummies, covariates_list):
    """
    특정 아웃컴과 보정 목록에 대해 Cox Proportional Hazards Model을 피팅하고 통계 요약을 추출합니다.
    """
    results = []
    
    # Model 정의 구성
    models_dict = {
        'Model 1': exposure_dummies,  # 비보정
        'Model 2': exposure_dummies + ['Age_Baseline', 'Is_Female'],  # 인구학적 보정
        'Model 3': exposure_dummies + ['Age_Baseline', 'Is_Female'] + covariates_list  # 풀 보정
    }
    
    for m_name, covs in models_dict.items():
        analysis_cols = [time_col, event_col] + covs
        df_fit = df[analysis_cols].dropna().copy()
        
        # lifelines CoxPHFitter 피팅
        cph = CoxPHFitter()
        try:
            cph.fit(df_fit, duration_col=time_col, event_col=event_col)
            summary = cph.summary
            
            for dummy in exposure_dummies:
                hr = np.exp(summary.loc[dummy, 'coef'])
                lower_ci = np.exp(summary.loc[dummy, 'coef'] - 1.96 * summary.loc[dummy, 'se(coef)'])
                upper_ci = np.exp(summary.loc[dummy, 'coef'] + 1.96 * summary.loc[dummy, 'se(coef)'])
                p_val = summary.loc[dummy, 'p']
                
                results.append({
                    'Outcome': outcome_name,
                    'Exposure_Variable': dummy,
                    'Model': m_name,
                    'Hazard_Ratio': hr,
                    'Lower_95_CI': lower_ci,
                    'Upper_95_CI': upper_ci,
                    'p_value': p_val,
                    'AIC': cph.AIC_partial_
                })
        except Exception as e:
            print(f"[!] {outcome_name} - {m_name} 피팅 실패: {e}")
            
    return results


def run_entire_survival_analysis(df):
    """
    5개 아웃컴(CVD, Stroke, T2DM, CKD, Any Event)에 대해 Obesity Group 및 Trajectory Group 두 노출 변수를 모두 분석합니다.
    """
    print("\n[~] 2. 다변량 Cox 생존 분석(Phase 3)을 진행합니다...")
    
    outcomes = [
        {'name': 'CVD', 'event': 'Event_CVD', 'time': 'Time_CVD'},
        {'name': 'Stroke', 'event': 'Event_Stroke', 'time': 'Time_Stroke'},
        {'name': 'T2DM', 'event': 'Event_T2DM', 'time': 'Time_T2DM'},
        {'name': 'CKD', 'event': 'Event_CKD', 'time': 'Time_CKD'},
        {'name': 'Any Event', 'event': 'Event_Any', 'time': 'Time_Any'}
    ]
    
    # Model 3용 보정 변수 (연령/성별 제외)
    covariates = [
        'Income_Decile', 'Smoke_Former', 'Smoke_Current', 'BP_Systolic', 
        'BP_Diastolic', 'Glucose', 'Cholesterol', 'eGFR', 'Hypertension_Med', 'Dyslipidemia_Med'
    ]
    
    all_cox_results = []
    
    for outcome in outcomes:
        # A. Exposure = Obesity Group
        obs_dummies = ['Obese_G2', 'Obese_G3', 'Obese_G4']
        res_obs = fit_multivariate_cox(df, outcome['name'], outcome['event'], outcome['time'], obs_dummies, covariates)
        all_cox_results.extend(res_obs)
        
        # B. Exposure = Trajectory Group
        traj_dummies = ['Traj_Decreasing', 'Traj_Increasing']
        res_traj = fit_multivariate_cox(df, outcome['name'], outcome['event'], outcome['time'], traj_dummies, covariates)
        all_cox_results.extend(res_traj)
        
        print(f"  [✓] {outcome['name']}: 생존 분석 완료")
        
    df_results = pd.DataFrame(all_cox_results)
    
    # 소수점 정렬 및 가독성 개선
    df_results['Hazard_Ratio'] = df_results['Hazard_Ratio'].round(3)
    df_results['Lower_95_CI'] = df_results['Lower_95_CI'].round(3)
    df_results['Upper_95_CI'] = df_results['Upper_95_CI'].round(3)
    df_results['p_value'] = df_results['p_value'].round(4)
    df_results['AIC'] = df_results['AIC'].round(2)
    
    return df_results


def plot_kaplan_meier_curves(df, output_dir):
    """
    Obesity Group과 Trajectory Group에 따른 Kaplan-Meier 생존 곡선을 그려 저장합니다.
    """
    print("\n[~] 3. Kaplan-Meier 생존 곡선 시각화 작업을 진행합니다...")
    
    fig, axes = plt.subplots(1, 2, figsize=(16, 7))
    
    # A. Obesity Group별 KM (Outcome: Any Event)
    ax = axes[0]
    kmf = KaplanMeierFitter()
    
    group_labels = {
        1: 'Group 1 (Normal/Normal)',
        2: 'Group 2 (Normal/Central Obese)',
        3: 'Group 3 (Obese/Normal)',
        4: 'Group 4 (Obese/Central Obese)'
    }
    colors_obs = ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728']
    
    for g_id, label in group_labels.items():
        sub_df = df[df['Obese_Group'] == g_id]
        if len(sub_df) > 0:
            kmf.fit(sub_df['Time_Any'] / 365.25, event_observed=sub_df['Event_Any'], label=label)
            kmf.plot_survival_function(ax=ax, color=colors_obs[g_id-1], ci_show=False, linewidth=2.5)
            
    ax.set_title("Kaplan-Meier Survival Curves by Obesity Groups\n(Outcome: Any Chronic Disease)", fontsize=13, fontweight='bold', pad=15)
    ax.set_xlabel("Years from Baseline", fontsize=11, labelpad=8)
    ax.set_ylabel("Survival Probability", fontsize=11, labelpad=8)
    ax.grid(True, linestyle='--', alpha=0.5)
    ax.legend(title="Obesity Groups", loc="lower left", frameon=True)
    
    # B. Trajectory Group별 KM (Outcome: Any Event)
    ax = axes[1]
    colors_traj = {
        'Stable': '#2ca02c',
        'Increasing': '#d62728',
        'Decreasing': '#1f77b4'
    }
    
    for traj in ['Stable', 'Increasing', 'Decreasing']:
        sub_df = df[df['Trajectory_Group'] == traj]
        if len(sub_df) > 0:
            kmf.fit(sub_df['Time_Any'] / 365.25, event_observed=sub_df['Event_Any'], label=f"{traj} Trajectory")
            kmf.plot_survival_function(ax=ax, color=colors_traj[traj], ci_show=False, linewidth=2.5)
            
    ax.set_title("Kaplan-Meier Survival Curves by BMI/WHtR Trajectories\n(Outcome: Any Chronic Disease)", fontsize=13, fontweight='bold', pad=15)
    ax.set_xlabel("Years from Baseline", fontsize=11, labelpad=8)
    ax.set_ylabel("Survival Probability", fontsize=11, labelpad=8)
    ax.grid(True, linestyle='--', alpha=0.5)
    ax.legend(title="Trajectories", loc="lower left", frameon=True)
    
    plt.tight_layout()
    plot_path = os.path.join(output_dir, "kaplan_meier_curves.png")
    plt.savefig(plot_path, dpi=300, bbox_inches='tight')
    plt.close()
    print(f"  [✓] Kaplan-Meier 생존 곡선 이미지 저장 성공: {plot_path}")


def plot_forest_plots(df_results, output_dir):
    """
    Model 3(최종 풀보정)의 아웃컴별 위험비(Hazard Ratio)를 시각화하는 Forest Plot을 수동 구축합니다.
    """
    print("\n[~] 4. 위험비 Forest Plot 시각화 작업을 진행합니다...")
    
    # Model 3 최종 풀보정 데이터만 필터링
    df_m3 = df_results[df_results['Model'] == 'Model 3'].copy()
    
    if len(df_m3) == 0:
        print("[!] 에러: Model 3 데이터가 존재하지 않아 Forest Plot 생성을 건너뜁니다.")
        return
        
    # 시각화 가독성 매핑
    label_mapping = {
        'Obese_G2': 'Normal BMI / Central Obese (vs Group 1)',
        'Obese_G3': 'Obese BMI / Normal WHtR (vs Group 1)',
        'Obese_G4': 'Obese BMI / Central Obese (vs Group 1)',
        'Traj_Decreasing': 'Decreasing Trajectory (vs Stable)',
        'Traj_Increasing': 'Increasing Trajectory (vs Stable)'
    }
    
    df_m3['Label'] = df_m3['Exposure_Variable'].map(label_mapping)
    
    outcomes = df_m3['Outcome'].unique()
    fig, axes = plt.subplots(len(outcomes), 1, figsize=(10, 2.5 * len(outcomes)), sharex=True)
    
    if len(outcomes) == 1:
        axes = [axes]
        
    for idx, outcome in enumerate(outcomes):
        ax = axes[idx]
        sub_df = df_m3[df_m3['Outcome'] == outcome].copy()
        
        # 시각화 순서 정렬
        sub_df = sub_df.sort_values('Exposure_Variable', ascending=False)
        
        y_pos = np.arange(len(sub_df))
        
        # 에러바(95% CI)가 포함된 점도표 생성
        ax.errorbar(
            sub_df['Hazard_Ratio'], y_pos, 
            xerr=[sub_df['Hazard_Ratio'] - sub_df['Lower_95_CI'], sub_df['Upper_95_CI'] - sub_df['Hazard_Ratio']],
            fmt='o', color='#1f77b4', ecolor='#2c3e50', elinewidth=1.8, capsize=5, mfc='#e74c3c', mec='#c0392b', ms=8, label='Hazard Ratio (95% CI)'
        )
        
        # 위험비 = 1.0 기준선 설정
        ax.axvline(1.0, color='red', linestyle='--', linewidth=1.5, alpha=0.7)
        
        ax.set_yticks(y_pos)
        ax.set_yticklabels(sub_df['Label'], fontsize=10, fontweight='bold')
        ax.set_title(f"Outcome: {outcome}", fontsize=11, fontweight='bold', loc='left', pad=6, color='#2c3e50')
        ax.grid(True, axis='x', linestyle=':', alpha=0.6)
        
        # 텍스트로 값 표기
        for i, row in enumerate(sub_df.itertuples()):
            pval_text = f"p={row.p_value:.4f}" if row.p_value >= 0.0001 else "p<0.0001"
            ax.text(
                row.Upper_95_CI + 0.1 if row.Upper_95_CI < 4 else row.Hazard_Ratio + 0.2, i, 
                f"HR = {row.Hazard_Ratio:.2f} ({row.Lower_95_CI:.2f}-{row.Upper_95_CI:.2f}), {pval_text}", 
                va='center', fontsize=9.5, color='#34495e', fontweight='semibold'
            )
            
    # 전체 축 포맷팅
    plt.xlabel("Hazard Ratio (log scale reference)", fontsize=11, labelpad=10)
    plt.xscale('log')
    # 로그 스케일 틱 설정
    plt.xticks([0.2, 0.5, 1.0, 2.0, 5.0, 10.0], ['0.2', '0.5', '1.0', '2.0', '5.0', '10.0'])
    plt.xlim(0.1, 15.0)
    
    plt.tight_layout()
    plot_path = os.path.join(output_dir, "hazard_ratio_forest_plot.png")
    plt.savefig(plot_path, dpi=300, bbox_inches='tight')
    plt.close()
    print(f"  [✓] 위험비 Forest Plot 이미지 저장 성공: {plot_path}")


def run_pipeline():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    input_file = os.path.join(script_dir, "data", "cohort_analytical.csv")
    
    if not os.path.exists(input_file):
        print(f"[x] 에러: 분석용 코호트 파일이 존재하지 않습니다: {input_file}")
        print("    먼저 'build_cohort.py'를 가동하여 코호트를 추출하십시오.")
        sys.exit(1)
        
    print("=" * 60)
    print("🚀 종단적 궤적 군집화 및 다변량 Cox 생존 분석 파이프라인 가동")
    print("=" * 60)
    
    # 데이터 로드
    df_raw = pd.read_csv(input_file)
    print(f"[+] 코호트 데이터 로드 완료 (N = {len(df_raw)}명)")
    
    # 1. 궤적 군집화 실행
    df_clustered = perform_trajectory_clustering(df_raw)
    
    # 2. Cox 회귀 변수 가공
    df_prepped = prepare_cox_variables(df_clustered)
    
    # 3. 생존 분석 실행
    df_cox_results = run_entire_survival_analysis(df_prepped)
    
    # 4. 결과 출력 및 저장
    output_dir = os.path.join(script_dir, "data")
    summary_file = os.path.join(output_dir, "cox_analysis_summary.csv")
    df_cox_results.to_csv(summary_file, index=False, encoding='utf-8-sig')
    print(f"\n[+] 생존 분석 종합 통계 저장 완료: {summary_file}")
    
    # 5. 시각화 구동
    plots_dir = os.path.join(output_dir, "plots")
    os.makedirs(plots_dir, exist_ok=True)
    
    plot_kaplan_meier_curves(df_prepped, plots_dir)
    plot_forest_plots(df_cox_results, plots_dir)
    
    print("\n" + "=" * 50)
    print("📊 Model 3 (최종 보정) 주요 위험비(Hazard Ratio) 보고")
    print("=" * 50)
    df_m3 = df_cox_results[df_cox_results['Model'] == 'Model 3']
    for idx, row in df_m3.iterrows():
        pval_str = f"p={row['p_value']:.4f}" if row['p_value'] >= 0.0001 else "p<0.0001"
        print(f"- {row['Outcome']:10s} | {row['Exposure_Variable']:15s} | HR = {row['Hazard_Ratio']:.2f} (95% CI: {row['Lower_95_CI']:.2f} - {row['Upper_95_CI']:.2f}) | {pval_str}")
    print("=" * 50)
    
    print("\n🎉 모든 분석 파이프라인 및 학술 시각화 저장이 성공적으로 완료되었습니다!")


if __name__ == "__main__":
    run_pipeline()
