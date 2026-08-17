
Crawl4AI가 뭐길래 — 왜 기존 크롤러로는 부족한가
한 줄로 말하면, LLM이 바로 먹을 수 있는 마크다운을 뱉어주는 비동기 웹 크롤러다. BeautifulSoup이나 Scrapy가 HTML 파싱에 초점을 맞추는 것과 달리, Crawl4AI는 “크롤링 결과를 AI 모델에 바로 넣을 수 있는가”를 기준으로 설계됐다.

Crawl4AI가 해결하는 문제 — HTML은 브라우저용 언어이고 LLM/RAG 파이프라인 구축 시 발생하는 병목 현상과 기존 도구의 한계
Crawl4AI가 해결하는 문제 — HTML은 브라우저용 언어이고 LLM/RAG 파이프라인 구축 시 발생하는 병목 현상과 기존 도구의 한계
unclecode라는 개발자가 만들었고 Apache License 2.0이다. API 키도 구독도 필요 없다. Python 3.10 이상이면 돌아가고, 2026년 5월 기준 v0.8.6이 최신이다.

Crawl4AI 크롤러 아키타입 비교 — BeautifulSoup/Scrapy 전통적 파서, Firecrawl/Jina Reader 상용 SaaS, Crawl4AI 오픈소스 AI 네이티브의 방식·비용·JS 렌더링·LLM 적합성 비교
기능	설명
마크다운 변환	HTML을 RAG 파이프라인에 최적화된 깨끗한 마크다운으로 자동 변환
구조화 추출	CSS 셀렉터, XPath, LLM 기반으로 반복 패턴 데이터를 JSON으로 추출
브라우저 제어	Playwright 기반 헤드리스 브라우저로 JS 렌더링, 프록시, 스텔스 모드 지원
비동기 아키텍처	asyncio 기반으로 수십 개 URL을 동시에 크롤링 가능
딥 크롤링	BFS, DFS, BestFirst 전략으로 사이트 전체를 체계적으로 탐색
적응형 크롤링	충분한 정보를 수집하면 자동으로 멈추는 지능형 크롤링
비슷한 도구로 Firecrawl과 Jina Reader API가 있다. Firecrawl은 SaaS라서 호출당 비용이 붙고, Jina Reader는 단일 페이지 변환에 특화되어 있다. Crawl4AI는 로컬에서 무료로 돌리면서 딥 크롤링과 구조화 추출까지 된다는 게 차이점이다.

설치 3분 컷 — pip에서 Docker까지
설치는 간단하다. pip 설치, 브라우저 셋업, 환경 점검. 세 줄이면 된다.

```Shell
pip install -U crawl4ai
crawl4ai-setup
crawl4ai-doctor
```
crawl4ai-setup이 내부적으로 Playwright Chromium 바이너리를 다운로드하고, crawl4ai-doctor가 환경이 정상인지 확인해준다. 깨끗한 환경이라면 1분이면 끝난다.

Docker 쪽이 편하면 공식 이미지도 있다.Linux 및 Unix

```Shell
docker pull unclecode/crawl4ai:latest
docker run -p 11235:11235 unclecode/crawl4ai:latest
```
이미지 안에 브라우저 바이너리가 다 들어있어서 setup 과정이 필요 없다. 11235 포트로 REST API 서버가 뜨니까, 크롤링을 별도 서비스로 분리할 때 쓰기 좋다.

첫 크롤링 — 5줄이면 충분하다
Crawl4AI AsyncWebCrawler 5줄 코드 전체 수집 라이프사이클 — 헤드리스 Chromium 실행부터 URL 페치, DOM 정제, CrawlResult 반환까지 5단계 흐름
AsyncWebCrawler가 모든 것의 시작이다. async context manager로 감싸고 arun()에 URL을 던지면 끝이다.
```Python
import asyncio
from crawl4ai import AsyncWebCrawler

async def main():
async with AsyncWebCrawler() as crawler:
result = await crawler.arun("https://example.com")
print(result.markdown[:500])

if __name__ == "__main__":
asyncio.run(main())
```

async with 블록 안에서 헤드리스 Chromium이 뜨고, URL을 방문해서 HTML을 마크다운으로 변환한다. 블록이 끝나면 브라우저도 정리된다. 반환값인 CrawlResult에 markdown(변환 결과), html(원본), cleaned_html(스크립트 제거 버전), success(성공 여부), status_code(HTTP 코드)가 다 들어있다.

BrowserConfig와 CrawlerRunConfig — 세밀한 제어가 필요할 때
설정이 두 개로 나뉘어 있다. BrowserConfig는 브라우저 인스턴스 레벨 전역 설정이고, CrawlerRunConfig는 개별 크롤링 요청에 붙이는 실행 설정이다. 이 구분 덕분에 하나의 브라우저 인스턴스로 URL마다 다른 설정을 적용할 수 있다.
```Python
from crawl4ai import (
AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode
)

async def main():
    browser_conf = BrowserConfig(
        headless=True,
        browser_type="chromium",
        viewport_width=1280,
        viewport_height=800,
        user_agent="Mozilla/5.0 (compatible; MyCrawler/1.0)",
        text_mode=True,
        verbose=True
        )

run_conf = CrawlerRunConfig(
    cache_mode=CacheMode.BYPASS,
    word_count_threshold=100,
    exclude_external_links=True,
    remove_overlay_elements=True,
    page_timeout=30000
    )

async with AsyncWebCrawler(config=browser_conf) as crawler:
    result = await crawler.arun(
    url="https://example.com",
    config=run_conf
    )
    print(result.markdown)
```
text_mode=True는 이미지 로딩을 꺼서 속도를 높인다. cache_mode=CacheMode.BYPASS는 캐시 무시하고 항상 새로 가져오라는 뜻이고, word_count_threshold=100은 100단어 미만 텍스트 블록을 노이즈로 보고 날린다.컴퓨터 메모리

파라미터	타입	기본값	설명
browser_type	str	“chromium”	브라우저 엔진 (chromium, firefox, webkit)
headless	bool	True	헤드리스 모드
proxy_config	dict	None	프록시 서버 설정
viewport_width	int	1080	브라우저 뷰포트 너비
text_mode	bool	False	이미지 로딩 비활성화
light_mode	bool	False	백그라운드 기능 최소화
enable_stealth	bool	False	봇 탐지 우회 모드
CrawlerRunConfig는 옵션이 많은데, 자주 쓰는 것만 추리면 이 정도다.

파라미터	타입	기본값	설명
cache_mode	CacheMode	BYPASS	캐시 정책
css_selector	str	None	특정 영역만 크롤링
js_code	str/list	None	실행할 JavaScript 코드
wait_for	str	None	대기 조건 (“css:selector” 또는 “js:condition”)
session_id	str	None	세션 유지용 ID
extraction_strategy	object	None	구조화 추출 전략
markdown_generator	object	None	마크다운 생성 전략
screenshot	bool	False	페이지 스크린샷 캡처
마크다운 생성 전략 — 광고랑 네비게이션은 알아서 빠지게
Crawl4AI 마크다운 정제소 — PruningContentFilter 밀도 기반 필터링과 BM25ContentFilter 키워드 기반 필터링으로 10,000단어 원본을 3,000단어 fit_markdown으로 정제
마크다운 생성이 단순 변환에서 끝나지 않는 게 Crawl4AI의 강점이다. PruningContentFilter로 광고나 네비게이션 같은 저밀도 텍스트를 자동으로 잘라낼 수 있고, BM25ContentFilter로 특정 키워드 관련 콘텐츠만 골라낼 수도 있다.

자세히 알아보기
해부
font
컴퓨터 드라이브 및 저장장치
from crawl4ai import AsyncWebCrawler, CrawlerRunConfig, CacheMode
from crawl4ai.content_filter_strategy import PruningContentFilter
from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator

md_generator = DefaultMarkdownGenerator(
content_filter=PruningContentFilter(
threshold=0.4,
threshold_type="fixed"
)
)

config = CrawlerRunConfig(
cache_mode=CacheMode.BYPASS,
markdown_generator=md_generator
)

async with AsyncWebCrawler() as crawler:
result = await crawler.arun(
"https://news.ycombinator.com", config=config
)
print("원본 길이:", len(result.markdown.raw_markdown))
print("필터링 후:", len(result.markdown.fit_markdown))
from crawl4ai import AsyncWebCrawler, CrawlerRunConfig, CacheMode
from crawl4ai.content_filter_strategy import PruningContentFilter
from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator

md_generator = DefaultMarkdownGenerator(
    content_filter=PruningContentFilter(
        threshold=0.4,
        threshold_type="fixed"
    )
)

config = CrawlerRunConfig(
    cache_mode=CacheMode.BYPASS,
    markdown_generator=md_generator
)

async with AsyncWebCrawler() as crawler:
    result = await crawler.arun(
        "https://news.ycombinator.com", config=config
    )
    print("원본 길이:", len(result.markdown.raw_markdown))
    print("필터링 후:", len(result.markdown.fit_markdown))
Python
PruningContentFilter(threshold=0.4)가 콘텐츠 밀도 0.4 미만인 블록을 잘라낸다. 사이드바, 푸터, 광고가 대부분 여기 걸린다. raw_markdown이 필터 없는 전체 마크다운이고, fit_markdown이 필터 후 핵심만 남은 버전이다. Hacker News 같은 사이트에 돌려보면 fit 버전이 raw의 1/3 정도까지 줄어드는 걸 볼 수 있다.

BM25ContentFilter는 다른 접근이다. 검색 엔진에서 쓰는 BM25 알고리즘으로 특정 키워드와 관련성 높은 문단만 뽑는다. RAG 파이프라인에서 주제를 좁혀야 할 때 쓸만하다.

from crawl4ai.content_filter_strategy import BM25ContentFilter

md_generator = DefaultMarkdownGenerator(
content_filter=BM25ContentFilter(
keywords=["machine learning", "neural network"],
threshold=3.0
)
)
from crawl4ai.content_filter_strategy import BM25ContentFilter

md_generator = DefaultMarkdownGenerator(
    content_filter=BM25ContentFilter(
        keywords=["machine learning", "neural network"],
        threshold=3.0
    )
)
Python
“machine learning”이나 “neural network”와 관련성 점수 3.0 이상인 문단만 살린다. threshold를 올리면 기준이 까다로워지니까 결과가 너무 적으면 낮춰보면 된다.

CSS 기반 구조화 추출 — LLM 호출 없이 JSON 뽑기
Crawl4AI 구조화 추출 듀얼 전략 — JsonCssExtractionStrategy 속도·비용 최적화 방식과 LLMExtractionStrategy 의미론적 추론 방식 비교
Crawl4AI 구조화 추출 듀얼 전략 — JsonCssExtractionStrategy 속도·비용 최적화 방식과 LLMExtractionStrategy 의미론적 추론 방식 비교
상품 목록이나 뉴스 리스트처럼 반복되는 패턴을 JSON으로 뽑고 싶다면 JsonCssExtractionStrategy가 답이다. LLM API를 안 쓰니까 빠르고 돈도 안 든다.

import json
from crawl4ai import (
AsyncWebCrawler, CrawlerRunConfig, CacheMode,
JsonCssExtractionStrategy
)

schema = {
"name": "Hacker News 포스트",
"baseSelector": "tr.athing",
"fields": [
{
"name": "title",
"selector": "td.title > span > a",
"type": "text"
},
{
"name": "link",
"selector": "td.title > span > a",
"type": "attribute",
"attribute": "href"
},
{
"name": "rank",
"selector": "td.title > span.rank",
"type": "text"
}
]
}

async with AsyncWebCrawler() as crawler:
result = await crawler.arun(
url="https://news.ycombinator.com",
config=CrawlerRunConfig(
cache_mode=CacheMode.BYPASS,
extraction_strategy=JsonCssExtractionStrategy(schema)
)
)
posts = json.loads(result.extracted_content)
for post in posts[:5]:
print(f"{post['rank']} {post['title']}")
import json
from crawl4ai import (
    AsyncWebCrawler, CrawlerRunConfig, CacheMode,
    JsonCssExtractionStrategy
)

schema = {
    "name": "Hacker News 포스트",
    "baseSelector": "tr.athing",
    "fields": [
        {
            "name": "title",
            "selector": "td.title > span > a",
            "type": "text"
        },
        {
            "name": "link",
            "selector": "td.title > span > a",
            "type": "attribute",
            "attribute": "href"
        },
        {
            "name": "rank",
            "selector": "td.title > span.rank",
            "type": "text"
        }
    ]
}

async with AsyncWebCrawler() as crawler:
    result = await crawler.arun(
        url="https://news.ycombinator.com",
        config=CrawlerRunConfig(
            cache_mode=CacheMode.BYPASS,
            extraction_strategy=JsonCssExtractionStrategy(schema)
        )
    )
    posts = json.loads(result.extracted_content)
    for post in posts[:5]:
        print(f"{post['rank']} {post['title']}")
Python
baseSelector가 반복 요소의 컨테이너를 가리키고, fields가 각 항목에서 뽑을 데이터를 정의한다. type은 “text”(텍스트), “attribute”(HTML 속성값), “html”(내부 HTML) 세 가지다.

재밌는 건 URL 대신 HTML 문자열을 직접 넘길 수도 있다는 거다. raw:// 접두사만 붙이면 된다.

raw_html = "<div class='item'><h2>Item 1</h2><a href='/item1'>Link</a></div>"

result = await crawler.arun(
url="raw://" + raw_html,
config=CrawlerRunConfig(
extraction_strategy=JsonCssExtractionStrategy(schema)
)
)
raw_html = "<div class='item'><h2>Item 1</h2><a href='/item1'>Link</a></div>"

result = await crawler.arun(
    url="raw://" + raw_html,
    config=CrawlerRunConfig(
        extraction_strategy=JsonCssExtractionStrategy(schema)
    )
)
Python
브라우저를 띄우지 않고 전달된 HTML을 직접 파싱한다. 이미 HTML을 갖고 있는데 추출 로직만 테스트하고 싶을 때 편하다.

LLM 기반 구조화 추출 — CSS로 안 되면 AI한테 맡기기
CSS 셀렉터로 깔끔하게 안 잡히는 페이지가 있다. 레이아웃이 불규칙하거나, 데이터가 자연어 속에 섞여 있거나. 이럴 때 LLMExtractionStrategy가 쓸모 있다. Pydantic 모델로 원하는 구조를 정의하면 LLM이 페이지를 읽고 그 구조대로 JSON을 만들어준다.

import os
from pydantic import BaseModel, Field
from crawl4ai import (
AsyncWebCrawler, CrawlerRunConfig, BrowserConfig,
LLMConfig, LLMExtractionStrategy, CacheMode
)

class ModelPricing(BaseModel):
model_name: str = Field(..., description="AI 모델명")
input_fee: str = Field(..., description="입력 토큰 요금")
output_fee: str = Field(..., description="출력 토큰 요금")

crawler_config = CrawlerRunConfig(
cache_mode=CacheMode.BYPASS,
extraction_strategy=LLMExtractionStrategy(
llm_config=LLMConfig(
provider="openai/gpt-4o",
api_token=os.getenv("OPENAI_API_KEY")
),
schema=ModelPricing.model_json_schema(),
extraction_type="schema",
instruction="모든 AI 모델명과 입력/출력 토큰 요금을 추출하세요",
extra_args={"temperature": 0, "max_tokens": 2000}
)
)

async with AsyncWebCrawler() as crawler:
result = await crawler.arun(
url="https://openai.com/api/pricing/",
config=crawler_config
)
print(result.extracted_content)
import os
from pydantic import BaseModel, Field
from crawl4ai import (
    AsyncWebCrawler, CrawlerRunConfig, BrowserConfig,
    LLMConfig, LLMExtractionStrategy, CacheMode
)

class ModelPricing(BaseModel):
    model_name: str = Field(..., description="AI 모델명")
    input_fee: str = Field(..., description="입력 토큰 요금")
    output_fee: str = Field(..., description="출력 토큰 요금")

crawler_config = CrawlerRunConfig(
    cache_mode=CacheMode.BYPASS,
    extraction_strategy=LLMExtractionStrategy(
        llm_config=LLMConfig(
            provider="openai/gpt-4o",
            api_token=os.getenv("OPENAI_API_KEY")
        ),
        schema=ModelPricing.model_json_schema(),
        extraction_type="schema",
        instruction="모든 AI 모델명과 입력/출력 토큰 요금을 추출하세요",
        extra_args={"temperature": 0, "max_tokens": 2000}
    )
)

async with AsyncWebCrawler() as crawler:
    result = await crawler.arun(
        url="https://openai.com/api/pricing/",
        config=crawler_config
    )
    print(result.extracted_content)
Python
provider는 openai/gpt-4o, anthropic/claude-3-sonnet, ollama/llama3.3 같은 형식이다. Ollama를 쓰면 api_token 없이 로컬에서 무료로 돌릴 수 있다. extraction_type="schema"가 Pydantic JSON Schema를 LLM에게 넘겨서 정해진 구조대로 응답을 받게 한다.

당연히 CSS 추출보다 느리고, 외부 API를 쓰면 비용도 든다. 그래서 실무에서는 CSS를 기본으로 깔고, CSS로 안 되는 페이지만 LLM 추출을 붙이는 식으로 섞어 쓰는 게 현실적이다.

JavaScript 동적 콘텐츠 처리 — 클릭해야 나오는 데이터
Crawl4AI 동적 렌더링과 스텔스 모드 — js_code 주입, wait_for 명시적 대기, session_id 세션 유지, enable_stealth 봇 탐지 우회 4단계 흐름
SPA나 동적 페이지에서는 JavaScript를 실행해야 데이터가 보인다. js_code 파라미터에 실행할 JS를 넘기면 된다.

from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode

js_click_tabs = """
(async () => {
const tabs = document.querySelectorAll(".tab-menu > div");
for (let tab of tabs) {
tab.scrollIntoView();
tab.click();
await new Promise(r => setTimeout(r, 500));
}
})();
"""

config = CrawlerRunConfig(
cache_mode=CacheMode.BYPASS,
js_code=[js_click_tabs],
wait_for="css:.tab-content-loaded",
page_timeout=60000
)

async with AsyncWebCrawler(
config=BrowserConfig(headless=True, java_script_enabled=True)
) as crawler:
result = await crawler.arun(
url="https://example.com/tabbed-page",
config=config
)
print(result.markdown)
from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode

js_click_tabs = """
(async () => {
    const tabs = document.querySelectorAll(".tab-menu > div");
    for (let tab of tabs) {
        tab.scrollIntoView();
        tab.click();
        await new Promise(r => setTimeout(r, 500));
    }
})();
"""

config = CrawlerRunConfig(
    cache_mode=CacheMode.BYPASS,
    js_code=[js_click_tabs],
    wait_for="css:.tab-content-loaded",
    page_timeout=60000
)

async with AsyncWebCrawler(
    config=BrowserConfig(headless=True, java_script_enabled=True)
) as crawler:
    result = await crawler.arun(
        url="https://example.com/tabbed-page",
        config=config
    )
    print(result.markdown)
Python
js_code에 넣은 JavaScript가 탭을 하나씩 클릭하면서 숨겨진 콘텐츠를 로드한다. wait_for="css:.tab-content-loaded"는 지정한 셀렉터가 DOM에 나타날 때까지 기다리라는 뜻이다. "js:document.querySelector('.data').children.length > 10" 같은 JS 조건식도 쓸 수 있다.

한 가지 더. session_id를 지정하면 여러 arun() 호출 사이에 쿠키와 로그인 상태가 유지된다. 로그인 필요한 페이지를 크롤링하거나, 무한 스크롤에서 스크롤+추출을 반복할 때 쓴다.

딥 크롤링 — 페이지 하나가 아니라 사이트 전체가 필요할 때
Crawl4AI 딥 크롤링 내비게이션 전략 3종 — BFS 너비 우선, DFS 깊이 우선, BestFirst 관련성 우선 크롤링 전략 시각화
한 페이지만 긁는 게 아니라 사이트를 통째로 돌아야 할 때가 있다. 기술 문서 전체를 인덱싱한다거나 뉴스 아카이브를 모은다거나. Crawl4AI는 BFS(너비 우선), DFS(깊이 우선), BestFirst(관련성 우선) 세 가지 전략을 제공한다.

from crawl4ai import AsyncWebCrawler, CrawlerRunConfig
from crawl4ai.deep_crawling import BFSDeepCrawlStrategy

config = CrawlerRunConfig(
deep_crawl_strategy=BFSDeepCrawlStrategy(
max_depth=2,
include_external=False,
max_pages=50
),
verbose=True
)

async with AsyncWebCrawler() as crawler:
results = await crawler.arun("https://docs.example.com", config=config)
print(f"총 {len(results)}개 페이지 크롤링 완료")

for result in results[:5]:
depth = result.metadata.get("depth", 0)
print(f"[깊이 {depth}] {result.url}")
from crawl4ai import AsyncWebCrawler, CrawlerRunConfig
from crawl4ai.deep_crawling import BFSDeepCrawlStrategy

config = CrawlerRunConfig(
    deep_crawl_strategy=BFSDeepCrawlStrategy(
        max_depth=2,
        include_external=False,
        max_pages=50
    ),
    verbose=True
)

async with AsyncWebCrawler() as crawler:
    results = await crawler.arun("https://docs.example.com", config=config)
    print(f"총 {len(results)}개 페이지 크롤링 완료")

    for result in results[:5]:
        depth = result.metadata.get("depth", 0)
        print(f"[깊이 {depth}] {result.url}")
Python
BFSDeepCrawlStrategy가 시작 URL에서 발견된 링크를 레벨별로 따라간다. max_depth=2면 2단계 깊이까지, max_pages=50은 최대 50페이지까지만 크롤링하는 안전장치다.

전략	탐색 방식	적합한 상황
BFS	같은 깊이의 모든 링크를 먼저 탐색	사이트 전체를 골고루 수집할 때
DFS	한 경로를 끝까지 따라간 뒤 백트래킹	특정 섹션을 깊게 파고들 때
BestFirst	키워드 관련성 점수가 높은 링크부터 탐색	특정 주제 관련 페이지만 선별할 때
BestFirst가 개인적으로 가장 자주 쓰는 전략인데, KeywordRelevanceScorer와 같이 쓰면 원하는 주제의 페이지부터 먼저 크롤링된다.

from crawl4ai.deep_crawling import BestFirstCrawlingStrategy
from crawl4ai.deep_crawling.scorers import KeywordRelevanceScorer

scorer = KeywordRelevanceScorer(
keywords=["API", "authentication", "quickstart"],
weight=0.7
)

config = CrawlerRunConfig(
deep_crawl_strategy=BestFirstCrawlingStrategy(
max_depth=2,
include_external=False,
url_scorer=scorer,
max_pages=25
),
stream=True
)

async with AsyncWebCrawler() as crawler:
async for result in await crawler.arun(
"https://docs.example.com", config=config
):
score = result.metadata.get("score", 0)
print(f"[점수 {score:.2f}] {result.url}")
from crawl4ai.deep_crawling import BestFirstCrawlingStrategy
from crawl4ai.deep_crawling.scorers import KeywordRelevanceScorer

scorer = KeywordRelevanceScorer(
    keywords=["API", "authentication", "quickstart"],
    weight=0.7
)

config = CrawlerRunConfig(
    deep_crawl_strategy=BestFirstCrawlingStrategy(
        max_depth=2,
        include_external=False,
        url_scorer=scorer,
        max_pages=25
    ),
    stream=True
)

async with AsyncWebCrawler() as crawler:
    async for result in await crawler.arun(
        "https://docs.example.com", config=config
    ):
        score = result.metadata.get("score", 0)
        print(f"[점수 {score:.2f}] {result.url}")
Python
KeywordRelevanceScorer가 URL과 콘텐츠에서 키워드 관련성을 0~1로 매긴다. weight=0.7이면 이 점수가 순위의 70%를 차지한다는 뜻이다. stream=True를 걸면 전체가 끝날 때까지 안 기다리고, 페이지 하나 끝날 때마다 결과가 날아온다.

딥 크롤링 고급 기능 — 필터 체인과 장애 복구
실전에서는 “guide”나 “tutorial”이 URL에 들어간 것만 긁고 싶다거나, 크롤링 도중에 네트워크가 끊겨서 처음부터 다시 돌려야 하는 상황이 생긴다. FilterChain과 상태 저장 콜백이 이걸 해결해준다.

from crawl4ai.deep_crawling.filters import (
FilterChain, URLPatternFilter, DomainFilter, ContentTypeFilter
)

filter_chain = FilterChain([
URLPatternFilter(patterns=["*guide*", "*tutorial*"]),
DomainFilter(
allowed_domains=["docs.example.com"],
blocked_domains=["old.docs.example.com"]
),
ContentTypeFilter(allowed_types=["text/html"])
])

config = CrawlerRunConfig(
deep_crawl_strategy=BFSDeepCrawlStrategy(
max_depth=2,
filter_chain=filter_chain
)
)
from crawl4ai.deep_crawling.filters import (
    FilterChain, URLPatternFilter, DomainFilter, ContentTypeFilter
)

filter_chain = FilterChain([
    URLPatternFilter(patterns=["*guide*", "*tutorial*"]),
    DomainFilter(
        allowed_domains=["docs.example.com"],
        blocked_domains=["old.docs.example.com"]
    ),
    ContentTypeFilter(allowed_types=["text/html"])
])

config = CrawlerRunConfig(
    deep_crawl_strategy=BFSDeepCrawlStrategy(
        max_depth=2,
        filter_chain=filter_chain
    )
)
Python
세 필터가 순서대로 적용된다. URL 패턴 → 도메인 → 콘텐츠 타입. PDF나 이미지는 ContentTypeFilter에서 걸러진다.

장시간 크롤링의 장애 복구는 Redis에 상태를 저장하는 패턴으로 해결한다.

자세히 알아보기
언어 관련 자료
데이터 관리
Java
import json
import redis.asyncio as redis
from crawl4ai.deep_crawling import BFSDeepCrawlStrategy

redis_client = redis.Redis(host="localhost", port=6379, db=0)

saved_state = None
existing = await redis_client.get("crawl4ai:state")
if existing:
saved_state = json.loads(existing)
print(f"체크포인트에서 재개: {saved_state['pages_crawled']}페이지 완료 상태")

async def persist_state(state: dict):
await redis_client.set("crawl4ai:state", json.dumps(state))

strategy = BFSDeepCrawlStrategy(
max_depth=3,
max_pages=100,
resume_state=saved_state,
on_state_change=persist_state
)
import json
import redis.asyncio as redis
from crawl4ai.deep_crawling import BFSDeepCrawlStrategy

redis_client = redis.Redis(host="localhost", port=6379, db=0)

saved_state = None
existing = await redis_client.get("crawl4ai:state")
if existing:
    saved_state = json.loads(existing)
    print(f"체크포인트에서 재개: {saved_state['pages_crawled']}페이지 완료 상태")

async def persist_state(state: dict):
    await redis_client.set("crawl4ai:state", json.dumps(state))

strategy = BFSDeepCrawlStrategy(
    max_depth=3,
    max_pages=100,
    resume_state=saved_state,
    on_state_change=persist_state
)
Python
on_state_change가 상태 변경 때마다 Redis에 저장한다. 크롤러가 죽어도 resume_state에 마지막 상태를 넣으면 이미 방문한 페이지는 건너뛰고 나머지부터 이어간다. 문서 수천 페이지를 긁어야 하는 상황이면 사실상 필수다.

병렬 크롤링 — URL 리스트를 한번에 돌리기
Crawl4AI 프로덕션 환경 탄력성과 스케일링 — MemoryAdaptiveDispatcher 적응형 병렬 처리와 Redis 상태 저장 콜백 기반 장애 복구 아키텍처
URL이 여러 개면 arun_many()를 쓴다. 내부적으로 MemoryAdaptiveDispatcher가 시스템 메모리를 보고 동시 실행 수를 조절해준다.

from crawl4ai import AsyncWebCrawler, CrawlerRunConfig, CacheMode

urls = [
"https://example.com/page1",
"https://example.com/page2",
"https://example.com/page3",
"https://example.com/page4",
"https://example.com/page5"
]

config = CrawlerRunConfig(
cache_mode=CacheMode.BYPASS,
stream=True
)

async with AsyncWebCrawler() as crawler:
async for result in await crawler.arun_many(urls, config=config):
if result.success:
print(f"[성공] {result.url}: {len(result.markdown.raw_markdown)}자")
else:
print(f"[실패] {result.url}: {result.error_message}")
from crawl4ai import AsyncWebCrawler, CrawlerRunConfig, CacheMode

urls = [
    "https://example.com/page1",
    "https://example.com/page2",
    "https://example.com/page3",
    "https://example.com/page4",
    "https://example.com/page5"
]

config = CrawlerRunConfig(
    cache_mode=CacheMode.BYPASS,
    stream=True
)

async with AsyncWebCrawler() as crawler:
    async for result in await crawler.arun_many(urls, config=config):
        if result.success:
            print(f"[성공] {result.url}: {len(result.markdown.raw_markdown)}자")
        else:
            print(f"[실패] {result.url}: {result.error_message}")
Python
stream=True면 끝나는 순서대로 결과가 온다. stream=False(기본값)면 전부 끝날 때까지 기다렸다가 한꺼번에 돌아온다.

URL마다 다른 설정이 필요하면 url_matcher를 쓴다.

configs = [
CrawlerRunConfig(
url_matcher="*.pdf",
cache_mode=CacheMode.ENABLED
),
CrawlerRunConfig(
url_matcher=["*/blog/*", "*/article/*"],
word_count_threshold=200
),
CrawlerRunConfig() # 나머지 URL에 적용되는 기본 설정
]

results = await crawler.arun_many(urls, config=configs)
configs = [
    CrawlerRunConfig(
        url_matcher="*.pdf",
        cache_mode=CacheMode.ENABLED
    ),
    CrawlerRunConfig(
        url_matcher=["*/blog/*", "*/article/*"],
        word_count_threshold=200
    ),
    CrawlerRunConfig()  # 나머지 URL에 적용되는 기본 설정
]

results = await crawler.arun_many(urls, config=configs)
Python
CrawlerRunConfig 리스트를 넘기면 URL별로 매칭되는 설정이 적용된다. PDF는 캐시를 켜고, 블로그 페이지는 200단어 이상 블록만 남기는 식이다.

CrawlResult 뜯어보기 — 마크다운 말고 뭐가 더 있나
arun()이 돌려주는 CrawlResult에는 마크다운 말고도 쓸만한 게 꽤 있다.

필드	설명
markdown.raw_markdown	필터링 없는 전체 마크다운
markdown.fit_markdown	콘텐츠 필터 적용 후 마크다운
markdown.markdown_with_citations	링크 참조가 포함된 마크다운
html	원본 HTML
cleaned_html	스크립트/제외 태그 제거된 HTML
extracted_content	추출 전략 결과 (JSON 문자열)
links	내부/외부 링크 목록
media	이미지, 비디오, 오디오 목록
metadata	페이지 메타데이터 (제목, 저자 등)
screenshot	Base64 인코딩된 스크린샷 (옵션)
network_requests	캡처된 HTTP 요청/응답 목록
links가 {"internal": [...], "external": [...]}로 나뉘어 있고, 각 링크에 href, text, context(주변 텍스트)가 붙어있다. RAG에서 출처 추적할 때 context가 꽤 쓸모 있다. media도 비슷하게 이미지의 src, alt, 관련성 score를 갖고 있다.

어떤 상황에 뭘 써야 하나 — 시나리오별 조합
Crawl4AI API 시나리오 매트릭스 치트시트 — RAG 데이터 수집, 상품 가격 트래킹, 기술 문서 인덱싱, 동적 SPA 크롤링 유스케이스별 권장 API 조합과 핵심 효과
API가 많으니까 헷갈릴 수 있다. 상황별로 뭘 쓰면 되는지 정리해봤다.

시나리오	핵심 API	권장 설정
RAG 데이터 수집	arun() + PruningContentFilter	fit_markdown 사용, 광고/네비 제거
상품 가격 모니터링	JsonCssExtractionStrategy	CSS 셀렉터로 가격/이름 추출
뉴스 아카이빙	BFSDeepCrawlStrategy + arun_many()	도메인 제한, 스트리밍 모드
기술 문서 인덱싱	BestFirstCrawlingStrategy	키워드 스코어러로 관련 페이지 우선 크롤링
비정형 데이터 추출	LLMExtractionStrategy	Pydantic 스키마 정의, Ollama로 비용 절감
동적 SPA 크롤링	js_code + wait_for + session_id	JS 실행 후 DOM 변경 대기
