from __future__ import annotations

from PySide6 import QtGui, QtWidgets

from stock_data.gui.font_policy import (
    KOREAN_GLYPH_PROBE,
    configure_application_font,
    font_supports_korean,
)


def test_application_font_policy_resolves_every_required_korean_glyph() -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    result = configure_application_font(app)

    assert result.glyphs_supported
    assert result.family
    assert font_supports_korean(app.font())
    assert all(
        QtGui.QFontMetrics(app.font()).inFontUcs4(ord(character))
        for character in KOREAN_GLYPH_PROBE
    )
