from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import CONVERTIBLE_EXTENSIONS

_md_converter: Any = None


def _get_converter() -> Any:
    global _md_converter
    if _md_converter is None:
        from markitdown import MarkItDown

        _md_converter = MarkItDown()
    return _md_converter


def convert_to_markdown(
    src_path: Path, md_dir: Path, source_url: str, title: str
) -> tuple[bool, str | None]:
    """다운로드된 문서를 markdown으로 변환한다. HWP/HWPX는 지원 대상에서 제외(원본만 보관)."""
    ext = src_path.suffix.lower().lstrip(".")
    if ext not in CONVERTIBLE_EXTENSIONS:
        return False, f"변환 미지원 형식: .{ext} (원본 파일만 보관)"

    try:
        converter = _get_converter()
        result = converter.convert(str(src_path))
        text = result.text_content or ""
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"

    if not text.strip():
        return False, "변환 결과가 비어 있음"

    md_dir.mkdir(parents=True, exist_ok=True)
    out_path = md_dir / f"{src_path.stem}.md"
    header = (
        "---\n"
        f"title: {title or src_path.stem}\n"
        f"source_url: {source_url}\n"
        f"source_file: {src_path.name}\n"
        f"converted_at: {datetime.now(timezone.utc).isoformat()}\n"
        "---\n\n"
    )
    out_path.write_text(header + text, encoding="utf-8")
    return True, str(out_path)
