"""
Notes panel — a PDF reader that sits beside the main overlay.

It is a separate window from the LLM/screenshot overlay, so the two move
together but are used independently: you scroll your notes while the
overlay still answers screenshots and PC audio.

Like the overlay it is hidden from screen capture, stays above other
windows (KWin on-screen-display layer) and never steals keyboard focus —
so it does not pull focus away from the call you are on. Unlike the
overlay it is NOT click-through, so it can receive a dropped PDF.
Scrolling is driven by the overlay's global hotkeys, not the mouse.
"""

from __future__ import annotations

import logging
import os
from typing import List, Optional

from PySide6.QtCore import QMargins, QPointF, Qt
from PySide6.QtWidgets import QLabel, QStackedWidget, QVBoxLayout, QWidget
from PySide6.QtPdf import QPdfDocument
from PySide6.QtPdfWidgets import QPdfView

from ui.privacy_window import PrivacyWindow

logger = logging.getLogger(__name__)

WINDOW_TITLE = "Hindsight Notes"


class PdfPanel(PrivacyWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(WINDOW_TITLE)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus  # never take focus from the call
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        # Opaque so the PDF is readable over whatever is behind it.
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setAcceptDrops(True)  # a dropped PDF is how a document gets loaded

        self._doc = QPdfDocument(self)
        self._name = ""
        self._build_ui()
        self._resize_to_screen()

    # -- UI -----------------------------------------------------------

    def _build_ui(self) -> None:
        self.setStyleSheet(
            "PdfPanel { background-color: #18181b; "
            "border: 1px solid rgba(255,255,255,40); }"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.setSpacing(0)

        self._title = QLabel("📄  Notes")
        self._title.setStyleSheet(
            "color:#e4e4e7; font-size:13px; font-weight:bold; "
            "padding:7px 11px; background-color:#18181b; "
            "border-bottom:1px solid #3f3f46;"
        )
        layout.addWidget(self._title)

        self._stack = QStackedWidget()
        layout.addWidget(self._stack, 1)

        hint = QLabel(
            "Drag a PDF here\n\n"
            "Ctrl+W / Ctrl+S — previous / next page\n"
            "Ctrl+Shift+Arrows — move this panel"
        )
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setStyleSheet("color:#71717a; font-size:15px; padding:30px; line-height:160%;")
        self._stack.addWidget(hint)  # index 0: empty state

        self._view = QPdfView(self)
        self._view.setDocument(self._doc)
        self._view.setPageMode(QPdfView.PageMode.SinglePage)  # one page at a time
        self._view.setZoomMode(QPdfView.ZoomMode.FitToWidth)  # page fills panel width
        self._view.setDocumentMargins(QMargins(6, 6, 6, 6))
        # Panel is sized to the page, so no scrollbars are needed; hiding them
        # also removes the thin scrollbar strip beside the page.
        self._view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._view.pageNavigator().currentPageChanged.connect(self._on_page_changed)
        # Match the LLM overlay's background (rgb(24,24,27))
        self._view.setStyleSheet("QPdfView { background-color:#18181b; border:none; }")
        self._view.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._stack.addWidget(self._view)  # index 1: document

    def _resize_to_screen(self) -> None:
        from ui.privacy_window import primary_screen

        screen = primary_screen()
        avail = screen.availableGeometry() if screen else None
        # Wider so the page (fit-to-width) renders big enough to read comfortably.
        if avail:
            width = min(680, max(460, avail.width() // 3))
            height = min(1000, avail.height() - 40)
        else:
            width, height = 660, 820
        self.resize(width, max(360, height))

    # -- document -----------------------------------------------------

    def has_document(self) -> bool:
        return self._doc.status() == QPdfDocument.Status.Ready and self._doc.pageCount() > 0

    def load_pdf(self, path: str) -> bool:
        error = self._doc.load(path)
        if error != QPdfDocument.Error.None_:
            logger.warning(f"Could not open PDF '{path}': {error}")
            self._title.setText("📄  Could not open that PDF")
            self._stack.setCurrentIndex(0)
            return False
        self._name = os.path.basename(path)
        self._stack.setCurrentIndex(1)
        self._fit_height_to_page()
        nav = self._view.pageNavigator()
        nav.jump(0, QPointF(0, 0), 0)
        self._update_title()
        logger.info(f"Opened notes PDF '{self._name}' ({self._doc.pageCount()} pages)")
        return True

    def _fit_height_to_page(self) -> None:
        """Size the panel to the first page's aspect ratio so a single page
        fills it with no empty strip above or below."""
        if self._doc.pageCount() <= 0:
            return
        size = self._doc.pagePointSize(0)
        if size.width() <= 0 or size.height() <= 0:
            return
        from ui.privacy_window import primary_screen

        content_w = self.width() - 2  # window frame
        page_h = content_w * (size.height() / size.width())
        title_h = self._title.sizeHint().height()
        wanted = int(title_h + page_h + 16)  # + document margins
        screen = primary_screen()
        cap = (screen.availableGeometry().height() - 40) if screen else 1000
        self.resize(self.width(), min(wanted, cap))

    def _update_title(self) -> None:
        if not self.has_document():
            return
        page = self._view.pageNavigator().currentPage() + 1
        total = self._doc.pageCount()
        suffix = f"  —  {page}/{total}" if total > 1 else ""
        self._title.setText(f"📄  {self._name}{suffix}")

    def _on_page_changed(self, _page: int) -> None:
        self._update_title()

    # -- page navigation (driven by the overlay's global hotkeys) -----

    def next_page(self) -> None:
        self._go(1)

    def prev_page(self) -> None:
        self._go(-1)

    def _go(self, delta: int) -> None:
        if self._stack.currentIndex() != 1:
            return
        nav = self._view.pageNavigator()
        target = nav.currentPage() + delta
        if 0 <= target < self._doc.pageCount():
            nav.jump(target, QPointF(0, 0), 0)

    # -- drag & drop --------------------------------------------------

    @staticmethod
    def _pdf_paths(mime) -> List[str]:
        paths = []
        if mime.hasUrls():
            for url in mime.urls():
                p = url.toLocalFile()
                if p and p.lower().endswith(".pdf") and os.path.isfile(p):
                    paths.append(p)
        return paths

    def dragEnterEvent(self, event) -> None:
        if self._pdf_paths(event.mimeData()):
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        self.dragEnterEvent(event)

    def dropEvent(self, event) -> None:
        paths = self._pdf_paths(event.mimeData())
        if paths:
            self.load_pdf(paths[0])
            event.acceptProposedAction()
        else:
            event.ignore()
