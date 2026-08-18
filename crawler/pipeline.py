from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlparse

import aiohttp
from crawl4ai import AsyncWebCrawler, BrowserConfig

from .board import DownloadCandidate, confirm_board_relevance, crawl_board, looks_like_direct_download
from .config import PROFILE_SAMPLE_SIZE, Config
from .downloader import DownloadResult, build_client_session, download_candidate
from .fetch import fetch_html
from .menu import discover_document_boards
from .profile import (
    BoardProfile,
    analyze_pdf,
    average_page_count,
    candidate_tier,
    detect_license,
    detect_multimodal,
    extract_post_count,
    write_profile_csv,
)


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


async def _profile_board(
    cfg: Config,
    session: aiohttp.ClientSession,
    crawler: AsyncWebCrawler,
    board_url: str,
    board_name: str,
    candidates: list[DownloadCandidate],
) -> BoardProfile:
    """게시판에서 발견된 후보 중 앞 5개를 실제로 내려받아 sites/<site>/samples/<board>/
    에 저장하고, PDF 샘플을 분석해 CSV 한 행(BoardProfile)을 만든다."""
    sample = candidates[:PROFILE_SAMPLE_SIZE]
    sample_dir = cfg.samples_dir(board_name)
    sample_seen: set[str] = set()  # 매니페스트 캐시와 무관하게 항상 실제로 받아본다
    sem = asyncio.Semaphore(cfg.concurrency)
    results: list[DownloadResult] = await asyncio.gather(
        *[download_candidate(session, c, cfg, sem, sample_dir, sample_seen) for c in sample]
    )

    formats: set[str] = set()
    analyses = []
    for r in results:
        if r.status != "ok" or not r.extension:
            continue
        formats.add(r.extension.upper())
        if r.extension.lower() == "pdf" and r.saved_path:
            analyses.append(analyze_pdf(Path(r.saved_path)))

    avg_len = average_page_count(analyses)
    multimodal = detect_multimodal(analyses)
    tier, tier_reason = candidate_tier(analyses)

    try:
        list_html = await fetch_html(crawler, board_url, cfg)
    except Exception:
        list_html = ""
    post_count = extract_post_count(list_html) if list_html else None
    license_ = detect_license(list_html) if list_html else "미확인"

    return BoardProfile(
        site=cfg.site_name,
        board=board_name,
        board_url=board_url,
        download_method=cfg.download_method,
        data_format="/".join(sorted(formats)) or "미확인",
        post_count=str(post_count) if post_count is not None else "미확인",
        avg_length=f"{avg_len:.1f}" if avg_len is not None else "미확인",
        multimodal="/".join(multimodal),
        license=license_,
        tier_candidate=tier,
        tier_reason=tier_reason,
    )


async def run_pipeline(cfg: Config, board_urls: list[str] | None = None) -> None:
    manifest = _load_manifest(cfg)
    seen_urls = {r["url"] for r in manifest if r.get("status") == "ok"}
    verbose = cfg.limit_downloads_per_board is not None

    browser_conf = BrowserConfig(
        headless=True, text_mode=True, verbose=False, user_agent=cfg.user_agent
    )
    all_candidates: list[DownloadCandidate] = []
    board_titles: dict[str, str] = {}
    profiles: list[BoardProfile] = []

    # aiohttp 세션을 크롤러 블록 바깥이 아니라 감싸는 형태로 미리 열어둔다 —
    # 게시판별 프로파일링(샘플 5개 실제 다운로드)이 게시판 순회 도중 일어나므로,
    # 첨부파일 바이너리 다운로드에 쓰는 이 세션이 그 시점에도 필요하다.
    async with build_client_session(cfg) as session:
        # --- 1~2단계: crawl4ai(헤드리스 브라우저)로 메뉴/게시판 HTML을 수집한다.
        # JS로 목록을 렌더링하는 사이트에도 그대로 통하도록 하기 위함. ---
        async with AsyncWebCrawler(config=browser_conf) as crawler:
            if board_urls is None:
                print("[1/3] 메뉴 탐색 중...")
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
            # 다운로드 대상이 된다. 확정된 게시판은 곧바로 샘플 5개를 내려받아
            # 프로파일링해 CSV 한 행을 만든다. ---
            print("[2/3] 게시판 순회, 확정 및 프로파일링 중...")
            for board_url in board_urls:
                if looks_like_direct_download(board_url):
                    # 메뉴 항목이 게시판 목록이 아니라 파일 하나를 바로 내려받는
                    # 링크인 경우 (예: "20년사"). 크롤링을 시도하지 않고 바로
                    # 다운로드 후보로 취급한다 — 즉시-다운로드 URL을 브라우저로
                    # goto()하면 crawl4ai가 실패로 처리하는 버그가 있다. 게시판
                    # 목록이 없어 프로파일링 대상도 아니다.
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

                board_name = board_titles.get(board_url) or _board_dir_name(board_url)
                profile = await _profile_board(
                    cfg, session, crawler, board_url, board_name, candidates
                )
                profiles.append(profile)
                print(
                    f"       -> 프로파일: tier={profile.tier_candidate} "
                    f"avg_length={profile.avg_length} multimodal={profile.multimodal} "
                    f"license={profile.license}"
                )

        deduped: dict[str, DownloadCandidate] = {}
        for c in all_candidates:
            deduped.setdefault(c.href, c)
        all_candidates = list(deduped.values())

        # --- 3단계: 첨부파일 바이너리 다운로드는 aiohttp로 처리한다.
        # crawl4ai의 브라우저 기반 다운로드 가로채기(accept_downloads)는 이런
        # 즉시-다운로드형 URL에서 성공 여부를 신뢰할 수 없는 버그가 있어(실측 확인)
        # 제외했다. ---
        print(f"[3/3] 파일 다운로드 중... (총 {len(all_candidates)}개 후보, 중복 제거 완료)")
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

    write_profile_csv(cfg.profile_csv_path, profiles)

    print(
        f"\n완료.\n  원본 파일: {cfg.raw_dir}\n  게시판 프로파일 CSV: {cfg.profile_csv_path}\n"
        f"  manifest: {cfg.manifest_path}"
    )
