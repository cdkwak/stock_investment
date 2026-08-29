from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from PySide6 import QtGui, QtWidgets


KOREAN_GLYPH_PROBE = "한글갱신관심종목…"

_PREFERRED_FAMILIES = (
    "Malgun Gothic",
    "맑은 고딕",
    "Noto Sans CJK KR",
    "Noto Sans KR",
    "Apple SD Gothic Neo",
)

_SYSTEM_FONT_FILES = (
    Path("C:/Windows/Fonts/malgun.ttf"),
    Path("C:/Windows/Fonts/malgunbd.ttf"),
    Path("/System/Library/Fonts/AppleSDGothicNeo.ttc"),
    Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    Path("/usr/share/fonts/truetype/noto/NotoSansKR-Regular.ttf"),
)


@dataclass(frozen=True)
class FontPolicyResult:
    family: str
    loaded_font_files: tuple[str, ...]
    glyphs_supported: bool


def font_supports_korean(font: QtGui.QFont) -> bool:
    metrics = QtGui.QFontMetrics(font)
    return all(metrics.inFontUcs4(ord(character)) for character in KOREAN_GLYPH_PROBE)


def _font_for_family(family: str, point_size: float) -> QtGui.QFont:
    font = QtGui.QFont(family)
    if point_size > 0:
        font.setPointSizeF(point_size)
    font.setStyleStrategy(QtGui.QFont.StyleStrategy.PreferAntialias)
    return font


def explicit_point_font(
    font: QtGui.QFont,
    *,
    fallback: QtGui.QFont | None = None,
) -> QtGui.QFont:
    """Return a detached QFont with an explicit, positive point size."""

    effective_point_size = font.pointSizeF()
    if effective_point_size <= 0 and fallback is not None:
        effective_point_size = fallback.pointSizeF()
    if effective_point_size <= 0:
        raise ValueError("font must expose a positive point size")
    result = QtGui.QFont(font)
    result.setPointSizeF(effective_point_size)
    return result


def configure_application_font(
    app: QtWidgets.QApplication,
    *,
    preferred_families: Iterable[str] = _PREFERRED_FAMILIES,
    font_files: Iterable[Path] = _SYSTEM_FONT_FILES,
) -> FontPolicyResult:
    """Install one Korean-capable application font or fail before rendering tofu."""

    current = app.font()
    if font_supports_korean(current):
        return FontPolicyResult(current.family(), (), True)

    point_size = current.pointSizeF()
    available = frozenset(QtGui.QFontDatabase.families())
    for family in preferred_families:
        if family not in available:
            continue
        candidate = _font_for_family(family, point_size)
        if font_supports_korean(candidate):
            app.setFont(candidate)
            return FontPolicyResult(family, (), True)

    loaded_files: list[str] = []
    loaded_families: list[str] = []
    for font_path in font_files:
        path = Path(font_path)
        if not path.is_file():
            continue
        font_id = QtGui.QFontDatabase.addApplicationFont(str(path))
        if font_id < 0:
            continue
        loaded_files.append(str(path))
        loaded_families.extend(QtGui.QFontDatabase.applicationFontFamilies(font_id))

    for family in (*preferred_families, *loaded_families):
        candidate = _font_for_family(family, point_size)
        if font_supports_korean(candidate):
            app.setFont(candidate)
            return FontPolicyResult(family, tuple(loaded_files), True)

    raise RuntimeError("Korean UI font unavailable: refusing to render unreadable text")


__all__ = [
    "FontPolicyResult",
    "KOREAN_GLYPH_PROBE",
    "configure_application_font",
    "explicit_point_font",
    "font_supports_korean",
]
