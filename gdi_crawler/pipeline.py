from __future__ import annotations

import asyncio
import json
import ssl
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlparse

import aiohttp
import truststore
from crawl4ai import AsyncWebCrawler, BrowserConfig

from .board import DownloadCandidate, confirm_board_relevance, crawl_board, looks_like_direct_download
from .config import Config
from .converter import convert_to_markdown
from .downloader import download_candidate
from .menu import discover_document_boards


def _load_manifest(cfg: Config) -> list[dict]:
    if cfg.manifest_path.exists():
        return json.loads(cfg.manifest_path.read_text(encoding="utf-8"))
    return []


def _save_manifest(cfg: Config, records: list[dict]) -> None:
    cfg.manifest_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.manifest_path.write_text(
        json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _board_dir_name(board_url: str) -> str:
    path = urlparse(board_url).path.rstrip("/")
    name = path.rsplit("/", 1)[-1] if path else "root"
    return name or "root"


async def run_pipeline(cfg: Config, board_urls: list[str] | None = None) -> None:
    manifest = _load_manifest(cfg)
    seen_urls = {r["url"] for r in manifest if r.get("status") == "ok"}
    verbose = cfg.limit_downloads_per_board is not None

    # --- 1~2단계: crawl4ai(헤드리스 브라우저)로 메뉴/게시판 HTML을 수집한다.
    # JS로 목록을 렌더링하는 사이트에도 그대로 통하도록 하기 위함. ---
    browser_conf = BrowserConfig(
        headless=True, text_mode=True, verbose=False, user_agent=cfg.user_agent
    )
    all_candidates: list[DownloadCandidate] = []
    board_titles: dict[str, str] = {}
    async with AsyncWebCrawler(config=browser_conf) as crawler:
        if board_urls is None:
            print("[1/4] 메뉴 탐색 중...")
            selections = await discover_document_boards(cfg, crawler)
            board_urls = [s.href for s in selections]
            board_titles = {s.href: s.text for s in selections}
            if not board_urls:
                print("  -> 문서 게시판을 찾지 못했습니다. --boards로 직접 지정해 보세요.")
                return
            print(f"  -> 문서 게시판 {len(board_urls)}개 선정:")
            for s in selections:
                print(f"     - {s.text} ({s.href}) score={s.relevance_score:.2f} reason={s.reason!r}")

        # --- 게시판 단위 확정: 링크를 하나하나 심사하는 대신, 게시판에서 뽑힌
        # 게시글/첨부 샘플을 보고 "이 게시판 자체가 문서 게시판인가"만 LLM 1회로
        # 판단한다. 확정되면 그 게시판의 후보는 (강한/약한 매칭 구분 없이) 전부
        # 다운로드 대상이 된다. ---
        print("[2/4] 게시판 순회 및 확정 중...")
        for board_url in board_urls:
            if looks_like_direct_download(board_url):
                # 메뉴 항목이 게시판 목록이 아니라 파일 하나를 바로 내려받는
                # 링크인 경우 (예: "20년사"). 크롤링을 시도하지 않고 바로
                # 다운로드 후보로 취급한다 — 즉시-다운로드 URL을 브라우저로
                # goto()하면 crawl4ai가 실패로 처리하는 버그가 있다.
                print(f"  - {board_url}: 게시판이 아니라 파일 직접 링크로 판단 -> 바로 다운로드 대상에 추가")
                all_candidates.append(
                    DownloadCandidate(
                        href=board_url,
                        link_text=board_titles.get(board_url, ""),
                        context="",
                        board_url=board_url,
                        strong_match=True,
                    )
                )
                continue

            candidates = await crawl_board(cfg, crawler, board_url)
            if not candidates:
                print(f"  - {board_url}: 후보 없음 -> 스킵")
                continue

            is_relevant, confidence, reason = await confirm_board_relevance(
                cfg, board_url, candidates
            )
            confirmed = is_relevant and confidence >= cfg.min_board_confidence
            print(
                f"  - {board_url}: 후보 {len(candidates)}개, "
                f"확정={'예' if confirmed else '아니오'} (confidence={confidence:.2f}) "
                f"reason={reason!r}"
            )
            if not confirmed:
                continue

            board_candidates = candidates
            if cfg.limit_downloads_per_board is not None:
                board_candidates = board_candidates[: cfg.limit_downloads_per_board]
                print(
                    f"       -> --limit-downloads-per-board {cfg.limit_downloads_per_board} 적용: "
                    f"{len(candidates)}개 중 {len(board_candidates)}개만 다운로드"
                )
            if verbose:
                for c in board_candidates:
                    print(
                        f"       · [{'확실' if c.strong_match else '게시판확정'}] "
                        f"{c.link_text!r} -> {c.href}"
                    )
            all_candidates.extend(board_candidates)

    deduped: dict[str, DownloadCandidate] = {}
    for c in all_candidates:
        deduped.setdefault(c.href, c)
    all_candidates = list(deduped.values())

    # --- 3단계: 첨부파일 바이너리 다운로드는 aiohttp로 처리한다.
    # crawl4ai의 브라우저 기반 다운로드 가로채기(accept_downloads)는 이런
    # 즉시-다운로드형 URL에서 성공 여부를 신뢰할 수 없는 버그가 있어(실측 확인)
    # 제외했다. ---
    print(f"[3/4] 파일 다운로드 중... (총 {len(all_candidates)}개 후보, 중복 제거 완료)")

    # OS 트러스트스토어로 SSL 검증한다. python.org 빌드가 자체 CA 번들만 써서
    # 서버가 중간 인증서를 안 보내는 사이트에서 인증서 체인 오류가 나는 걸 막는다
    # (Windows/macOS/Linux 모두 지원).
    ssl_context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    connector = aiohttp.TCPConnector(limit=cfg.concurrency, ssl=ssl_context)
    timeout = aiohttp.ClientTimeout(total=cfg.request_timeout)
    headers = {"User-Agent": cfg.user_agent}

    async with aiohttp.ClientSession(
        connector=connector, timeout=timeout, headers=headers
    ) as session:
        sem = asyncio.Semaphore(cfg.concurrency)
        tasks = []
        for c in all_candidates:
            board_dir = cfg.raw_dir / _board_dir_name(c.board_url)
            tasks.append(download_candidate(session, c, cfg, sem, board_dir, seen_urls))
        results = await asyncio.gather(*tasks)

    new_records = [asdict(r) for r in results if r.status != "cached"]
    manifest.extend(new_records)
    _save_manifest(cfg, manifest)

    if verbose:
        for r in results:
            print(f"     · [{r.status}] {r.url}")
            if r.saved_path:
                print(f"       -> 저장: {r.saved_path}")
            if r.error:
                print(f"       -> 사유: {r.error}")

    ok_count = sum(1 for r in results if r.status == "ok")
    cached_count = sum(1 for r in results if r.status == "cached")
    other_count = len(results) - ok_count - cached_count
    print(
        f"  -> 성공 {ok_count}개, 중복(스킵) {cached_count}개, "
        f"실패/미지원형식 {other_count}개"
    )

    print("[4/4] Markdown 변환 중...")
    converted, skipped = 0, 0
    for r in results:
        if r.status != "ok" or not r.saved_path:
            continue
        src_path = Path(r.saved_path)
        md_dir = cfg.markdown_dir / _board_dir_name(r.board_url)
        success, info = convert_to_markdown(src_path, md_dir, r.url, r.title_guess)
        if success:
            converted += 1
            if verbose:
                print(f"     · 변환 완료: {src_path.name} -> {info}")
        else:
            skipped += 1
            print(f"  ! 변환 생략: {src_path.name} ({info})")
    print(f"  -> 변환 완료 {converted}개, 생략 {skipped}개")

    print(
        f"\n완료.\n  원본 파일: {cfg.raw_dir}\n  markdown: {cfg.markdown_dir}\n"
        f"  manifest: {cfg.manifest_path}"
    )
