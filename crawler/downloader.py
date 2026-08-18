from __future__ import annotations

import asyncio
import re
import ssl
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote

import aiohttp
import truststore

from .board import DownloadCandidate
from .config import Config

FILENAME_STAR_RE = re.compile(r"filename\*=(?:UTF-8'')?([^;]+)", re.I)
FILENAME_RE = re.compile(r'filename="?([^";]+)"?', re.I)

# Content-Type이 application/octet-stream 등으로 뭉뚱그려지는 사이트가 많아,
# Content-Disposition 파일명에서 확장자를 못 뽑았을 때만 이 표로 보조 판별한다.
CONTENT_TYPE_TO_EXT = {
    "application/pdf": "pdf",
    "application/x-hwp": "hwp",
    "application/haansofthwp": "hwp",
    "application/vnd.hancom.hwp": "hwp",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
    "application/msword": "doc",
}


def build_client_session(cfg: Config) -> aiohttp.ClientSession:
    """첨부파일 다운로드에 쓰는 aiohttp 세션을 만든다. OS 트러스트스토어로 SSL
    검증한다 — python.org 빌드가 자체 CA 번들만 써서 서버가 중간 인증서를 안 보내는
    사이트에서 인증서 체인 오류가 나는 걸 막는다 (Windows/macOS/Linux 모두 지원).
    generic 파이프라인(pipeline.py)과 사이트별 스크립트(sites/*)가 이 세션 생성
    로직과 download_candidate()를 그대로 공유한다."""
    ssl_context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    connector = aiohttp.TCPConnector(limit=cfg.concurrency, ssl=ssl_context)
    timeout = aiohttp.ClientTimeout(total=cfg.request_timeout)
    headers = {"User-Agent": cfg.user_agent}
    return aiohttp.ClientSession(connector=connector, timeout=timeout, headers=headers)


@dataclass
class DownloadResult:
    url: str
    board_url: str
    title_guess: str
    status: str  # "ok" | "skipped_type" | "error" | "cached"
    saved_path: str | None = None
    extension: str | None = None
    error: str | None = None


def _parse_filename(content_disposition: str) -> str | None:
    if not content_disposition:
        return None
    m = FILENAME_STAR_RE.search(content_disposition)
    if m:
        return unquote(m.group(1).strip())
    m = FILENAME_RE.search(content_disposition)
    if m:
        raw = m.group(1).strip()
        return unquote(raw) if "%" in raw else raw
    return None


def _sanitize_filename(name: str, fallback_ext: str) -> str:
    name = (name or "").strip() or f"document.{fallback_ext}"
    name = re.sub(r'[\\/*?:"<>|\x00-\x1f]', "_", name)
    b = name.encode("utf-8")
    if len(b) > 200:
        stem, _, ext = name.rpartition(".")
        stem_b = stem.encode("utf-8")[:190].decode("utf-8", errors="ignore")
        name = f"{stem_b}.{ext}" if ext else stem_b
    return name


def _unique_path(board_dir: Path, filename: str) -> Path:
    path = board_dir / filename
    if not path.exists():
        return path
    stem, dot, ext = filename.rpartition(".")
    suffix = f"_{abs(hash(filename)) % 10000}"
    return board_dir / (f"{stem}{suffix}.{ext}" if dot else f"{filename}{suffix}")


async def download_candidate(
    session: aiohttp.ClientSession,
    candidate: DownloadCandidate,
    cfg: Config,
    sem: asyncio.Semaphore,
    board_dir: Path,
    seen_urls: set[str],
) -> DownloadResult:
    if candidate.href in seen_urls:
        return DownloadResult(
            candidate.href, candidate.board_url, candidate.link_text, "cached"
        )

    async with sem:
        last_error: str | None = None
        # 일부 사이트(예: korean.go.kr)는 첨부 다운로드 URL에 Referer 검사를 걸어
        # 직접 접근을 403으로 막는다 (실측 확인) — 게시글 상세 페이지 URL을
        # Referer로 실어 보내면 통과한다.
        req_headers = {"Referer": candidate.referer} if candidate.referer else None
        for attempt in range(3):
            try:
                async with session.get(
                    candidate.href, headers=req_headers, allow_redirects=True
                ) as resp:
                    if resp.status != 200:
                        last_error = f"HTTP {resp.status}"
                        if attempt < 2:
                            await asyncio.sleep(1.5 * (attempt + 1))
                            continue
                        return DownloadResult(
                            candidate.href, candidate.board_url, candidate.link_text,
                            "error", error=last_error,
                        )

                    cd = resp.headers.get("Content-Disposition", "")
                    filename = _parse_filename(cd)
                    ext = filename.rsplit(".", 1)[-1].lower() if filename and "." in filename else None

                    if not ext or ext not in cfg.ALLOWED_EXTENSIONS:
                        ctype = resp.headers.get("Content-Type", "").split(";")[0].strip().lower()
                        ext = CONTENT_TYPE_TO_EXT.get(ctype, ext)

                    if not ext or ext not in cfg.ALLOWED_EXTENSIONS:
                        return DownloadResult(
                            candidate.href, candidate.board_url, candidate.link_text,
                            "skipped_type", extension=ext, error="허용되지 않은 파일 형식",
                        )

                    if not filename:
                        filename = f"{candidate.link_text or 'document'}.{ext}"
                    filename = _sanitize_filename(filename, fallback_ext=ext)

                    board_dir.mkdir(parents=True, exist_ok=True)
                    path = _unique_path(board_dir, filename)

                    data = await resp.read()
                    path.write_bytes(data)
                    seen_urls.add(candidate.href)
                    return DownloadResult(
                        candidate.href, candidate.board_url, candidate.link_text,
                        "ok", saved_path=str(path), extension=ext,
                    )
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                last_error = str(e)
                if attempt < 2:
                    await asyncio.sleep(1.5 * (attempt + 1))
                    continue
        return DownloadResult(
            candidate.href, candidate.board_url, candidate.link_text,
            "error", error=last_error,
        )
