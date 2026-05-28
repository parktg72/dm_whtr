#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
국민건강보험 빅데이터 기반 BMI & WHtR 연구 (DM_WHTR)
DuckDB 기반 초고속 로컬 데이터 병합 및 코호트 빌딩 파이프라인

본 스크립트는 AGY 본부(DevOps/Orchestrator)와 Codex 본부(Logic Builder)의 협업으로 구현되었습니다.
- [DuckDB 하이브리드 아키텍처 도입]
  - 대용량 분할 CSV 파일들(Globs)을 DuckDB를 통해 디스크 스패닝 조회하여 병목 현상과 메모리(OOM) 방지.
  - DuckDB에서 자격 3회 이상 검진자 필터링 및 2013년 이전 기왕력자 제거(Wash-out) 연산 고속 처리.
  - 필터링된 초경량화 코호트 데이터를 Zero-Copy로 Pandas에 인계하여 기존 비만 매트릭스, OLS 선형 기울기 및 11개년 생존 기간 연산을 정밀 계산.
- [환경제약 반영] 윈도우 및 Python 3.12, 폐쇄망 환경을 네이티브 지원합니다.
"""

import os
import sys
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

# 폐쇄망 환경을 고려한 duckdb 임포트 예외 처리
try:
    import duckdb
except ImportError:
    print("\n" + "=" * 80)
    print("[!] 에러: DuckDB 라이브러리(duckdb)가 설치되어 있지 않습니다.")
    print("-" * 80)
    print("📢 [폐쇄망 환경 설치 가이드]")
    print("본 PC는 인터넷 연결이 제한된 폐쇄망 환경이므로 자동 다운로드가 불가합니다.")
    print("인터넷이 가능한 PC에서 아래 순서에 따라 설치 파일(Wheel)을 준비하여 설치하십시오:")
    print("\n1. 외부 인터넷 PC에서 필요한 패키지 다운로드:")
    print("   mkdir C:\\packages")
    print("   pip download -d C:\\packages duckdb")
    print("\n2. 다운로드된 'C:\\packages' 폴더를 USB 등의 저장 매체로 복사하여 본 폐쇄망 PC로 이동.")
    print("\n3. 본 폐쇄망 PC의 터미널(CMD/PowerShell)에서 오프라인 설치 실행:")
    print("   pip install --no-index --find-links=C:\\packages duckdb")
    print("=" * 80 + "\n")
    input("스크립트를 종료하려면 엔터를 누르십시오...")
    sys.exit(1)


def calculate_ols_slope(y_values, x_years):
    """
    각 개인의 검진 연도(x)와 지표 값(y)에 대해 최소제곱법(OLS) 선형 회귀 기울기(beta)를 구합니다.
    """
    if len(y_values) < 2:
        return 0.0
    valid_indices = ~np.isnan(y_values)
    x = np.array(x_years)[valid_indices]
    y = np.array(y_values)[valid_indices]
    
    if len(x) < 2:
        return 0.0
    slope, _ = np.polyfit(x, y, 1)
    return slope


DIAGNOSIS_PRIMARY_ALIASES = ("ICD_CODE", "MCEX_SICK_SYM", "MCEX_SICK_SYM1")
DIAGNOSIS_SECONDARY_ALIASES = tuple(f"MCEX_SICK_SYM{i}" for i in range(2, 8))
DIAGNOSIS_ALL_ALIASES = DIAGNOSIS_PRIMARY_ALIASES + DIAGNOSIS_SECONDARY_ALIASES


def _normalize_code_value(value):
    if value is None or pd.isna(value):
        return None
    value = str(value).strip().upper()
    return value or None


def _row_diagnosis_codes(row, columns):
    codes = []
    seen = set()
    for column in columns:
        code = _normalize_code_value(row.get(column))
        if code and code not in seen:
            seen.add(code)
            codes.append(code)
    return codes


def normalize_diagnosis_columns(df):
    df = df.copy()
    upper_to_column = {column.upper(): column for column in df.columns}
    diagnosis_columns = [upper_to_column[name] for name in DIAGNOSIS_ALL_ALIASES if name in upper_to_column]
    primary_columns = [upper_to_column[name] for name in DIAGNOSIS_PRIMARY_ALIASES if name in upper_to_column]

    if diagnosis_columns:
        df['ICD_CODE_ALL'] = df.apply(lambda row: _row_diagnosis_codes(row, diagnosis_columns), axis=1)

    if primary_columns:
        df['ICD_CODE'] = df.apply(
            lambda row: next(iter(_row_diagnosis_codes(row, primary_columns)), None),
            axis=1,
        )
    return df


def apply_lag_time_filter(df_cohort):
    return df_cohort[~((df_cohort['Event_Any'] == 1) & (df_cohort['Time_Any'] <= 365))].copy()


def _row_has_diagnosis_prefix(row, prefixes):
    code = _normalize_code_value(row.get('ICD_CODE'))
    if not code:
        return False
    return any(code.startswith(prefix) for prefix in prefixes)


def create_standardized_diagnosis_view(con, raw_dir):
    con.execute(f"CREATE VIEW v_diagnosis_raw AS SELECT * FROM read_csv_auto('{raw_dir}/diagnosis/diagnosis_*.csv')")
    column_rows = con.execute("DESCRIBE v_diagnosis_raw").fetchall()
    columns = [row[0] for row in column_rows]
    upper_to_column = {column.upper(): column for column in columns}

    primary_columns = [upper_to_column[name] for name in DIAGNOSIS_PRIMARY_ALIASES if name in upper_to_column]
    if not primary_columns:
        raise ValueError("diagnosis data must include ICD_CODE, MCEX_SICK_SYM, or MCEX_SICK_SYM1")
    if 'ICD_CODE' in upper_to_column:
        con.execute("CREATE VIEW v_diagnosis AS SELECT * FROM v_diagnosis_raw")
        return

    icd_expression = "COALESCE(" + ", ".join(
        f"NULLIF(TRIM(CAST({column} AS VARCHAR)), '')" for column in primary_columns
    ) + ")"

    con.execute(f"""
        CREATE VIEW v_diagnosis AS
        SELECT *, {icd_expression} AS ICD_CODE
        FROM v_diagnosis_raw
    """)


def build_cohort_duckdb():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    raw_dir = os.path.join(script_dir, "data", "raw")
    output_dir = os.path.join(script_dir, "data")
    
    # 0. 로컬 추출 원천 파일 존재 여부 검증
    death_file = os.path.join(raw_dir, "death", "death_all.csv")
    if not os.path.exists(death_file):
        print(f"[x] 에러: 로컬 추출 데이터가 존재하지 않습니다: {raw_dir}")
        print("    먼저 'extract_hana.py'를 실행하여 HANA DB에서 데이터를 다운로드하십시오.")
        sys.exit(1)

    print("\n" + "=" * 60)
    print("📊 DuckDB 로컬 고속 ETL 및 하이브리드 코호트 전처리 파이프라인 가동")
    print("=" * 60)

    # 1. DuckDB 인메모리 커넥션 생성 및 튜닝
    print("[~] DuckDB 임베디드 OLAP 엔진 로딩 및 최적화 설정 중...")
    con = duckdb.connect(database=':memory:')
    con.execute("SET max_memory = '8GB'")
    con.execute("SET threads = 4")

    # 2. 로컬 디렉터리 분할 CSV들의 Glob 뷰(View) 정의
    print("  - 로컬 계층형 CSV 와일드카드 뷰 정의...")
    con.execute(f"CREATE VIEW v_death AS SELECT * FROM read_csv_auto('{raw_dir}/death/death_all.csv')")
    con.execute(f"CREATE VIEW v_elig_checkup AS SELECT * FROM read_csv_auto('{raw_dir}/eligibility_checkup/elig_checkup_*.csv')")
    create_standardized_diagnosis_view(con, raw_dir)
    con.execute(f"CREATE VIEW v_billing AS SELECT * FROM read_csv_auto('{raw_dir}/billing/billing_*.csv')")

    # 3. DuckDB SQL 기반 코호트 대상자 및 기왕력자(Wash-out) 필터링
    print("[~] DuckDB 병렬 벡터화 쿼리를 이용해 코호트 대상자 필터링 구동 중...")
    
    # A. 2009~2012년 내 최소 3회 이상 검진받은 대상자 PIDs 추출
    con.execute("""
        CREATE TABLE t_valid_pids AS 
        SELECT INDI_DSCM_NO 
        FROM v_elig_checkup
        WHERE STD_YYYY BETWEEN '2009' AND '2012'
        GROUP BY INDI_DSCM_NO
        HAVING COUNT(STD_YYYY) >= 3
    """)
    total_valid = con.execute("SELECT COUNT(*) FROM t_valid_pids").fetchone()[0]
    print(f"  [+] 1차 필터링: 4개년(2009-2012) 중 3회 이상 수검자 고유 인원 = {total_valid:,}명")

    # B. 2013-01-01 기준 Wash-out (기왕력자) PIDs 식별
    # 날짜 포맷 강건화 (YYYY-MM-DD 또는 YYYYMMDD에 대응하기 위해 대시 제거 후 비교)
    con.execute("""
        CREATE TABLE t_washout_pids AS
        SELECT DISTINCT INDI_DSCM_NO
        FROM v_diagnosis
        WHERE REPLACE(CAST(MDCARE_STRT_DT AS VARCHAR), '-', '') < '20130101'
          AND (
              ICD_CODE LIKE 'I20%' OR ICD_CODE LIKE 'I21%' OR ICD_CODE LIKE 'I22%' OR
              ICD_CODE LIKE 'I23%' OR ICD_CODE LIKE 'I24%' OR ICD_CODE LIKE 'I25%' OR -- CVD
              ICD_CODE LIKE 'I6%' OR -- Stroke
              ICD_CODE LIKE 'E11%' OR -- T2DM
              ICD_CODE LIKE 'N18%'     -- CKD
          )
    """)
    total_washout = con.execute("SELECT COUNT(*) FROM t_washout_pids").fetchone()[0]
    print(f"  [-] 2차 필터링: Baseline 이전 기왕력(CVD, Stroke, 당뇨, CKD) 보유자 = {total_washout:,}명")

    # C. 최종 코호트 확정 (3회 수검자 중 기왕력자 전면 배제)
    con.execute("""
        CREATE TABLE t_final_cohort_pids AS
        SELECT INDI_DSCM_NO 
        FROM t_valid_pids
        WHERE INDI_DSCM_NO NOT IN (SELECT INDI_DSCM_NO FROM t_washout_pids)
    """)
    total_final = con.execute("SELECT COUNT(*) FROM t_final_cohort_pids").fetchone()[0]
    print(f"  [=] 3차 필터링: 최종 분석 대상 코호트 인원 확정 = {total_final:,}명")
    cohort_pid_rows = con.execute("SELECT INDI_DSCM_NO FROM t_final_cohort_pids").fetchall()
    validate_unique_hashed_ids(
        pd.DataFrame({'ID': [hash_identifier(row[0]) for row in cohort_pid_rows]}),
        context="final DuckDB cohort candidate hashes before record generation",
    )

    if total_final == 0:
        print("[x] 필터링된 대상자가 없습니다. 파이프라인을 종료합니다.")
        con.close()
        return

    # 4. Zero-Copy를 활용해 최종 대상자의 데이터만 Pandas로 고속 인계
    print("\n[~] DuckDB 결과물 Zero-Copy 메모리 공유로 Pandas 인계 중...")
    
    # 최종 코호트 대상자의 건강검진 전체 이력 적재
    df_filtered = con.execute("""
        SELECT * FROM v_elig_checkup 
        WHERE INDI_DSCM_NO IN (SELECT INDI_DSCM_NO FROM t_final_cohort_pids)
    """).df()
    
    # 최종 코호트 대상자의 2013년 이후 추적 질병 진단 기록 적재
    df_followup = con.execute("""
        SELECT * FROM v_diagnosis 
        WHERE INDI_DSCM_NO IN (SELECT INDI_DSCM_NO FROM t_final_cohort_pids)
          AND REPLACE(CAST(MDCARE_STRT_DT AS VARCHAR), '-', '') >= '20130101'
          AND REPLACE(CAST(MDCARE_STRT_DT AS VARCHAR), '-', '') <= '20231231'
        ORDER BY MDCARE_STRT_DT ASC
    """).df()
    df_followup = normalize_diagnosis_columns(df_followup)

    # 최종 코호트 대상자의 일반명세서 기록 적재 (CVD/Stroke 입원 여부 및 기간 정밀 필터링용)
    df_billing = con.execute("""
        SELECT * FROM v_billing
        WHERE INDI_DSCM_NO IN (SELECT INDI_DSCM_NO FROM t_final_cohort_pids)
          AND REPLACE(CAST(MDCARE_STRT_DT AS VARCHAR), '-', '') >= '20130101'
          AND REPLACE(CAST(MDCARE_STRT_DT AS VARCHAR), '-', '') <= '20231231'
    """).df()
    
    # 사망 기록 적재
    df_death = con.execute("""
        SELECT * FROM v_death
        WHERE INDI_DSCM_NO IN (SELECT INDI_DSCM_NO FROM t_final_cohort_pids)
    """).df()

    con.close()
    print("[+] DuckDB 디스크 메모리 오프로드 성공 및 로컬 전처리 가동 시작!")

    # 5. 각 대상자별 Baseline 변수 및 OLS 기울기, Survival Metrics 정밀 계산
    cohort_pids = df_filtered['INDI_DSCM_NO'].unique().tolist()
    index_date = datetime(2013, 1, 1)
    end_date = datetime(2023, 12, 31)
    max_days = (end_date - index_date).days
    
    baseline_records = []
    
    # 빠른 조회를 위해 사망 기록 딕셔너리화
    death_dict = dict(zip(df_death['INDI_DSCM_NO'], df_death['DTH_ASSMD_DT']))

    for pid in cohort_pids:
        # 개인별 검진 데이터 정렬
        p_df = df_filtered[df_filtered['INDI_DSCM_NO'] == pid].sort_values('STD_YYYY')
        years = p_df['STD_YYYY'].astype(int).tolist()
        
        # 키/몸무게 기반 BMI 보정
        p_df['CALC_BMI'] = p_df['G1E_WGHT'] / ((p_df['G1E_HGHT'] / 100.0) ** 2)
        p_df['BMI'] = p_df['G1E_BMI'].fillna(p_df['CALC_BMI'])
        bmis = p_df['BMI'].tolist()
        
        # WHtR 및 허리둘레
        p_df['WHTR'] = p_df['G1E_WSTC'] / p_df['G1E_HGHT']
        whtrs = p_df['WHTR'].tolist()
        wcs = p_df['G1E_WSTC'].tolist()
        
        # OLS 회귀 기울기 계산
        bmi_slope = calculate_ols_slope(bmis, years)
        whtr_slope = calculate_ols_slope(whtrs, years)
        wc_slope = calculate_ols_slope(wcs, years)
        
        # Baseline (가장 최근 수검 데이터)
        baseline_row = p_df.iloc[-1]
        
        base_year = baseline_row['STD_YYYY']
        base_age = int(baseline_row['AGED'])
        base_sex = baseline_row['SEX_TYPE']
        base_height = baseline_row['G1E_HGHT']
        base_weight = baseline_row['G1E_WGHT']
        base_bmi = baseline_row['BMI']
        base_waist = baseline_row['G1E_WSTC']
        base_whtr = baseline_row['WHTR']
        
        # 소득분위 결측 방어
        income_decile = int(baseline_row['INCOME_DECILE']) if 'INCOME_DECILE' in baseline_row else 5
        smoking_status = int(baseline_row['SMOKING_STATUS']) if 'SMOKING_STATUS' in baseline_row else 1
        
        # 4개 Obesity Group 할당
        if base_bmi < 25.0 and base_whtr < 0.5:
            obese_group = 1
        elif base_bmi < 25.0 and base_whtr >= 0.5:
            obese_group = 2
        elif base_bmi >= 25.0 and base_whtr < 0.5:
            obese_group = 3
        else:
            obese_group = 4
            
        # 2013년 이후 질병 내역
        p_events = df_followup[df_followup['INDI_DSCM_NO'] == pid]
        p_bill = df_billing[df_billing['INDI_DSCM_NO'] == pid]
        
        def get_disease_event_with_inpatient(p_events, p_bill, prefixes, require_inpatient=False):
            # 대상 상병 조회
            matches = p_events[p_events.apply(lambda row: _row_has_diagnosis_prefix(row, prefixes), axis=1)]
            if len(matches) == 0:
                return 0, max_days
                
            for _, row in matches.iterrows():
                event_dt_str = str(row['MDCARE_STRT_DT']).replace('-', '')
                event_dt = datetime.strptime(event_dt_str, '%Y%m%d')
                
                # 심뇌혈관 질환에 입원(MCARE_TP='1') 및 입내원일수(>=2일) 연동 검증 필터 적용
                if require_inpatient:
                    # 요양개시일이 동일한 일반 명세서 연동
                    matching_bill = p_bill[p_bill['MDCARE_STRT_DT'].astype(str).str.replace('-', '') == event_dt_str]
                    inpatient_valid = False
                    for _, b_row in matching_bill.iterrows():
                        mcare_tp = str(b_row['MCARE_TP']).strip()
                        days = float(b_row['HSPTZ_VSHSP_DD_CNT']) if pd.notna(b_row['HSPTZ_VSHSP_DD_CNT']) else 0
                        if mcare_tp == '1' and days >= 2.0:
                            inpatient_valid = True
                            break
                    if not inpatient_valid:
                        continue # 입원 조건을 불충족하므로 다음 상병기록 검색
                
                survival_days = (event_dt - index_date).days
                return 1, min(max(0, survival_days), max_days)
                
            return 0, max_days

        # 질병별 이벤트 및 생존일 연산 (CVD & Stroke는 입원 2일 이상 조건 적용)
        event_cvd, time_cvd = get_disease_event_with_inpatient(p_events, p_bill, ['I20', 'I21', 'I22', 'I23', 'I24', 'I25'], require_inpatient=True)
        event_stroke, time_stroke = get_disease_event_with_inpatient(p_events, p_bill, ['I60', 'I61', 'I62', 'I63', 'I64', 'I65', 'I66', 'I67', 'I68', 'I69'], require_inpatient=True)
        event_t2dm, time_t2dm = get_disease_event_with_inpatient(p_events, p_bill, ['E11'], require_inpatient=False)
        event_ckd, time_ckd = get_disease_event_with_inpatient(p_events, p_bill, ['N18'], require_inpatient=False)
        
        # 사망 사건 연동에 따른 중도 절단(Censoring)
        if pid in death_dict:
            death_dt_str = str(death_dict[pid]).replace('-', '')
            death_dt = datetime.strptime(death_dt_str, '%Y%m%d')
            death_days = (death_dt - index_date).days
            death_days = min(max(0, death_days), max_days)
            
            # 각 질병 발생 시점이 사망 시점보다 늦으면 이벤트를 무효화하고 사망일을 절단 시점으로 설정
            if event_cvd == 1 and time_cvd > death_days:
                event_cvd, time_cvd = 0, death_days
            elif event_cvd == 0:
                time_cvd = min(time_cvd, death_days)
                
            if event_stroke == 1 and time_stroke > death_days:
                event_stroke, time_stroke = 0, death_days
            elif event_stroke == 0:
                time_stroke = min(time_stroke, death_days)
                
            if event_t2dm == 1 and time_t2dm > death_days:
                event_t2dm, time_t2dm = 0, death_days
            elif event_t2dm == 0:
                time_t2dm = min(time_t2dm, death_days)
                
            if event_ckd == 1 and time_ckd > death_days:
                event_ckd, time_ckd = 0, death_days
            elif event_ckd == 0:
                time_ckd = min(time_ckd, death_days)
        
        # 복합 아웃컴 연산
        events_list = [event_cvd, event_stroke, event_t2dm, event_ckd]
        times_list = [time_cvd, time_stroke, time_t2dm, time_ckd]
        
        if sum(events_list) > 0:
            event_any = 1
            time_any = min([t for e, t in zip(events_list, times_list) if e == 1])
        else:
            event_any = 0
            if pid in death_dict:
                death_dt_str = str(death_dict[pid]).replace('-', '')
                death_dt = datetime.strptime(death_dt_str, '%Y%m%d')
                time_any = min(max(0, (death_dt - index_date).days), max_days)
            else:
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
            'Income_Decile': income_decile,
            'Smoking_Status': smoking_status,
            'BP_Systolic': baseline_row['G1E_BP_SYS'] if 'G1E_BP_SYS' in baseline_row else baseline_row.get('BP_SYS', 120),
            'BP_Diastolic': baseline_row['G1E_BP_DIA'] if 'G1E_BP_DIA' in baseline_row else baseline_row.get('BP_DIA', 80),
            'Glucose': baseline_row['G1E_FBS'] if 'G1E_FBS' in baseline_row else baseline_row.get('GLUCOSE', 100),
            'Cholesterol': baseline_row['G1E_TOT_CHOL'] if 'G1E_TOT_CHOL' in baseline_row else baseline_row.get('CHOLESTEROL', 200),
            'eGFR': baseline_row['G1E_GFR'] if 'G1E_GFR' in baseline_row else baseline_row.get('EGFR', 90),
            'Hypertension_Med': int(baseline_row['HYPERTENSION_MED']) if 'HYPERTENSION_MED' in baseline_row else 0,
            'Dyslipidemia_Med': int(baseline_row['DYSLIPIDEMIA_MED']) if 'DYSLIPIDEMIA_MED' in baseline_row else 0,
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
    validate_unique_hashed_ids(df_cohort, context="full DuckDB analytical cohort")

    # 6. 최종 분석 코호트 파일 로컬 저장 (Windows Excel CP949 디코딩 대응)
    full_output_file = os.path.join(output_dir, "cohort_analytical.csv")
    df_cohort.to_csv(full_output_file, index=False, encoding='utf-8-sig')
    print(f"\n[+] DuckDB 병합 전체 분석 코호트 파일 저장 완료: {full_output_file} (N = {len(df_cohort)}명)")

    # B. 민감도 분석용 Lag-time(1년) 코호트 저장
    df_lag1y = apply_lag_time_filter(df_cohort)
    validate_unique_hashed_ids(df_lag1y, context="lag-time DuckDB analytical cohort")
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

    print("\n🎉 DuckDB 기반 전처리 및 분석 코호트 빌딩 완료!")


if __name__ == "__main__":
    build_cohort_duckdb()
