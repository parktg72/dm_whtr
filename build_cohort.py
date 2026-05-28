#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
국민건강보험 빅데이터 기반 BMI & WHtR 연구 (DM_WHTR)
Phase 1: 코호트 정제 및 궤적 계산 파이프라인 스크립트

본 스크립트는 Codex 본부(Code Builder)와 Claude 본부(Architect)의 협업으로 구현되었습니다.
- SQLite 데이터베이스(`synthetic_nhis.db`)에서 원천 검진 및 자격 데이터를 로드합니다.
- 연구 프로토콜에 따라 다음 필터링 및 변수 연산을 수행합니다:
  1. 연령 조건 (20~45세) 및 4개년(2009~2012년) 중 3회 이상 검진자 추출.
  2. Wash-out Period: 2013-01-01 이전 대상 질병 기왕력 보유자 배제.
  3. Baseline(마지막 검진 기록) 기준 BMI 및 WHtR 산출, 4개 Obesity Group 할당.
  4. OLS 회귀 분석을 통한 BMI 및 WHtR의 4개년 선형 기울기($\beta$) 계산.
  5. 2013-01-01 ~ 2023-12-31(11년간) 추적 관찰을 통한 질병별 Event 및 Survival Time 산출.
  6. 민감도 분석용 Lag-time (최초 1년 이내 이벤트 발생) 제외 코호트 별도 생성.
- Windows 환경 및 Python 3.12 한글 인코딩(`utf-8-sig`)을 지원합니다.
"""

import os
import sys
import sqlite3
from datetime import datetime
import pandas as pd
import numpy as np
from cohort_privacy import hash_identifier, validate_unique_hashed_ids

# Windows CMD 한글 인코딩 방어
if sys.platform.startswith('win'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except AttributeError:
        pass

def calculate_ols_slope(y_values, x_years):
    """
    각 개인의 검진 연도(x)와 지표 값(y)에 대해 최소제곱법(OLS) 선형 회귀 기울기(beta)를 구합니다.
    y = beta * x + alpha
    """
    if len(y_values) < 2:
        return 0.0
    
    # NaN 결측 제거
    valid_indices = ~np.isnan(y_values)
    x = np.array(x_years)[valid_indices]
    y = np.array(y_values)[valid_indices]
    
    if len(x) < 2:
        return 0.0
        
    slope, _ = np.polyfit(x, y, 1)
    return slope


def apply_lag_time_filter(df_cohort):
    return df_cohort[~((df_cohort['Event_Any'] == 1) & (df_cohort['Time_Any'] <= 365))].copy()

def build_cohort():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    db_path = os.path.join(script_dir, "synthetic_nhis.db")
    
    if not os.path.exists(db_path):
        print(f"[x] 에러: 데이터베이스 파일이 존재하지 않습니다: {db_path}")
        print("    먼저 'generate_synthetic_db.py'를 실행하여 DB를 생성하십시오.")
        sys.exit(1)

    print(f"[~] SQLite DB 연동 시작: {db_path}")
    conn = sqlite3.connect(db_path)
    
    # 1. 2009~2012년 검진 자료 및 기본 정보 추출
    # G1E_HGHT 및 G1E_WSTC 결측은 행 단위로 SQL 필터에서 1차 제외
    query = """
    SELECT 
        P.INDI_DSCM_NO,
        P.STD_YYYY,
        P.SEX_TYPE,
        P.BYEAR,
        (CAST(P.STD_YYYY AS INTEGER) - CAST(P.BYEAR AS INTEGER)) AS AGED,
        E.G1E_HGHT,
        E.G1E_WGHT,
        E.G1E_BMI,
        E.G1E_WSTC,
        E.INCOME_DECILE,
        E.SMOKING_STATUS,
        E.BP_SYS,
        E.BP_DIA,
        E.GLUCOSE,
        E.CHOLESTEROL,
        E.EGFR,
        E.HYPERTENSION_MED,
        E.DYSLIPIDEMIA_MED
    FROM ELIGIBILITY P
    INNER JOIN CHECKUP E
        ON P.INDI_DSCM_NO = E.INDI_DSCM_NO
        AND P.STD_YYYY = E.EXMD_BZ_YYYY
    WHERE (CAST(P.STD_YYYY AS INTEGER) - CAST(P.BYEAR AS INTEGER)) BETWEEN 20 AND 45
      AND P.STD_YYYY BETWEEN '2009' AND '2012'
      AND E.G1E_HGHT IS NOT NULL AND E.G1E_HGHT > 0
      AND E.G1E_WGHT IS NOT NULL AND E.G1E_WGHT > 0
      AND E.G1E_WSTC IS NOT NULL AND E.G1E_WSTC > 0
    """
    
    df_raw = pd.read_sql_query(query, conn)
    print(f"[+] 로드된 총 검진 행 수 (20~45세, 신체계측 유효): {len(df_raw):,}개")

    # 2. 2009~2012년 내 최소 3회 이상 건강검진을 받은 대상자 필터링
    checkup_counts = df_raw.groupby('INDI_DSCM_NO')['STD_YYYY'].count()
    valid_pids = checkup_counts[checkup_counts >= 3].index.tolist()
    df_filtered = df_raw[df_raw['INDI_DSCM_NO'].isin(valid_pids)].copy()
    print(f"[+] 3회 이상 검진자 필터링 후 행 수: {len(df_filtered):,}개 (고유 인원: {len(valid_pids):,}명)")

    if len(valid_pids) == 0:
        print("[x] 필터링된 대상자가 없습니다. 파이프라인을 종료합니다.")
        conn.close()
        return

    # 3. 2013-01-01 기준 Wash-out Period (기왕력 제거)
    # 2013년 1월 1일 이전에 질병 진단 이력이 있는 자 조회
    dis_query = """
    SELECT INDI_DSCM_NO, ICD_CODE, DIAG_DATE 
    FROM DISEASE_HISTORY 
    WHERE DIAG_DATE < '2013-01-01'
    """
    df_history = pd.read_sql_query(dis_query, conn)
    
    # 대상 질병 정의: CVD (I20~I25), Stroke (I60~I69), T2DM (E11), CKD (N18)
    def is_target_disease(icd):
        icd = str(icd).strip().upper()
        return (icd.startswith('I20') or icd.startswith('I21') or icd.startswith('I22') or 
                icd.startswith('I23') or icd.startswith('I24') or icd.startswith('I25') or # CVD
                icd.startswith('I60') or icd.startswith('I61') or icd.startswith('I62') or 
                icd.startswith('I63') or icd.startswith('I64') or icd.startswith('I65') or 
                icd.startswith('I66') or icd.startswith('I67') or icd.startswith('I68') or icd.startswith('I69') or # Stroke
                icd.startswith('E11') or # T2DM
                icd.startswith('N18')) # CKD
                
    df_history['is_target'] = df_history['ICD_CODE'].apply(is_target_disease)
    pre_existing_pids = df_history[df_history['is_target'] == True]['INDI_DSCM_NO'].unique().tolist()
    
    # 코호트에서 기왕력자 전면 배제
    cohort_pids = [pid for pid in valid_pids if pid not in pre_existing_pids]
    print(f"[-] Wash-out Period 적용: 기왕력자 {len(pre_existing_pids)}명 제외 -> 최종 코호트 인원: {len(cohort_pids):,}명")
    validate_unique_hashed_ids(
        pd.DataFrame({'ID': [hash_identifier(pid) for pid in cohort_pids]}),
        context="final cohort candidate hashes before record generation",
    )
    
    # 4. 각 대상자별 Baseline 변수 및 OLS 기울기 계산
    print("[~] 대상자별 비만 지표, 4개 비교군 할당 및 종단 OLS 기울기 연산 수행 중...")
    
    baseline_records = []
    
    # 2013-01-01부터 2023-12-31 사이의 follow-up 질병 이벤트 조회
    followup_query = """
    SELECT INDI_DSCM_NO, ICD_CODE, DIAG_DATE 
    FROM DISEASE_HISTORY 
    WHERE DIAG_DATE >= '2013-01-01' AND DIAG_DATE <= '2023-12-31'
    ORDER BY DIAG_DATE ASC
    """
    df_followup = pd.read_sql_query(followup_query, conn)
    
    index_date = datetime(2013, 1, 1)
    end_date = datetime(2023, 12, 31)
    max_days = (end_date - index_date).days  # 4,016일

    for pid in cohort_pids:
        # 개인별 검진 데이터 정렬
        p_df = df_filtered[df_filtered['INDI_DSCM_NO'] == pid].sort_values('STD_YYYY')
        
        # OLS 기울기 계산을 위한 x, y 수집
        years = p_df['STD_YYYY'].astype(int).tolist()
        
        # BMI 계산
        p_df['CALC_BMI'] = p_df['G1E_WGHT'] / ((p_df['G1E_HGHT'] / 100.0) ** 2)
        p_df['BMI'] = p_df['G1E_BMI'].fillna(p_df['CALC_BMI'])
        bmis = p_df['BMI'].tolist()
        
        # WHtR 계산
        p_df['WHTR'] = p_df['G1E_WSTC'] / p_df['G1E_HGHT']
        whtrs = p_df['WHTR'].tolist()
        wcs = p_df['G1E_WSTC'].tolist() # 허리둘레
        
        # OLS 기울기 연산
        bmi_slope = calculate_ols_slope(bmis, years)
        whtr_slope = calculate_ols_slope(whtrs, years)
        wc_slope = calculate_ols_slope(wcs, years)
        
        # Baseline은 2009~2012년 검진 중 가장 마지막 검진 기록으로 정의
        baseline_row = p_df.iloc[-1]
        
        base_year = baseline_row['STD_YYYY']
        base_age = int(baseline_row['AGED'])
        base_sex = baseline_row['SEX_TYPE']
        base_height = baseline_row['G1E_HGHT']
        base_weight = baseline_row['G1E_WGHT']
        base_bmi = baseline_row['BMI']
        base_waist = baseline_row['G1E_WSTC']
        base_whtr = baseline_row['WHTR']
        
        # 4개 노출 그룹 분류 (BMI 25, WHtR 0.5 기준)
        if base_bmi < 25.0 and base_whtr < 0.5:
            obese_group = 1 # 정상/정상
        elif base_bmi < 25.0 and base_whtr >= 0.5:
            obese_group = 2 # 정상/복부비만
        elif base_bmi >= 25.0 and base_whtr < 0.5:
            obese_group = 3 # 비만/정상
        else:
            obese_group = 4 # 비만/복부비만
            
        # 5. 각 질병별 Event 및 Survival Time 연산
        # 개인의 질병 기록 추출
        p_events = df_followup[df_followup['INDI_DSCM_NO'] == pid]
        
        def get_disease_event(p_events, prefixes):
            # 대상 접두사(예: I20, I21)에 매칭되는 최초 이벤트 날짜 추출
            matches = p_events[p_events['ICD_CODE'].apply(lambda x: any(str(x).upper().startswith(p) for p in prefixes))]
            if len(matches) > 0:
                event_dt_str = matches.iloc[0]['DIAG_DATE']
                event_dt = datetime.strptime(event_dt_str, '%Y-%m-%d')
                survival_days = (event_dt - index_date).days
                # 음수 방지 예외 처리
                survival_days = max(0, survival_days)
                return 1, min(survival_days, max_days)
            else:
                return 0, max_days

        # 질병별 계산
        event_cvd, time_cvd = get_disease_event(p_events, ['I20', 'I21', 'I22', 'I23', 'I24', 'I25'])
        event_stroke, time_stroke = get_disease_event(p_events, ['I60', 'I61', 'I62', 'I63', 'I64', 'I65', 'I66', 'I67', 'I68', 'I69'])
        event_t2dm, time_t2dm = get_disease_event(p_events, ['E11'])
        event_ckd, time_ckd = get_disease_event(p_events, ['N18'])
        
        # 복합 엔드포인트 (Any Event: CVD, Stroke, T2DM, CKD 중 가장 먼저 일어난 것)
        events_list = [event_cvd, event_stroke, event_t2dm, event_ckd]
        times_list = [time_cvd, time_stroke, time_t2dm, time_ckd]
        
        if sum(events_list) > 0:
            event_any = 1
            # 이벤트 발생일 중 최소값 선택
            time_any = min([t for e, t in zip(events_list, times_list) if e == 1])
        else:
            event_any = 0
            time_any = max_days

        baseline_records.append({
            'ID': hash_identifier(pid),
            'Sex': base_sex,
            'Age_Baseline': base_age,
            'Baseline_Year': base_year,
            'Height': base_height,
            'Weight': base_weight,
            'BMI_Baseline': round(base_bmi, 2),
            'Waist_Baseline': base_waist,
            'WHtR_Baseline': round(base_whtr, 4),
            'Obese_Group': obese_group,
            'BMI_Slope': round(bmi_slope, 4),
            'WHtR_Slope': round(whtr_slope, 5),
            'Waist_Slope': round(wc_slope, 4),
            'Income_Decile': int(baseline_row['INCOME_DECILE']),
            'Smoking_Status': int(baseline_row['SMOKING_STATUS']),
            'BP_Systolic': baseline_row['BP_SYS'],
            'BP_Diastolic': baseline_row['BP_DIA'],
            'Glucose': baseline_row['GLUCOSE'],
            'Cholesterol': baseline_row['CHOLESTEROL'],
            'eGFR': baseline_row['EGFR'],
            'Hypertension_Med': int(baseline_row['HYPERTENSION_MED']),
            'Dyslipidemia_Med': int(baseline_row['DYSLIPIDEMIA_MED']),
            'Event_CVD': event_cvd,
            'Time_CVD': time_cvd,
            'Event_Stroke': event_stroke,
            'Time_Stroke': time_stroke,
            'Event_T2DM': event_t2dm,
            'Time_T2DM': time_t2dm,
            'Event_CKD': event_ckd,
            'Time_CKD': time_ckd,
            'Event_Any': event_any,
            'Time_Any': time_any
        })

    df_cohort = pd.DataFrame(baseline_records)
    validate_unique_hashed_ids(df_cohort, context="full analytical cohort")

    # 6. 결과 디렉토리 생성 및 코호트 CSV 저장 (BOM 포함 UTF-8)
    output_dir = os.path.join(script_dir, "data")
    os.makedirs(output_dir, exist_ok=True)
    
    # A. 전체 분석 코호트 저장
    full_output_file = os.path.join(output_dir, "cohort_analytical.csv")
    df_cohort.to_csv(full_output_file, index=False, encoding='utf-8-sig')
    print(f"\n[+] 전체 분석 코호트 파일 저장 완료: {full_output_file} (N = {len(df_cohort)}명)")

    # B. 민감도 분석용 Lag-time(1년) 적용 코호트 저장
    # index date (2013-01-01) 이후 1년(365일) 이내에 실제 Any Event가 발생한 인원만 제거
    # 사망 등으로 1년 이내 검열된 비이벤트 대상자는 민감도 분석 코호트에 유지
    df_lag1y = apply_lag_time_filter(df_cohort)
    validate_unique_hashed_ids(df_lag1y, context="lag-time analytical cohort")
    lag_output_file = os.path.join(output_dir, "cohort_analytical_lag1y.csv")
    df_lag1y.to_csv(lag_output_file, index=False, encoding='utf-8-sig')
    print(f"[+] Lag-time 1년 필터링 민감도 코호트 파일 저장 완료: {lag_output_file} (N = {len(df_lag1y)}명, {len(df_cohort) - len(df_lag1y)}명 제외)")

    # 간단 분포 요약 출력
    print("\n--- 4대 Obesity Group별 최종 인원 분포 (Wash-out 후) ---")
    group_labels = {
        1: 'Group 1 (Normal/Normal)',
        2: 'Group 2 (Normal/Central Obese)',
        3: 'Group 3 (Obese/Normal)',
        4: 'Group 4 (Obese/Central Obese)'
    }
    counts = df_cohort['Obese_Group'].value_counts().sort_index()
    for g, count in counts.items():
        print(f"  * {group_labels[g]}: {count:,}명 ({count/len(df_cohort)*100:.1f}%)")

    conn.close()
    print("\n🎉 코호트 추출 및 전처리 파이프라인 가동 완료!")

if __name__ == "__main__":
    build_cohort()
