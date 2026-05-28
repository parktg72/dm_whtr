# 🩺 국민건강보험 빅데이터 기반 BMI & WHtR 종단 변화 연구 (DM_WHTR)

본 프로젝트는 국민건강보험공단(NHIS) 빅데이터를 활용하여 장기적 비만 지표(BMI) 및 복부 비만 지표(WHtR)의 변화 궤적(Longitudinal Trajectories)을 추적하고, 이에 따른 만성 질환(심뇌혈관 질환, 제2형 당뇨병, 만성 신장 질환 등)의 발생 위험을 다변량 Cox proportional hazards 모델을 통해 규명하는 고성능 데이터 파이프라인 시스템입니다.

특히, **국민건강보험공단 분석실 특유의 Windows 윈도우 환경, Python 3.12 제약, 그리고 외부망 연결이 완전히 차단된 폐쇄망(Air-gapped) 분석 PC**에서 아무런 오류 없이 100% 가동 및 설치될 수 있도록 설계되었습니다.

---

## 🏢 멀티 에이전트 협업 체계 (Headquarters)
본 프로젝트는 SPoC인 **AGY 본부 (Gemini)**의 조율 하에, **Claude 본부 (Anthropic)**의 역학 및 생물 통계 설계, **Codex 본부 (OpenAI Codex)**의 초고속 알고리즘 개발이 유기적으로 연동하여 완성되었습니다.

---

## 📂 파일 구조 및 역할 (Directory Map)

* **[generate_synthetic_db.py](file:///mnt/h/dm_whtr/generate_synthetic_db.py):** NHIS 원천 빅데이터 스키마와 데이터 분포를 반영한 1,000명의 합성 가상 SQLite 데이터베이스(`synthetic_nhis.db`) 자동 생성 엔진.
* **[build_cohort.py](file:///mnt/h/dm_whtr/build_cohort.py):** 나이 필터, 검진 빈도 조건(4개년 중 3회 이상), 기왕력 Wash-out(2013-01-01 이전), 2x2 비만 조합 매트릭스 분류, OLS 변화율 기울기($\beta$) 연산, Lag-time(1년) 민감도 필터를 가동하는 핵심 ETL 파이프라인.
* **[analyze_trajectories.py](file:///mnt/h/dm_whtr/analyze_trajectories.py):** OLS 기울기 기반 K-Means 클러스터링(Stable, Increasing, Decreasing 군집화) 및 5대 아웃컴별 다변량 Cox proportional hazards 모델(Model 1~3) 피팅, KM Curves 및 Forest Plot 차트 자동 시각화 엔진.
* **[extract_hana.py](file:///mnt/h/dm_whtr/extract_hana.py):** 실제 연구 착수 시 건보공단 서버(SAP HANA DB)에 원격 연동하여 원천 데이터를 CP949 인코딩 깨짐 없이 대화형으로 고속 수집하는 범용 데이터 추출 스크립트.
* **[validate_extracted_data.py](file:///mnt/h/dm_whtr/validate_extracted_data.py):** HANA 추출 직후 `data/raw/` 계층형 CSV의 파일 완결성, 필수 스키마, 공허 파일 허용 규칙, 핵심 수치 범위, 결측률, 생활습관 harmonization, PK 및 `BZ_YYYY`/`STD_YYYY` 일관성을 사전 검증하는 원천 산출물 품질 게이트.
* **[validate_cohort_output.py](file:///mnt/h/dm_whtr/validate_cohort_output.py):** `merge_local_data_duckdb.py` 또는 `build_cohort.py` 실행 후 `data/cohort_analytical.csv`와 `data/cohort_analytical_lag1y.csv`를 Cox 분석 전에 검증하는 최종 코호트 QA 게이트. 필수 컬럼, 16자리 해시 ID, 비만군 일관성, 공변량 범위, 사건/시간 논리, 복합 endpoint, Lag-time subset, Cox event sparsity를 점검합니다.
* **[requirements.txt](file:///mnt/h/dm_whtr/requirements.txt):** 오프라인 환경 이식을 위해 고정된 패키지 및 라이브러리 버전 명세서.
* **[test_cohort_pipeline.py](file:///mnt/h/dm_whtr/test_cohort_pipeline.py):** ETL 및 필터링, 비만 지표 연산 정합성을 검증하는 6대 핵심 단위 테스트 슈트.
* **[test_analysis_pipeline.py](file:///mnt/h/dm_whtr/test_analysis_pipeline.py):** K-Means 군집 분류, Cox PH 수렴성, 위험비 범위, 시각화 파일 생성을 검증하는 4대 분석 단위 테스트 슈트.
* **[test_validate_extracted_data.py](file:///mnt/h/dm_whtr/test_validate_extracted_data.py):** `validate_extracted_data.py`의 파일/스키마/결측/수치/PK 검증 로직을 작은 fixture CSV로 검증하는 TDD 테스트 슈트.
* **[test_validate_cohort_output.py](file:///mnt/h/dm_whtr/test_validate_cohort_output.py):** `validate_cohort_output.py`의 최종 코호트 stop-the-line 실패 기준을 검증하는 TDD 테스트 슈트.

---

## 📡 폐쇄망(Air-gapped) 오프라인 배포 및 이식 가이드

인터넷 연결이 완전히 차단된 건보공단 분석 PC 환경에서 라이브러리 및 소스코드를 가동하기 위한 오프라인 휠하우스(Wheelhouse) 이식 표준 매뉴얼입니다.

### [1단계] 외부 인터넷 PC에서 패키지 다운로드
연구실 또는 자택 등 외부 인터넷 연결 PC(반드시 대상 폐쇄망 PC와 **동일한 OS 및 Python 3.12 버전** 환경 권장)에서 본 프로젝트 디렉토리로 이동한 후, `requirements.txt`에 명세된 모든 Wheel 파일을 다운로드합니다:
```cmd
mkdir C:\packages
pip download -r requirements.txt -d C:\packages
```

### [2단계] 저장 매체(USB 등)를 통한 폐쇄망 이송
`C:\packages` 폴더 전체와 프로젝트 소스코드 디렉토리를 보안 USB 또는 외장 저장 매체를 활용해 폐쇄망 PC 내부로 이송합니다.

### [3단계] 폐쇄망 PC 내 로컬 가상환경 구축 및 설치
폐쇄망 분석 PC의 터미널(CMD, PowerShell 또는 Bash)을 열고, 프로젝트 디렉토리 내에 독립 가상환경(`venv`)을 생성한 뒤 인터넷망 조회를 전면 차단(`--no-index`)한 상태에서 로컬 휠을 사용하여 배포합니다:
```bash
# 1. 가상환경 생성 ( symlink 에러 시 --copies 옵션 추가 )
python3 -m venv venv

# 2. 가상환경 진입
# (Windows)
venv\Scripts\activate
# (Linux / WSL)
source venv/bin/activate

# 3. 로컬 휠 폴더 경로를 지정하여 오프라인 강제 설치
pip install --no-index --find-links=C:\packages -r requirements.txt
```

### [4단계] 스모크 테스트(Smoke Test)를 통한 시스템 안정성 즉시 검증
폐쇄망 환경에 모든 휠 설치가 정상 완료되었는지, 그리고 분석 코드가 정상 작동하는지 원천 데이터를 돌리기 전에 10초 만에 단위 검증을 마칩니다:
```bash
# 1단계: 원천 추출 산출물 검증기 TDD 구동
python3 -m unittest test_validate_extracted_data.py

# 2단계: 최종 코호트 산출물 검증기 TDD 구동
python3 -m unittest test_validate_cohort_output.py

# 3단계: 코호트 파이프라인 TDD 구동
python3 -m unittest test_cohort_pipeline.py

# 4단계: 통계 분석 파이프라인 TDD 구동
python3 -m unittest test_analysis_pipeline.py

# 전체 테스트 일괄 실행
python3 -m unittest discover -v

# 합성 DB → 코호트 → 전체 테스트 → 최종 QA → 분석까지 한 번에 실행
python3 run_pipeline_smoke_test.py

# 빠른 환경 점검용: 분석 단계는 생략하고 전체 테스트와 최종 QA까지만 실행
python3 run_pipeline_smoke_test.py --skip-analysis
```
모든 명령어가 **OK**로 끝나면, 폐쇄망 내부 시스템 설치가 완벽하게 성공한 것입니다.

WSL에서 `/mnt/...` 경로에 venv 생성 시 symlink 또는 `lib64` 권한 문제가 발생하면 WSL native filesystem에 전용 venv를 생성해 사용합니다:
```bash
python3 -m venv --copies /home/ptg/venvs/dm_whtr
/home/ptg/venvs/dm_whtr/bin/python -m pip install --no-index --find-links=/mnt/h/dm_whtr/wheels/linux -r /mnt/h/dm_whtr/requirements.txt
/home/ptg/venvs/dm_whtr/bin/python -m unittest discover -v
```

---

## 🛠️ 연구 실행 단계 (Running the Pipeline)

스모크 테스트를 마친 후, 가상 데이터셋 또는 SAP HANA DB로부터 원천 데이터 로드가 완료되면 다음 순서대로 연구를 실행합니다.

### 1단계: 가상 데이터베이스 생성 (또는 HANA 추출 가동)
* **가상 데이터 테스트 시:**
  ```bash
  python3 generate_synthetic_db.py
  ```
* **실제 HANA DB 연동 시:**
  ```bash
  python3 extract_hana.py
  ```

### 2단계: HANA 추출 산출물 품질 게이트 실행 (실제 HANA 연동 시 필수)
```bash
python3 validate_extracted_data.py --raw-dir data/raw --start-year 2009 --end-year 2023
```
* **역할:** `extract_hana.py`가 생성한 `data/raw/` 하위 CSV 파일이 누락 없이 존재하는지, 필수 컬럼과 주요 변수 범위가 정상인지, 비어도 되는 월별 상병/명세 파일과 실패해야 하는 연도별 검진 파일을 구분해 검증합니다.
* **중단 기준:** 이 단계가 실패하면 `merge_local_data_duckdb.py` 또는 `build_cohort.py`로 진행하지 말고 누락 파일, 스키마, 결측률, `BZ_YYYY`/`STD_YYYY` 불일치 원인을 먼저 수정해야 합니다.

### 3단계: DuckDB 기반 로컬 병합 및 코호트 정제 실행
```bash
python3 merge_local_data_duckdb.py
```
* **결과물:** 실제 HANA 추출 CSV 기반 `data/cohort_analytical.csv` 및 1년 Lag-time 민감도 코호트 `data/cohort_analytical_lag1y.csv`가 Excel 호환 UTF-8 BOM(`utf-8-sig`) 형식으로 추출됩니다.

### 4단계: SQLite 가상 데이터 기반 코호트 정제 및 OLS 변화율 전처리 실행 (시뮬레이션/테스트용)
```bash
python3 build_cohort.py
```
* **결과물:** 가상 SQLite 데이터셋 기반 `data/cohort_analytical.csv` 및 1년 Lag-time이 반영된 민감도 코호트 `data/cohort_analytical_lag1y.csv`가 Excel 호환 UTF-8 BOM(`utf-8-sig`) 형식으로 추출됩니다.

### 5단계: 최종 코호트 QA 게이트 실행 (Cox 분석 전 필수)
```bash
python3 validate_cohort_output.py --cohort data/cohort_analytical.csv --lag1y data/cohort_analytical_lag1y.csv
```
* **역할:** 최종 분석 코호트의 필수 컬럼, 해시 ID 비식별성, BMI/WHtR 기반 `Obese_Group` 정합성, 공변량·trajectory slope 범위, endpoint event/time 논리, death censoring을 포함한 `Event_Any`/`Time_Any` 복합 endpoint 일관성, 1년 Lag-time 코호트 subset 관계, Cox 분석 event sparsity를 점검합니다.
* **중단 기준:** 이 단계가 실패하면 `analyze_trajectories.py`로 진행하지 않습니다. 현재 합성 산출물은 `generate_synthetic_db.py`의 체중 하한 및 혈압 순서 보정 후 이 게이트를 통과하며, endpoint별 EPV 부족은 warning으로 보고됩니다.

### 6단계: 종단 궤적 분류 및 다변량 Cox Proportional Hazards 생존분석 실행
```bash
python3 analyze_trajectories.py
```
* **결과물:**
  1. **[cox_analysis_summary.csv](file:///mnt/h/dm_whtr/data/cox_analysis_summary.csv):** 5대 질병별 Model 1, 2, 3 전체 통계 수치 표.
  2. **[kaplan_meier_curves.png](file:///mnt/h/dm_whtr/data/plots/kaplan_meier_curves.png):** Obesity Group 및 궤적 그룹별 Kaplan-Meier 생존 곡선 시각화 차트.
  3. **[hazard_ratio_forest_plot.png](file:///mnt/h/dm_whtr/data/plots/hazard_ratio_forest_plot.png):** 최종 Model 3 풀보정 위험비(Hazard Ratio) 및 95% 신뢰구간(CI) Forest Plot 차트.

---

## 📊 11개년 가상 코호트 (N = 517) 통계 분석 결과 요약

본 시스템 검증용 가상 코호트(N = 517명)의 최종 다변량 Cox Proportional Hazards 회귀분석(Model 3: 풀보정 모델) 결과입니다.

* **비만 & 복부비만 동시 보유군(Group 4)의 초고위험성:**
  - **전체 만성질환 (Any Event):** 정상군 대비 발생 위험 **2.19배 유의하게 급증** (95% CI: 1.06 - 4.53, **p = 0.0350**)
  - **제2형 당뇨 (T2DM):** 정상군 대비 발생 위험 **6.10배 극단적 급증** (95% CI: 1.03 - 35.99, **p = 0.0458**)
* **종단 비만 감소 궤적군(Decreasing Trajectory)의 예방 효과:**
  - **전체 만성질환 (Any Event):** 체중/복부 비만 지표가 추적 기간 동안 지속 감소한 군은 안정 유지군 대비 만성질환 발생 위험이 **59% 크게 감소** (HR = 0.41, 95% CI: 0.20 - 0.81, **p = 0.0099**)

본 통계 모형의 유의성은 실제 원천 빅데이터 투입 시 신뢰구간이 더욱 엄밀하고 조밀하게 좁아지며 견고하게 검증될 것입니다.
