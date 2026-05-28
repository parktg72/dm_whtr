#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
국민건강보험 빅데이터 기반 BMI & WHtR 연구 (DM_WHTR)
Phase 1: 가상 NHIS 데이터셋 생성기 (SQLite 기반)

본 스크립트는 Codex 본부(TDD Agent)와 AGY 본부(DevOps)의 협업으로 구현되었습니다.
- 실제 건보공단 코호트 스키마(H:\\lay_out 명세)를 기반으로 1,000명의 가상 인구 데이터를 모델링합니다.
- 나이 제한(20~45세), 검진 횟수 결측, 상병 기왕력(Wash-out), Lag-time 검증용 데이터를 의도적으로 포함합니다.
- Windows 환경 및 Python 3.12를 지원하며, 윈도우 인코딩 오류가 발생하지 않도록 조치되었습니다.
"""

import os
import sys
import sqlite3
import random
from datetime import datetime, timedelta

# Windows CMD 한글 인코딩 방어
if sys.platform.startswith('win'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except AttributeError:
        pass


def enforce_min_weight(weight, minimum=30.0):
    """Clamp synthetic adult checkup weight to the final cohort QA hard lower bound."""
    if weight is None:
        return None
    return round(max(float(weight), minimum), 1)


def enforce_bp_order(bp_sys, bp_dia):
    """Ensure synthetic systolic blood pressure is not lower than diastolic pressure."""
    if bp_sys is None or bp_dia is None:
        return bp_sys, bp_dia
    bp_sys = float(bp_sys)
    bp_dia = float(bp_dia)
    if bp_sys < bp_dia:
        bp_sys = bp_dia
    return round(bp_sys, 1), round(bp_dia, 1)

def generate_data():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    db_path = os.path.join(script_dir, "synthetic_nhis.db")
    
    # 기존 db 존재 시 삭제 후 신규 생성
    if os.path.exists(db_path):
        try:
            os.remove(db_path)
        except Exception as e:
            print(f"[!] 기존 DB 파일 삭제 중 에러 발생 (프로세스 점유 중일 수 있음): {e}")

    print(f"[~] 가상 SQLite DB 생성 중: {db_path}")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # 1. 테이블 생성
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
    conn.commit()

    # 2. 1,000명의 가상 인물 생성 및 주입
    print("[~] 1,000명의 가상 인물에 대한 데이터 모델링 및 주입 시작...")
    
    eligibility_data = []
    checkup_data = []
    disease_data = []

    # 2013년 1월 1일 기준 나이 계산용 년도 설정
    # 2009~2012년 기준 건강검진 수행
    years = ['2009', '2010', '2011', '2012']

    # 재현성을 위해 시드 고정
    random.seed(42)

    for pid in range(1, 1001):
        # A. 기본 인적 사항 생성 (나이 및 성별 분포 다양화)
        # 80%는 20~45세 연령대 (출생년도 1968 ~ 1993)
        # 10%는 20세 미만 (출생년도 1994 ~ 2000)
        # 10%는 45세 초과 (출생년도 1950 ~ 1967)
        rand_age_group = random.random()
        if rand_age_group < 0.80:
            byear = str(random.randint(1968, 1992))  # 2012년 기준 20~44세
        elif rand_age_group < 0.90:
            byear = str(random.randint(1994, 2005))  # 2012년 기준 20세 미만 (제외 대상)
        else:
            byear = str(random.randint(1945, 1965))  # 2012년 기준 47세 이상 (제외 대상)

        sex = random.choice(['1', '2']) # 1: 남성, 2: 여성

        # B. 검진 빈도 조건 다양화 (제외 대상 유도)
        # 85%는 3~4회 검진 수행 (Inclusion 대상)
        # 15%는 1~2회 검진만 수행 (Exclusion 대상)
        rand_checkup_freq = random.random()
        if rand_checkup_freq < 0.85:
            # 3회 또는 4회 검진
            exam_years = random.choice([['2009', '2010', '2011', '2012'], 
                                        ['2009', '2011', '2012'], 
                                        ['2009', '2010', '2012']])
        else:
            # 1회 또는 2회 검진 (정상 제외 대상)
            exam_years = random.choice([['2009'], ['2012'], ['2009', '2010']])

        # 신장(cm) 고정 (키는 변하지 않는다고 가정하거나 미세 변화)
        height = round(random.normalvariate(173.0, 6.0) if sex == '1' else random.normalvariate(160.0, 5.5), 1)

        # Baseline 체중 및 허리둘레 설정
        base_weight = round(random.normalvariate(72.0, 12.0) if sex == '1' else random.normalvariate(56.0, 9.0), 1)
        base_waist = round(random.normalvariate(84.0, 8.0) if sex == '1' else random.normalvariate(76.0, 7.5), 1)

        # 변화 트렌드 설정 (체중 증가군, 감소군, 유지군 등)
        weight_slope = random.choice([-1.5, -0.5, 0.0, 0.5, 1.5, 2.5])
        waist_slope = random.choice([-1.0, -0.3, 0.0, 0.3, 1.0, 1.8])

        # C. 각 검진 연도별 데이터 주입
        for idx, yr in enumerate(exam_years):
            # 자격 정보 추가
            eligibility_data.append((pid, yr, sex, byear))

            # 키에 대한 미세 오차(검진 시마다 미세하게 다르게 측정됨을 시뮬레이션)
            yr_height = round(height + random.uniform(-0.5, 0.5), 1)
            
            # 기울기에 따른 체중/허리둘레 계산 (연차가 누적될 수록 변화)
            yr_diff = int(yr) - 2009
            yr_weight = enforce_min_weight(round(base_weight + (weight_slope * yr_diff) + random.uniform(-0.8, 0.8), 1))
            yr_waist = round(base_waist + (waist_slope * yr_diff) + random.uniform(-0.6, 0.6), 1)
            
            # BMI 공식 적용 계산
            yr_bmi = round(yr_weight / ((yr_height / 100.0) ** 2), 2)

            # 5% 확률로 특정 행의 키/몸무게/허리둘레 결측치 발생 (결측 제외용)
            if pid % 20 == 0 and idx == len(exam_years) - 1:
                # 마지막 검진 기록에 결측치 주입
                yr_height = None
                yr_weight = None
                yr_bmi = None
                yr_waist = None

            # 임상 지표 생성
            income = random.randint(1, 10)
            smoking = random.choice([1, 2, 3])  # 1: 비흡연, 2: 과거, 3: 현재
            bp_sys = round(random.normalvariate(122.0, 13.0), 1)
            bp_dia = round(random.normalvariate(78.0, 9.0), 1)
            bp_sys, bp_dia = enforce_bp_order(bp_sys, bp_dia)
            glucose = round(random.normalvariate(95.0, 15.0), 1)
            cholesterol = round(random.normalvariate(198.0, 35.0), 1)
            egfr = round(random.normalvariate(88.0, 12.0), 1)
            htn_med = 1 if bp_sys >= 140 or bp_dia >= 90 or random.random() < 0.05 else 0
            dys_med = 1 if cholesterol >= 240 or random.random() < 0.08 else 0

            checkup_data.append((
                pid, yr, yr_height, yr_weight, yr_bmi, yr_waist,
                income, smoking, bp_sys, bp_dia, glucose, cholesterol, egfr, htn_med, dys_med
            ))

        # D. 상병 및 질병 히스토리 데이터 생성
        # 1) Wash-out 대상자 설정 (10% 확률로 2013년 1월 1일 이전에 질병 기왕력 보유)
        has_baseline_disease = random.random() < 0.10
        if has_baseline_disease:
            diag_year = random.randint(2009, 2012)
            diag_month = random.randint(1, 12)
            diag_day = random.randint(1, 28)
            diag_date = f"{diag_year}-{diag_month:02d}-{diag_day:02d}"
            icd_code = random.choice(['I20.9', 'I63.9', 'E11.9', 'N18.9'])
            disease_data.append((pid, icd_code, diag_date))

        # 2) Follow-up 기간 (2013-01-01 ~ 2023-12-31) 중 이벤트 발생자 모델링
        # baseline 비만 그룹 4에 가깝거나 체중/허리둘레 기울기가 클수록 이벤트 확률 증가
        obesity_risk = 0.03 # 기본 이벤트 확률 (3%)
        if weight_slope > 1.0:
            obesity_risk += 0.07
        if waist_slope > 0.8:
            obesity_risk += 0.08
        if base_weight / ((height/100.0)**2) >= 25.0:
            obesity_risk += 0.06
        if base_waist / height >= 0.5:
            obesity_risk += 0.07

        # 2013년 이후 질병 발생 시뮬레이션
        if not has_baseline_disease and random.random() < obesity_risk:
            # 2013-01-01부터 2023-12-31 사이의 랜덤한 날짜 생성
            start_date = datetime(2013, 1, 1)
            days_to_add = random.randint(1, 4015)  # 약 11개년
            event_date = start_date + timedelta(days=days_to_add)
            event_date_str = event_date.strftime('%Y-%m-%d')
            
            # Lag-time 제외용 시뮬레이션 (2013년 내에 최초 이벤트 발생자는 Lag-time 필터 시 제외됨)
            icd_code = random.choice(['I21.9', 'I64', 'E11.9', 'N18.3'])
            disease_data.append((pid, icd_code, event_date_str))

    # 데이터 DB 일괄 삽입
    cursor.executemany("INSERT INTO ELIGIBILITY VALUES (?, ?, ?, ?)", eligibility_data)
    cursor.executemany("""
    INSERT INTO CHECKUP VALUES (
        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
    )
    """, checkup_data)
    cursor.executemany("INSERT INTO DISEASE_HISTORY VALUES (?, ?, ?)", disease_data)

    conn.commit()
    
    # 3. 삽입 통계 확인
    cursor.execute("SELECT COUNT(DISTINCT INDI_DSCM_NO) FROM ELIGIBILITY")
    unique_elg = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM ELIGIBILITY")
    total_elg = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM CHECKUP")
    total_chk = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM DISEASE_HISTORY")
    total_dis = cursor.fetchone()[0]

    print("\n" + "=" * 50)
    print("📊 가상 NHIS SQLite DB 생성 결과 보고")
    print("=" * 50)
    print(f"- 고유 대상자 수 (N): {unique_elg:,}명")
    print(f"- ELIGIBILITY 총 레코드 수: {total_elg:,}개")
    print(f"- CHECKUP 총 레코드 수: {total_chk:,}개")
    print(f"- DISEASE_HISTORY 총 레코드 수: {total_dis:,}개")
    print("=" * 50)

    conn.close()
    print("[+] DB 주입이 정상 완료되었습니다.")

if __name__ == "__main__":
    generate_data()
