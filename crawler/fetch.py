from __future__ import annotations

import asyncio

from crawl4ai import AsyncWebCrawler, CacheMode, CrawlerRunConfig

from .config import Config


async def fetch_html(crawler: AsyncWebCrawler, url: str, cfg: Config, retries: int = 3) -> str:
    """crawl4ai로 페이지를 가져와 원본 HTML을 반환한다.

    메뉴/게시판 목록은 BeautifulSoup 셀렉터로 파싱해야 하므로 markdown이 아니라
    result.html을 그대로 쓴다. crawl4ai는 헤드리스 브라우저 기반이라 목록을
    JS로 렌더링하는 사이트에도 그대로 통한다 (첨부파일 다운로드 자체는 별도로
    aiohttp를 쓴다 — crawl4ai 0.9.2는 다운로드 트리거 URL로 직접 이동할 때
    성공 여부를 신뢰할 수 없는 버그가 있어 실측으로 확인했다).
    """
    run_conf = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        page_timeout=cfg.request_timeout * 1000,
    )
    last_error: str | None = None
    for attempt in range(retries):
        result = await crawler.arun(url=url, config=run_conf)
        if result.success:
            return result.html
        last_error = result.error_message
        if attempt < retries - 1:
            await asyncio.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"크롤링 실패: {url} - {last_error}")
