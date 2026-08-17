from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv

from gdi_crawler.config import Config
from gdi_crawler.pipeline import run_pipeline


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="공공기관 홈페이지 문서(PDF/HWP/DOCX 등) 대량 다운로드 + Markdown 변환 파이프라인"
    )
    p.add_argument("--base-url", required=True, help="사이트 루트 URL (예: https://gdi.re.kr)")
    p.add_argument("--start-path", default="/main", help="메뉴를 탐색할 시작 페이지 경로")
    p.add_argument(
        "--boards", nargs="*", default=None,
        help="자동 메뉴 탐색 대신 직접 지정할 게시판 URL 목록 (지정 시 1단계 메뉴 탐색을 건너뜀)",
    )
    p.add_argument("--output-dir", default="./output")
    p.add_argument("--max-pages", type=int, default=5, help="게시판별 최대 크롤링 페이지 수")
    p.add_argument("--concurrency", type=int, default=8, help="동시 다운로드/요청 수")
    p.add_argument("--no-llm", action="store_true", help="LLM 없이 규칙 기반 휴리스틱만으로 실행")
    p.add_argument(
        "--llm-provider", default=os.getenv("LLM_PROVIDER", "anthropic"),
        choices=["anthropic", "openai"],
    )
    p.add_argument("--min-menu-relevance", type=float, default=0.5)
    p.add_argument(
        "--min-board-confidence", type=float, default=0.6,
        help="게시판 자체를 '관련 문서 게시판'으로 확정하기 위한 최소 confidence",
    )
    p.add_argument(
        "--limit-downloads-per-board", type=int, default=None,
        help="확정된 게시판마다 내려받을 파일 수 상한 (테스트용, 예: 1)",
    )
    return p.parse_args()


def main() -> None:
    load_dotenv()
    args = parse_args()
    cfg = Config(
        base_url=args.base_url,
        start_path=args.start_path,
        output_dir=Path(args.output_dir),
        max_pages_per_board=args.max_pages,
        concurrency=args.concurrency,
        use_llm=not args.no_llm,
        llm_provider=args.llm_provider,
        min_menu_relevance=args.min_menu_relevance,
        min_board_confidence=args.min_board_confidence,
        limit_downloads_per_board=args.limit_downloads_per_board,
    )
    asyncio.run(run_pipeline(cfg, board_urls=args.boards))


if __name__ == "__main__":
    main()
