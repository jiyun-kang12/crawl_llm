# Crawl-Parsing pipeline 설계 with Crawl4ai

핵심은 **"LLM에게 원본 HTML을 통째로 주지 않는 것"**입니다. 

## 전체 방향: "LLM은 판단만, 파싱은 코드가"

LLM에게 HTML을 그대로 넘기고 "다운로드 방식 찾아줘"라고 하면:
- 토큰 비용 폭증 (페이지 하나당 수만 토큰)
- HTML 노이즈(네비게이션, 광고, 스크립트) 때문에 정확도 저하
- 결과가 자유 텍스트라서 후속 자동화(다운로드 실행)에 파싱이 또 필요함

대신 **코드로 후보를 추려서(전처리) 압축된 정보만 LLM에게 주고, LLM은 구조화된 JSON(schema)으로만 답하게** 하는 게 훨씬 효율적이고 안정적입니다.

---

## 단계별 재설계

### 사용하는 도구 - crawl4ai
- crawling과 llm 연결은 이 도구를 같이 활용
- llm은 우선 무료 모델 사용

```
# Install the package
pip install -U crawl4ai

# For pre release versions
pip install crawl4ai --pre

# Run post-installation setup
crawl4ai-setup

# Verify your installation
crawl4ai-doctor
```

### 1단계: 메뉴 탐색 (Main Page → 관련 메뉴 URL)

HTML 전체 대신 `nav`, `header`, `.gnb`, `.menu`, `ul.nav` 같은 셀렉터로 **메뉴 영역만 추출**해서 링크 텍스트+href만 뽑습니다.

```python
from bs4 import BeautifulSoup

def extract_menu_links(html, base_url):
    soup = BeautifulSoup(html, "html.parser")
    candidates = soup.select("nav a, header a, .gnb a, .menu a, .lnb a, ul.nav a")
    return [
        {"text": a.get_text(strip=True), "href": urljoin(base_url, a["href"])}
        for a in candidates if a.get("href")
    ]
```

이 텍스트+href 리스트(보통 몇백 토큰 이내)만 LLM에 전달하고, **자유 답변이 아니라 구조화 스키마**로 받습니다.

```python
from pydantic import BaseModel

class MenuSelection(BaseModel):
    text: str
    href: str
    relevance_score: float  # 0~1, 인문사회 연구 관련도
    reason: str
```

프롬프트 예시:
```
아래는 웹사이트의 메뉴 링크 목록입니다. 이 중 "인문사회 연구 보고서/발간자료/논문"과
관련된 메뉴만 선택하고, 각각에 관련도 점수(0~1)를 매기세요.
관련 없는 일반 메뉴(로그인, 사이트맵, 채용정보 등)는 제외하세요.
```

→ 이 단계는 **저렴하고 빠른 모델**(예: gpt-4o-mini, claude-haiku)로 충분합니다. 판단이 단순(카테고리 분류)하기 때문이에요.

---

### 2단계: 선정된 메뉴 페이지 순회 → 다운로드 링크 후보 추출

여기가 핵심 개선 포인트입니다. **"HTML 다운로드 방식을 LLM이 찾게"** 하지 말고, **모든 `<a>` 태그를 코드로 먼저 추출**해서 다운로드 가능성이 있는 것만 후보로 압축한 뒤 LLM에게 넘기세요.

```python
def extract_download_candidates(html, base_url):
    soup = BeautifulSoup(html, "html.parser")
    candidates = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        text = a.get_text(strip=True)
        # 확장자 있는 것 + 다운로드 관련 키워드/패턴 있는 것 모두 후보로
        if any(k in href.lower() for k in ["download", "file", "attach", "board"]) \
           or any(k in text for k in ["다운로드", "첨부", "PDF", "한글", "HWP", ".pdf", ".hwp", ".docx"]) \
           or href.lower().endswith((".pdf", ".hwp", ".hwpx", ".docx")):
            # 주변 문맥(제목, 게시글 제목 등)도 같이 수집하면 LLM 판단 정확도↑
            parent_text = a.find_parent(["li", "tr", "div"])
            context = parent_text.get_text(" ", strip=True)[:200] if parent_text else ""
            candidates.append({
                "href": urljoin(base_url, href),
                "link_text": text,
                "context": context
            })
    return candidates
```

이렇게 하면 페이지 하나에 링크가 수백 개 있어도 실제 후보는 보통 몇 개~몇십 개로 줄어듭니다. 이 축약된 리스트만 LLM에게 보냅니다.

```python
class DownloadCandidate(BaseModel):
    href: str
    is_target_document: bool     # 인문사회 연구 관련 줄글 문서인지
    file_type_guess: str         # pdf / hwp / docx / unknown
    confidence: float
    title_guess: str             # 문서 제목 추정 (파일명으로 활용)
```

프롬프트:
```
아래는 게시판 페이지에서 추출한 다운로드 후보 링크 목록입니다.
각 항목의 link_text와 context를 보고, 인문사회 연구 보고서/논문류의
PDF, HWP, DOCX 파일 다운로드 링크인지 판단하세요.
표지, 목차만 있는 요약본이 아니라 본문 전체(long-context 줄글) 문서로
추정되는 것만 is_target_document=true로 표시하세요.
```

→ 여기서도 판단 자체는 단순 분류라서 저가 모델로 충분합니다. **다만 결과가 불확실(confidence < 0.6)한 경우만 상위 모델로 재검증**하는 2단계 필터링을 넣으면 비용 대비 정확도가 좋아집니다.

---

### 3단계: 실제 다운로드 (파일 타입 검증 + 저장)

LLM의 `file_type_guess`는 **참고용**이지, 신뢰해서 바로 저장하면 안 됩니다. 실제로는 HEAD 요청으로 `Content-Type`/`Content-Disposition`을 확인해서 진짜 pdf/hwp/docx인지 검증하세요.

```python
import aiohttp, re, os

ALLOWED_TYPES = {
    "application/pdf": ".pdf",
    "application/x-hwp": ".hwp",
    "application/haansofthwp": ".hwp",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
}

async def verify_and_download(session, url, title_guess, save_dir, sem):
    async with sem:
        try:
            async with session.get(url, allow_redirects=True) as resp:
                ctype = resp.headers.get("Content-Type", "").split(";")[0].lower()
                cd = resp.headers.get("Content-Disposition", "")

                ext = ALLOWED_TYPES.get(ctype)
                if not ext:
                    m = re.search(r'\.(pdf|hwp|hwpx|docx)', cd, re.I)
                    if m:
                        ext = "." + m.group(1).lower()
                if not ext:
                    return None  # 허용 타입 아니면 스킵

                fname_match = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', cd)
                filename = fname_match.group(1) if fname_match else f"{title_guess}{ext}"
                filename = re.sub(r'[\\/*?:"<>|]', "_", filename)

                path = os.path.join(save_dir, filename)
                with open(path, "wb") as f:
                    f.write(await resp.read())
                return {"url": url, "saved": path}
        except Exception as e:
            return {"url": url, "error": str(e)}
```

동시성 제어는 `asyncio.Semaphore`로 (사이트에 부담 주지 않도록 5~10개 정도로 제한):

```python
sem = asyncio.Semaphore(8)
async with aiohttp.ClientSession() as session:
    tasks = [verify_and_download(session, c["href"], c["title_guess"], save_dir, sem)
             for c in confirmed_candidates]
    results = await asyncio.gather(*tasks)
```

---

## 정리: 효율성/정확도를 위한 핵심 원칙

| 원칙 | 이유 |
|---|---|
| **LLM에 원본 HTML 대신 전처리된 후보 리스트만 전달** | 토큰 절감, 노이즈 제거 → 정확도↑ |
| **자유 텍스트 답변 대신 Pydantic/JSON 스키마 강제** | 후속 자동화 파싱이 안정적 |
| **분류 작업(관련도 판단)은 저가 모델 사용, 애매한 것만 고급 모델 재검증** | 비용 최적화 |
| **LLM 판단은 "후보 선정"까지만, 실제 파일타입 검증은 HEAD/Content-Type로 코드가** | LLM 환각 방지, hwp/pdf 오분류 방지 |
| **비동기 다운로드는 Semaphore로 동시성 제한 + 재시도 로직** | 사이트 차단/타임아웃 방지 |
| **다운로드 로그(manifest.json)로 URL-파일-메타데이터 매핑 저장** | 중복 다운로드 방지, 추적 가능 |

이 구조면 페이지당 LLM 호출 2번(메뉴 판단은 전체 1번, 페이지별 후보 판단 n번) 정도로 끝나고, 각 호출의 입력이 작아서 비용도 크게 줄어듭니다.

