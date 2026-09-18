# 네이버 노출 추적기

두 센터(리부트 철산 / 팀포이즈 광교)의 네이버 검색 노출을 매일 자동으로 기록하고
GitHub Pages 대시보드로 보여준다. 서버비 0원, GitHub Actions만 사용.

- **플레이스**: 지역검색 API로 상위 5위 안에서의 위치 (API가 5개까지만 준다 — 그 밖은 측정 불가)
- **블로그**: 블로그 검색 API로 우리 글의 실제 순위 (100개씩 페이지를 넘겨 최대 300위까지)
- **키워드 발굴**: 검색광고 키워드도구 API로 절대 월간 검색수 (선택, 키가 있어야 동작)

---

## 30분 셋업

### 1. 저장소 만들기

```bash
gh repo create naver-rank-tracker --private --source=. --remote=origin
git add . && git commit -m "init"
git push -u origin main
```

### 2. 검색 API 키 발급 — NAVER CLOUD PLATFORM

> **2026-06-25부터 네이버 검색 API는 NAVER API HUB(네이버 클라우드 플랫폼)로 이관됐다.**
> developers.naver.com 신규 발급은 2026-07-31로 끝났고, 기존 키는 2027-06-30까지만 동작한다.
> 이 저장소는 **둘 다 지원**하지만, 새로 시작한다면 HUB 쪽이다.

1. https://console.ncloud.com 로그인 (가입 시 결제수단 등록이 필요하다)
2. **Services → Application Service → NAVER API HUB** 이용 신청
3. Application 등록 후 **Client ID / Client Secret** 발급

무료 한도는 **하루 25,000회**. 이 추적기는 키워드 8개 기준 하루 약 32회를 쓴다.

| | 신규 (HUB) | 레거시 |
|---|---|---|
| 호스트 | `naverapihub.apigw.ntruss.com` | `openapi.naver.com` |
| 지역검색 | `GET /search/v1/local` | `GET /v1/search/local.json` |
| 블로그검색 | `GET /search/v1/blog` | `GET /v1/search/blog.json` |
| 인증 헤더 | `X-NCP-APIGW-API-KEY-ID` / `X-NCP-APIGW-API-KEY` | `X-Naver-Client-Id` / `X-Naver-Client-Secret` |

문서: https://api.ncloud-docs.com/docs/naver-api-hub-overview

**파라미터 상한 (HUB 문서 기준)**

- 지역검색 `display` 1~5 — 상위 5개까지만 준다. 그 밖의 순위는 이 API로 알 수 없다.
- 블로그검색 `display` 최대 100, `start` 최대 1000 — 최대 1000위까지 확인 가능

### 3. 저장소 Secrets 등록

Settings → Secrets and variables → Actions → New repository secret

| 이름 | 값 |
|---|---|
| `NCP_API_KEY_ID` | HUB Client ID |
| `NCP_API_KEY` | HUB Client Secret |

기존 developers.naver.com 키를 쓸 거면 대신 `NAVER_CLIENT_ID` / `NAVER_CLIENT_SECRET`.
둘 다 있으면 HUB를 우선 사용한다.

### 4. `config/targets.yml` 채우기

**반드시 바꿔야 하는 값**은 `blog_ids` — `blog.naver.com/<여기>` 의 아이디다.
`place_aliases`는 네이버 지역검색에 나오는 업체명과 맞춰야 우리 업체로 인식된다.

### 5. GitHub Pages 켜기

Settings → Pages → Source: **Deploy from a branch** → Branch: `main`, 폴더 `/docs`
→ `https://<아이디>.github.io/naver-rank-tracker/`

### 6. 첫 실행

Actions 탭 → "네이버 노출 추적" → **Run workflow**.
이후 매일 KST 09:00에 자동 실행된다.

---

## 로컬 실행

```bash
pip install -r requirements.txt
export NCP_API_KEY_ID=... NCP_API_KEY=...   # 또는 NAVER_CLIENT_ID / NAVER_CLIENT_SECRET
python src/track.py            # data/rankings.csv 에 append
python src/build_dashboard.py  # docs/index.html 생성
```

## 키워드 발굴 (2단계)

검색광고 API는 **NCP와 무관한 별개 시스템**이라 이관 영향이 없다. 키가 생기면:

```bash
export NAVER_AD_API_KEY=... NAVER_AD_SECRET_KEY=... NAVER_AD_CUSTOMER_ID=...
python src/keywordtool.py 하이록스 크로스핏 철산 광교
# → data/keywords_YYYY-MM-DD.csv (월간 검색수 내림차순)
```

키 발급: 네이버 검색광고 → 도구 → **API 관리** → 액세스 라이선스 / 비밀키 발급.
CUSTOMER_ID는 같은 화면 상단의 고객 ID.

---

## 데이터 형식 — `data/rankings.csv`

| 컬럼 | 의미 |
|---|---|
| `date` | KST 날짜 |
| `center` | `Reboot` / `TeamPoise` |
| `keyword` | 검색어 |
| `surface` | `place` / `blog` |
| `our_rank` | 우리 순위. **빈 값 = 확인 범위 안에 없음** |
| `our_title` | 잡힌 항목 제목 |
| `rank1~3` | 그날 1~3위 (경쟁사 추적용) |
| `checked_depth` | 어디까지 확인했는지 |

한 줄이 하나의 관측이라 그대로 pandas/엑셀로 넘길 수 있다.

---

## 알아둘 한계

1. **지역검색 API는 상위 5개까지만 준다** (`display` 범위 1~5). 실제 네이버 지도 앱 순위는 사용자 위치로
   개인화되므로 이 값과 다를 수 있다. **절대 순위가 아니라 추세 신호로 읽을 것.**
2. 순위가 비어 있다고 "노출 0"이 아니다. "확인한 범위 안에는 없었다"는 뜻이다.
3. 이 도구는 **측정만 한다.** 순위를 올리는 건 콘텐츠와 실제 이용자 행동이다.
   트래픽·리뷰·저장 자동화는 2026년 기준 즉시 제재 대상이라 넣지 않았다.
