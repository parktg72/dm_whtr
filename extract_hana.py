#!/usr/bin/env python3
# -*- coding: utf-8 -*-

r"""
국민건강보험 빅데이터 기반 BMI & WHtR 연구 (DM_WHTR)
SAP HANA DB 다중 시간분할 고성능 로컬 추출 스크립트

본 스크립트는 AGY 본부(DevOps)와 Codex 본부(Logic Builder)의 협업으로 리팩토링되었습니다.
- [HANA DB 스키마 분할 배정 적용]
  - 사망, 자격, 검진: NHISBDA 스키마
  - 상병, 일반명세: NHISBASE 스키마
- [추출 시간분할 아키텍처 적용]
  - 사망 데이터: 연구 전체 기간 1회 일시 추출 -> data/raw/death/
  - 자격 & 검진 데이터: 연(Year) 단위 분할 추출 -> data/raw/eligibility_checkup/
  - 상병 & 일반명세 데이터: 월(Month) 단위 분할 추출 -> data/raw/diagnosis/ 및 data/raw/billing/
- [환경제약 반영] 윈도우(Windows) Python 3.12 및 오프라인(폐쇄망) 환경을 완벽히 지원합니다.
"""

import os
import sys
import getpass
import pandas as pd
import numpy as np

# 1. Windows CMD/PowerShell 한글 인코딩 깨짐 및 오류 방지 (CP949 -> UTF-8 강제 설정)
if sys.platform.startswith('win'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except AttributeError:
        pass

# 2. 폐쇄망 환경을 고려한 hdbcli 임포트 예외 처리
try:
    from hdbcli import dbapi
except ImportError:
    print("\n" + "=" * 80)
    print("[!] 에러: SAP HANA DB 연결 라이브러리(hdbcli)가 설치되어 있지 않습니다.")
    print("-" * 80)
    print("📢 [폐쇄망 환경 설치 가이드]")
    print("본 PC는 인터넷 연결이 제한된 폐쇄망 환경이므로 자동 다운로드가 불가합니다.")
    print("인터넷이 가능한 PC에서 아래 순서에 따라 설치 파일(Wheel)을 준비하여 설치하십시오:")
    print("\n1. 외부 인터넷 PC에서 필요한 패키지 다운로드:")
    print("   mkdir C:\\packages")
    print("   pip download -d C:\\packages hdbcli pandas numpy")
    print("\n2. 다운로드된 'C:\\packages' 폴더를 USB 등의 저장 매체로 복사하여 본 폐쇄망 PC로 이동.")
    print("\n3. 본 폐쇄망 PC의 터미널(CMD/PowerShell)에서 오프라인 설치 실행:")
    print("   pip install --no-index --find-links=C:\\packages hdbcli")
    print("=" * 80 + "\n")
    input("스크립트를 종료하려면 엔터를 누르십시오...")
    sys.exit(1)


def get_lifestyle_era(year):
    if year <= 2017:
        return "pre2018"
    if year == 2018:
        return "2018"
    return "post2019"


def _numeric(series):
    return pd.to_numeric(series, errors="coerce")


def harmonize_lifestyle(df, era):
    """검진 문진 원천 컬럼을 분석 표준 생활습관 컬럼으로 정규화합니다."""
    out = df.copy()

    if era in ("pre2018", "2018"):
        if "SMK_CURR" in out:
            smk_curr = _numeric(out["SMK_CURR"]).fillna(0).eq(1)
        else:
            smk_curr = _numeric(out.get("Q_SMK_YN", pd.Series(index=out.index, dtype="float"))).eq(3)

        if "DRK_LEVEL" in out:
            drk_level = _numeric(out["DRK_LEVEL"]).fillna(0)
        else:
            drk_freq = _numeric(out.get("Q_DRK_FRQ_V0108", pd.Series(index=out.index, dtype="float")))
            drk_level = np.select([drk_freq >= 4, drk_freq >= 2], [2, 1], default=0)

        if "PA_ACTIVE" in out:
            pa_active = _numeric(out["PA_ACTIVE"]).fillna(0).eq(1)
        else:
            pa_active = _numeric(out.get("Q_PA_FRQ", pd.Series(index=out.index, dtype="float"))).ge(3)
    elif era == "post2019":
        if "SMK_CURR" in out:
            smk_curr = _numeric(out["SMK_CURR"]).fillna(0).eq(1)
        else:
            smk_now = _numeric(out.get("Q_SMK_NOW_YN", pd.Series(index=out.index, dtype="float")))
            smk_curr = smk_now.eq(1)

        if "DRK_LEVEL" in out:
            drk_level = _numeric(out["DRK_LEVEL"]).fillna(0)
        else:
            drk_per = _numeric(out.get("Q_DRK_PER", pd.Series(index=out.index, dtype="float")))
            drk_freq = _numeric(out.get("Q_DRK_FRQ", pd.Series(index=out.index, dtype="float")))
            drk_level = np.select(
                [drk_per.fillna(0).le(0), drk_freq >= 4, drk_freq >= 2, drk_per >= 1],
                [0, 2, 1, 1],
                default=0,
            )

        if "PA_ACTIVE" in out:
            pa_active = _numeric(out["PA_ACTIVE"]).fillna(0).eq(1)
        else:
            pa_vd = _numeric(out.get("Q_PA_VD_FRQ", pd.Series(index=out.index, dtype="float")))
            pa_md = _numeric(out.get("Q_PA_MD_FRQ", pd.Series(index=out.index, dtype="float")))
            pa_musl = _numeric(out.get("Q_PA_MUSL_FRQ", pd.Series(index=out.index, dtype="float")))
            pa_active = pa_vd.ge(3) | pa_md.ge(5) | pa_musl.ge(2)
    else:
        raise ValueError(f"unknown lifestyle era: {era}")

    out["SMK_CURR"] = pd.Series(smk_curr, index=out.index).fillna(False).astype(int)
    out["DRK_LEVEL"] = pd.Series(drk_level, index=out.index).fillna(0).clip(0, 2).astype(int)
    out["PA_ACTIVE"] = pd.Series(pa_active, index=out.index).fillna(False).astype(int)

    if {"BZ_YYYY", "STD_YYYY"}.issubset(out.columns):
        mismatch = out["BZ_YYYY"].astype(str) != out["STD_YYYY"].astype(str)
        if mismatch.any():
            raise ValueError("BZ_YYYY must match STD_YYYY for eligibility/checkup extraction")

    return out


def get_eligibility_checkup_query(year):
    if year <= 2017:
        return f"""
            SELECT 
                P.INDI_DSCM_NO,
                P.STD_YYYY,
                P.SEX_TYPE,
                P.BYEAR,
                (CAST(P.STD_YYYY AS INTEGER) - CAST(P.BYEAR AS INTEGER)) AS AGED,
                E.EXMD_BZ_YYYY AS BZ_YYYY,
                E.G1E_HGHT,
                E.G1E_WGHT,
                E.G1E_BMI,
                E.G1E_WSTC,
                E.G1E_BP_SYS,
                E.G1E_BP_DIA,
                E.G1E_FBS,
                E.G1E_TOT_CHOL,
                E.G1E_GFR,
                CASE WHEN CAST(E.Q_SMK_YN AS VARCHAR) = '3' THEN 1 ELSE 0 END AS SMK_CURR,
                CASE
                    WHEN CAST(E.Q_DRK_FRQ_V0108 AS INTEGER) >= 4 THEN 2
                    WHEN CAST(E.Q_DRK_FRQ_V0108 AS INTEGER) >= 2 THEN 1
                    ELSE 0
                END AS DRK_LEVEL,
                CASE WHEN CAST(E.Q_PA_FRQ AS INTEGER) >= 3 THEN 1 ELSE 0 END AS PA_ACTIVE
            FROM NHISBDA.HHDV_DSES_YY P
            INNER JOIN NHISBDA.HMDT_G1EQ_RST E
                ON P.INDI_DSCM_NO = E.INDI_DSCM_NO
                AND P.STD_YYYY = E.EXMD_BZ_YYYY
            WHERE (CAST(P.STD_YYYY AS INTEGER) - CAST(P.BYEAR AS INTEGER)) BETWEEN 20 AND 45
              AND P.STD_YYYY = '{year}'
              AND E.G1E_HGHT IS NOT NULL AND E.G1E_HGHT > 0
              AND E.G1E_WSTC IS NOT NULL AND E.G1E_WSTC > 0
            """

    if year == 2018:
        return """
            SELECT 
                P.INDI_DSCM_NO,
                P.STD_YYYY,
                P.SEX_TYPE,
                P.BYEAR,
                (CAST(P.STD_YYYY AS INTEGER) - CAST(P.BYEAR AS INTEGER)) AS AGED,
                E.EXMD_BZ_YYYY AS BZ_YYYY,
                E.G1E_HGHT,
                E.G1E_WGHT,
                E.G1E_BMI,
                E.G1E_WSTC,
                E.G1E_BP_SYS,
                E.G1E_BP_DIA,
                E.G1E_FBS,
                E.G1E_TOT_CHOL,
                E.G1E_GFR,
                CASE WHEN CAST(Q.Q_SMK_YN AS VARCHAR) = '3' THEN 1 ELSE 0 END AS SMK_CURR,
                CASE
                    WHEN CAST(Q.Q_DRK_FRQ_V0108 AS INTEGER) >= 4 THEN 2
                    WHEN CAST(Q.Q_DRK_FRQ_V0108 AS INTEGER) >= 2 THEN 1
                    ELSE 0
                END AS DRK_LEVEL,
                CASE WHEN CAST(Q.Q_PA_FRQ AS INTEGER) >= 3 THEN 1 ELSE 0 END AS PA_ACTIVE
            FROM NHISBDA.HHDV_DSES_YY P
            INNER JOIN NHISBDA.HMDT_G1E_RST_2018 E
                ON P.INDI_DSCM_NO = E.INDI_DSCM_NO
                AND P.STD_YYYY = E.EXMD_BZ_YYYY
            INNER JOIN NHISBDA.HMDT_GQ_RST_2018 Q
                ON E.INDI_DSCM_NO = Q.INDI_DSCM_NO
                AND E.EXMD_BZ_YYYY = Q.EXMD_BZ_YYYY
            WHERE (CAST(P.STD_YYYY AS INTEGER) - CAST(P.BYEAR AS INTEGER)) BETWEEN 20 AND 45
              AND P.STD_YYYY = '2018'
              AND E.G1E_HGHT IS NOT NULL AND E.G1E_HGHT > 0
              AND E.G1E_WSTC IS NOT NULL AND E.G1E_WSTC > 0
            """

    return f"""
            SELECT 
                P.INDI_DSCM_NO,
                P.STD_YYYY,
                P.SEX_TYPE,
                P.BYEAR,
                (CAST(P.STD_YYYY AS INTEGER) - CAST(P.BYEAR AS INTEGER)) AS AGED,
                E.HC_BZ_YYYY AS BZ_YYYY,
                E.G1E_HGHT,
                E.G1E_WGHT,
                E.G1E_BMI,
                E.G1E_WSTC,
                E.G1E_BP_SYS,
                E.G1E_BP_DIA,
                E.G1E_FBS,
                E.G1E_TOT_CHOL,
                E.G1E_GFR,
                CASE WHEN CAST(Q.Q_SMK_NOW_YN AS VARCHAR) IN ('1', 'Y', 'YES') THEN 1 ELSE 0 END AS SMK_CURR,
                CASE
                    WHEN CAST(Q.Q_DRK_PER AS VARCHAR) IN ('0', 'N', 'NO') THEN 0
                    WHEN CAST(Q.Q_DRK_FRQ AS INTEGER) >= 4 THEN 2
                    WHEN CAST(Q.Q_DRK_FRQ AS INTEGER) >= 2 THEN 1
                    WHEN Q.Q_DRK_PER IS NOT NULL THEN 1
                    ELSE 0
                END AS DRK_LEVEL,
                CASE
                    WHEN CAST(Q.Q_PA_VD_FRQ AS INTEGER) >= 3 THEN 1
                    WHEN CAST(Q.Q_PA_MD_FRQ AS INTEGER) >= 5 THEN 1
                    WHEN CAST(Q.Q_PA_MUSL_FRQ AS INTEGER) >= 2 THEN 1
                    ELSE 0
                END AS PA_ACTIVE
            FROM NHISBDA.HHDV_DSES_YY P
            INNER JOIN NHISBDA.HMDT_G1E_RST_{year} E
                ON P.INDI_DSCM_NO = E.INDI_DSCM_NO
                AND P.STD_YYYY = E.HC_BZ_YYYY
            INNER JOIN NHISBDA.HMDT_GQ_RST_{year} Q
                ON E.INDI_DSCM_NO = Q.INDI_DSCM_NO
                AND E.HC_BZ_YYYY = Q.HC_BZ_YYYY
            WHERE (CAST(P.STD_YYYY AS INTEGER) - CAST(P.BYEAR AS INTEGER)) BETWEEN 20 AND 45
              AND P.STD_YYYY = '{year}'
              AND E.G1E_HGHT IS NOT NULL AND E.G1E_HGHT > 0
              AND E.G1E_WSTC IS NOT NULL AND E.G1E_WSTC > 0
            """


def get_connection():
    """사용자로부터 HANA DB 접속 정보를 입력받아 연결 객체를 반환합니다."""
    print("\n" + "=" * 50)
    print("📡 SAP HANA DB 연결 설정 (Windows/폐쇄망 구동 - 스키마 고정 추출)")
    print("=" * 50)
    
    ip = input("1. HANA DB IP 주소 (예: 192.168.1.100): ").strip()
    port = input("2. PORT 번호 (예: 30015): ").strip()
    user_id = input("3. 데이터베이스 사용자 ID: ").strip()
    password = getpass.getpass("4. 데이터베이스 비밀번호: ").strip()
    
    print("\n[~] HANA DB에 연결을 시도하는 중...")
    try:
        conn = dbapi.connect(
            address=ip,
            port=int(port),
            user=user_id,
            password=password
        )
        print("[+] HANA DB 연결 성공!")
        return conn
    except Exception as e:
        print(f"[x] 연결 실패: {e}")
        return None


def run_extraction_pipeline(conn):
    """지정된 스키마 배정 및 시간분할 기준에 맞춰 데이터를 다운로드합니다."""
    print("\n" + "=" * 60)
    print("🚀 다중 시간분할 고속 로컬 다운로드 파이프라인 가동")
    print("=" * 60)
    
    try:
        start_year = int(input("1. 연구 분석 시작년도 (예: 2009): ").strip())
        end_year = int(input("2. 연구 분석 종료년도 (예: 2024): ").strip())
    except ValueError:
        print("[x] 에러: 연도는 정수로 입력해주셔야 합니다.")
        return False
        
    script_dir = os.path.dirname(os.path.abspath(__file__))
    raw_dir = os.path.join(script_dir, "data", "raw")
    
    # 로컬 계층형 디렉터리 생성
    death_dir = os.path.join(raw_dir, "death")
    elig_checkup_dir = os.path.join(raw_dir, "eligibility_checkup")
    diag_dir = os.path.join(raw_dir, "diagnosis")
    billing_dir = os.path.join(raw_dir, "billing")
    
    for d in [death_dir, elig_checkup_dir, diag_dir, billing_dir]:
        os.makedirs(d, exist_ok=True)
        
    cursor = conn.cursor()
    
    # -------------------------------------------------------------
    # 1단계: 사망 데이터 추출 (전체 분석 기간 1회 일시 추출)
    # -------------------------------------------------------------
    print("\n[1단계] 사망 데이터(NHISBDA.HHDT_DEATH) 일시 추출에 진입합니다...")
    death_file = os.path.join(death_dir, "death_all.csv")
    death_query = f"""
    SELECT 
        INDI_DSCM_NO,
        DTH_ASSMD_DT
    FROM NHISBDA.HHDT_DEATH
    WHERE DTH_ASSMD_DT BETWEEN '{start_year}0101' AND '{end_year}1231'
    """
    try:
        print(f"  [실행 쿼리]: SELECT FROM NHISBDA.HHDT_DEATH (기간: {start_year}~{end_year})")
        df_death = pd.read_sql(death_query, conn)
        df_death.to_csv(death_file, index=False, encoding='utf-8-sig')
        print(f"  [+] 성공: 사망 데이터 추출 완료 ({len(df_death):,}건) -> {death_file}")
    except Exception as e:
        print(f"  [x] 에러: 사망 데이터 추출 실패 (또는 테이블 부재): {e}")
        # 사망 데이터가 없는 연구 환경 대비 빈 파일 생성
        pd.DataFrame(columns=["INDI_DSCM_NO", "DTH_ASSMD_DT"]).to_csv(death_file, index=False, encoding='utf-8-sig')
        print("  [!] 경고: 빈 사망 데이터 스키마 파일을 로컬 하드에 생성하였습니다.")

    # -------------------------------------------------------------
    # 2단계: 자격 및 검진 데이터 추출 (연 단위 루프 추출)
    # -------------------------------------------------------------
    print("\n[2단계] 자격 & 검진 데이터(NHISBDA 스키마) 연 단위 분할 추출에 진입합니다...")
    for year in range(start_year, end_year + 1):
        year_file = os.path.join(elig_checkup_dir, f"elig_checkup_{year}.csv")
        print(f"\n  [~] {year}년도 자격 & 검진 테이블 연동을 시도합니다...")
        
        query = get_eligibility_checkup_query(year)
        
        try:
            df_year = pd.read_sql(query, conn)
            df_year = harmonize_lifestyle(df_year, get_lifestyle_era(year))
            df_year.to_csv(year_file, index=False, encoding='utf-8-sig')
            print(f"    [+] 성공: {year}년도 자격 & 검진 다운로드 완료 ({len(df_year):,}건) -> {year_file}")
        except Exception as ey:
            print(f"    [x] 실패: {year}년도 테이블 누락 혹은 접근 거부: {ey}")
            continue

    # -------------------------------------------------------------
    # 3단계: 상병 및 일반명세 데이터 추출 (월 단위 이중 루프 추출)
    # -------------------------------------------------------------
    print("\n[3단계] 상병 & 일반명세 데이터(NHISBASE 스키마) 월 단위 분할 다운로드에 진입합니다...")
    for year in range(start_year, end_year + 1):
        for month in range(1, 13):
            date_prefix = f"{year}{month:02d}"
            
            # A. 상병(Diagnosis - HBMT_TBGJME40) 추출
            diag_file = os.path.join(diag_dir, f"diagnosis_{year}_{month:02d}.csv")
            diag_query = f"""
            SELECT 
                INDI_DSCM_NO,
                MCEX_SICK_SYM,
                MDCARE_STRT_DT
            FROM NHISBASE.HBMT_TBGJME40
            WHERE MDCARE_STRT_DT LIKE '{date_prefix}%'
            """
            
            # B. 일반명세(Billing - HBMT_TBGJME20) 추출
            bill_file = os.path.join(billing_dir, f"billing_{year}_{month:02d}.csv")
            bill_query = f"""
            SELECT 
                CMN_KEY,
                INDI_DSCM_NO,
                MCARE_TP,
                HSPTZ_VSHSP_DD_CNT,
                MDCARE_STRT_DT
            FROM NHISBASE.HBMT_TBGJME20
            WHERE MDCARE_STRT_DT LIKE '{date_prefix}%'
            """
            
            # 상병 다운로드 구동
            try:
                df_diag = pd.read_sql(diag_query, conn)
                if len(df_diag) > 0:
                    df_diag.to_csv(diag_file, index=False, encoding='utf-8-sig')
                    print(f"  [+] 상병 성공: {year}년 {month:02d}월 ({len(df_diag):,}건) -> {diag_file}")
                else:
                    # 데이터가 비어있어도 무결성 유지를 위해 빈 스키마 저장
                    pd.DataFrame(columns=["INDI_DSCM_NO", "MCEX_SICK_SYM", "MDCARE_STRT_DT"]).to_csv(diag_file, index=False, encoding='utf-8-sig')
            except Exception as ed:
                # 테이블 누락 대비 빈 스키마 저장
                pd.DataFrame(columns=["INDI_DSCM_NO", "MCEX_SICK_SYM", "MDCARE_STRT_DT"]).to_csv(diag_file, index=False, encoding='utf-8-sig')
                print(f"  [!] 상병 경고: {year}년 {month:02d}월 누락/접근불가 (빈 파일 대체)")
                
            # 일반명세 다운로드 구동
            try:
                df_bill = pd.read_sql(bill_query, conn)
                if len(df_bill) > 0:
                    df_bill.to_csv(bill_file, index=False, encoding='utf-8-sig')
                    print(f"  [+] 명세 성공: {year}년 {month:02d}월 ({len(df_bill):,}건) -> {bill_file}")
                else:
                    pd.DataFrame(columns=["CMN_KEY", "INDI_DSCM_NO", "MCARE_TP", "HSPTZ_VSHSP_DD_CNT", "MDCARE_STRT_DT"]).to_csv(bill_file, index=False, encoding='utf-8-sig')
            except Exception as eb:
                pd.DataFrame(columns=["CMN_KEY", "INDI_DSCM_NO", "MCARE_TP", "HSPTZ_VSHSP_DD_CNT", "MDCARE_STRT_DT"]).to_csv(bill_file, index=False, encoding='utf-8-sig')
                print(f"  [!] 명세 경고: {year}년 {month:02d}월 누락/접근불가 (빈 파일 대체)")

    cursor.close()
    return True


def main():
    print("=" * 60)
    print("🌟 국민건강보험 빅데이터 고성능 분할 로컬 추출 파이프라인 (HANA DB)")
    print("=" * 60)
    
    conn = get_connection()
    if not conn:
        print("[x] 데이터베이스 연결에 실패하여 종료합니다.")
        input("\n종료하려면 엔터를 누르십시오...")
        sys.exit(1)
        
    try:
        success = run_extraction_pipeline(conn)
        if success:
            print("\n🎉 모든 분할 로컬 추출이 완료되었습니다! (data/raw/ 폴더를 확인하십시오)")
    finally:
        conn.close()
        print("[+] HANA DB 연결이 정상 해제되었습니다.")
        input("\n작업 완료. 종료하려면 엔터를 누르십시오...")


if __name__ == "__main__":
    main()
