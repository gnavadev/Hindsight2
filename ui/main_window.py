"""
Main Window Implementation

Combines all UI components into the main application window with:
- Multi-modal input panel
- Response display area
- Document sidebar
- Keyboard shortcuts
"""

import logging
import re
import sys
import time
from PySide6.QtWidgets import (
    QHBoxLayout, QVBoxLayout, QLabel, QWidget, QMessageBox, QInputDialog,
    QTextBrowser, QSizePolicy, QLineEdit, QApplication
)
from PySide6.QtCore import Qt, QSize, QTimer, QThread, QObject, Signal
from PySide6.QtGui import QKeySequence, QShortcut, QPalette, QColor, QDesktopServices
import markdown
from pygments.formatters import HtmlFormatter
from ui.privacy_window import PrivacyWindow, exclude_widget_from_capture
from config.settings import Settings
from llm.smart_provider import SmartProvider, SmartGeminiProvider
from utils.hotkeys import chord_label, create_hotkey_manager, platform_mod
from utils.logging_setup import set_debug_logging

logger = logging.getLogger(__name__)


class _LlmSendWorker(QObject):
    """Runs the blocking LLM call off the UI thread so the overlay can paint."""

    finished = Signal(object, float, int)
    failed = Signal(str, float, int)
    status = Signal(str)

    def __init__(self, provider, text, images, audio_paths=None):
        super().__init__()
        self._provider = provider
        self._text = text
        self._images = images
        self._audio_paths = list(audio_paths or [])

    def run(self):
        n = len(self._images)
        t0 = time.time()
        try:
            text = self._text
            if self._audio_paths:
                self.status.emit("Transcribing PC audio…")
                parts = [self._provider.transcribe_audio(p) for p in self._audio_paths]
                transcript = "\n".join(t for t in parts if t).strip()
                logger.info(f"Transcript: {len(transcript)} chars")
                if not transcript and not self._images:
                    raise RuntimeError("No speech found in the recorded PC audio")
                text = _with_transcript(text, transcript, bool(self._images))
                self.status.emit("Sending to LLM…")
            result = self._provider.send_message(text=text, images=self._images)
            self.finished.emit(result, time.time() - t0, n)
        except Exception as e:
            logger.error(f"LLM send failed: {e}", exc_info=True)
            self.failed.emit(str(e), time.time() - t0, n)

_LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")


def _normalize_markdown(md: str) -> str:
    """LLMs write one newline where Markdown needs a blank line, so lists,
    headings, quotes and paragraphs get folded together ("1. a 2. b" on one
    line). Treat each line as its own block, except where lines belong
    together: consecutive list items, indented continuations, tables, quotes.
    Code blocks are left untouched."""
    out, prev, in_code = [], "", False
    for line in (md or "").splitlines():
        stripped = line.strip()
        if stripped.startswith(("```", "~~~")):
            if not in_code and prev.strip():
                out.append("")
            in_code = not in_code
        elif not in_code and stripped and prev.strip():
            p = prev.strip()
            together = (
                (_LIST_ITEM.match(line) and _LIST_ITEM.match(prev))
                or line.startswith((" ", "\t"))
                or (stripped.startswith("|") and p.startswith("|"))
                or (stripped.startswith(">") and p.startswith(">"))
            )
            if not together:
                out.append("")
        out.append(line)
        prev = "" if in_code and not stripped.startswith(("```", "~~~")) else line
    return "\n".join(out)


def _with_transcript(text: str, transcript: str, has_images: bool) -> str:
    if not transcript:
        return text
    block = (
        "Transcript of PC audio recorded during a conversation (what the other "
        "person said; automatic speech-to-text, may contain errors):\n"
        f'"""\n{transcript}\n"""'
    )
    if has_images:
        return f"{text}\n\n{block}\nUse the transcript as additional context."
    return (
        f"{block}\n\nIdentify the question, request or problem in what they said "
        "and give the best answer or reply to it. Format for quick reading on a "
        "small overlay, in Markdown:\n"
        "- Start with the direct answer or the reply to say, in 1-3 sentences.\n"
        "- Then at most 4 short bullet points with key details, if useful.\n"
        "- No preamble, no restating the question, no closing offers.\n"
        "- Leave a blank line between paragraphs and before lists."
    )


# ------------------------
# PySide6 v6 enum aliases
# ------------------------

_WT  = Qt.WindowType
_WA  = Qt.WidgetAttribute
_AF  = Qt.AlignmentFlag
_SBP = Qt.ScrollBarPolicy
_TM  = Qt.TransformationMode
_CR  = QPalette.ColorRole


class MainWindow(PrivacyWindow):
    """Main application window combining all UI components"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Privacy LLM Assistant")

        # 1. Window Flags
        self.setWindowFlags(
            _WT.FramelessWindowHint |
            _WT.WindowStaysOnTopHint |
            _WT.Tool |
            _WT.WindowDoesNotAcceptFocus
        )

        # 2. Transparency and click-through
        self.setAttribute(_WA.WA_TranslucentBackground)
        self.setAttribute(_WA.WA_ShowWithoutActivating)
        if sys.platform == "darwin":
            self.setAttribute(_WA.WA_MacAlwaysShowToolWindow, True)
        self.setWindowOpacity(1.0)  # text must not mix with what's behind
        self.set_click_through(True)

        # 3. Start small; expand once content is ready
        self.resize(280, 60)

        # Load settings
        self.settings = Settings()

        # Privacy / debug state
        self.privacy_enabled = True
        self.debug_mode = False
        self.current_images = []
        self.current_audio = []  # recorded PC-audio WAVs waiting to be sent
        from capture.system_audio import SystemAudioRecorder
        self.recorder = SystemAudioRecorder()
        self._rec_timer = QTimer(self)
        self._rec_timer.setInterval(1000)
        self._rec_timer.timeout.connect(self._show_capture_status)
        self._max_pages = 8
        self._send_in_progress = False
        self._llm_thread = None
        self._llm_worker = None

        # Initialize LLM provider
        self.llm_provider = None
        self._init_llm_provider()

        self._position_toast()
        self._apply_dark_theme()
        self._setup_ui()

        # Native Hotkeys
        self._hotkeys = None
        self._setup_global_hotkeys()

        logger.info("Overlay initialized")

    # ------------------------------------------------------------------
    # Positioning
    # ------------------------------------------------------------------

    def _position_toast(self):
        """Position window in top-left corner of the primary screen"""
        from ui.privacy_window import primary_screen
        screen = primary_screen()
        origin = screen.geometry().topLeft() if screen else None
        x, y = (origin.x(), origin.y()) if origin else (0, 0)
        self.move_overlay(x + 20, y + 50)

    # ------------------------------------------------------------------
    # Resize
    # ------------------------------------------------------------------

    def resizeEvent(self, event):
        """Reposition the floating status dot on resize"""
        if hasattr(self, 'status_label') and self.status_label:
            container = self.findChild(QWidget, "container")
            if container:
                x = container.width() - self.status_label.width() - 10
                self.status_label.move(x, 5)
                self.status_label.raise_()
        super().resizeEvent(event)

    # ------------------------------------------------------------------
    # Theme
    # ------------------------------------------------------------------

    def _apply_dark_theme(self):
        """Apply a modern dark theme"""
        palette = QPalette()
        palette.setColor(_CR.Window,          QColor(30, 30, 30))
        palette.setColor(_CR.WindowText,      QColor(224, 224, 224))
        palette.setColor(_CR.Base,            QColor(43, 43, 43))
        palette.setColor(_CR.AlternateBase,   QColor(53, 53, 53))
        palette.setColor(_CR.ToolTipBase,     QColor(255, 255, 220))
        palette.setColor(_CR.ToolTipText,     QColor(0, 0, 0))
        palette.setColor(_CR.Text,            QColor(224, 224, 224))
        palette.setColor(_CR.Button,          QColor(53, 53, 53))
        palette.setColor(_CR.ButtonText,      QColor(224, 224, 224))
        palette.setColor(_CR.BrightText,      QColor(255, 0, 0))
        palette.setColor(_CR.Link,            QColor(42, 130, 218))
        palette.setColor(_CR.Highlight,       QColor(42, 130, 218))
        palette.setColor(_CR.HighlightedText, QColor(0, 0, 0))
        self.setPalette(palette)

        self.setStyleSheet("""
            QToolTip {
                color: #ffffff;
                background-color: #2a82da;
                border: 1px solid white;
            }
            QWidget {
                font-family: 'Segoe UI', sans-serif;
            }
        """)

    # ------------------------------------------------------------------
    # UI Setup
    # ------------------------------------------------------------------

    def _setup_ui(self):
        """Setup the minimalist UI layout"""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 5)
        main_layout.setSpacing(2)

        # Container
        container = QWidget()
        container.setObjectName("container")
        container.setAttribute(_WA.WA_StyledBackground, True)
        container.setStyleSheet("""
            QWidget#container {
                background-color: rgb(24, 24, 27);
                border: 1px solid rgba(255, 255, 255, 30);
                border-radius: 10px;
            }
        """)
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(15, 10, 15, 5)

        # 1. Legend bar
        legend_widget = QWidget()
        legend_widget.setStyleSheet("background: transparent;")
        legend_layout = QVBoxLayout(legend_widget)
        legend_layout.setContentsMargins(0, 0, 0, 0)
        legend_layout.setSpacing(0)

        row1_layout = QHBoxLayout()
        row1_layout.setSpacing(10)
        row1_layout.setAlignment(_AF.AlignLeft)

        row2_layout = QHBoxLayout()
        row2_layout.setSpacing(10)
        row2_layout.setAlignment(_AF.AlignLeft)

        shortcuts = [
            (chord_label("H"), "Screenshot / add page"),
            (chord_label("R"), "Reset"),
            (chord_label("B"), "Hide/Show"),
            (chord_label("P"), "Privacy / Debug"),
            (chord_label("Arrows"), "Move"),
            (chord_label("]"), "Record PC audio"),
            (chord_label("Shift+Q"), "Quit"),
        ]

        for i, (keys, desc) in enumerate(shortcuts):
            lbl = QLabel(
                f"<span style='color: #4facfe;'><b>{keys}</b></span> "
                f"<span style='color: #888;'>{desc}</span>"
            )
            lbl.setStyleSheet("font-size: 9px; margin: 0px; padding: 0px;")
            (row1_layout if i < 3 else row2_layout).addWidget(lbl)

        row1_layout.addStretch()
        row2_layout.addStretch()
        legend_layout.addLayout(row1_layout)
        legend_layout.addLayout(row2_layout)
        container_layout.addWidget(legend_widget)

        # 2. Image preview strip (hidden by default; holds 1..N page thumbs)
        self.preview_container = QWidget()
        self.preview_container.setStyleSheet("background: transparent;")
        self.preview_layout = QHBoxLayout(self.preview_container)
        self.preview_layout.setContentsMargins(0, 2, 0, 2)
        self.preview_layout.setSpacing(6)
        self.preview_layout.setAlignment(_AF.AlignLeft)
        self.preview_container.hide()
        container_layout.addWidget(self.preview_container)

        # 3. Response viewer
        self.response_viewer = QTextBrowser()
        self.response_viewer.setStyleSheet("""
            QTextBrowser {
                color: #e4e4e7;
                font-size: 15px;
                background: transparent;
                border: none;
            }
        """)
        self.response_viewer.setOpenExternalLinks(True)
        self.response_viewer.setVerticalScrollBarPolicy(_SBP.ScrollBarAlwaysOff)
        self.response_viewer.setHorizontalScrollBarPolicy(_SBP.ScrollBarAlwaysOff)
        self.response_viewer.setHtml("Listening...")
        container_layout.addWidget(self.response_viewer)

        container_layout.setStretch(0, 0)
        container_layout.setStretch(1, 0)
        container_layout.setStretch(2, 1)

        main_layout.addWidget(container)

        # Status dot (floating overlay)
        self.status_label = QLabel("⬤", container)
        self.status_label.setStyleSheet(
            "color: #00ffca; font-size: 8px; background: transparent; padding: 2px;"
        )
        self.status_label.adjustSize()
        self.status_label.show()

    # ------------------------------------------------------------------
    # Global Hotkeys
    # ------------------------------------------------------------------

    def _setup_global_hotkeys(self):
        """Register global system-wide hotkeys (Windows RegisterHotKey / macOS Carbon)."""
        self._hotkeys = create_hotkey_manager(self)
        self.registered_dynamic_ids = set()
        from PySide6.QtCore import QTimer
        step = 30
        mod = platform_mod()

        def invoke(cb):
            return lambda: QTimer.singleShot(0, cb)

        bindings = [
            (101, mod, "h", invoke(self._handle_screenshot)),
            (102, mod, "b", invoke(self._toggle_visibility)),
            (103, mod, "p", invoke(self._toggle_privacy)),
            (104, mod, "up", invoke(lambda: self.move_window(0, -step))),
            (105, mod, "down", invoke(lambda: self.move_window(0, step))),
            (106, mod, "left", invoke(lambda: self.move_window(-step, 0))),
            (107, mod, "right", invoke(lambda: self.move_window(step, 0))),
            (108, mod, "r", invoke(self._handle_reset)),
            (109, f"{mod}+shift", "q", invoke(self._quit_app)),
            (110, mod, "]", invoke(self._toggle_recording)),
        ]
        # Ctrl+Arrows keep moving the overlay while held
        repeating = {104, 105, 106, 107}
        for hk_id, m, key, cb in bindings:
            self._hotkeys.register(hk_id, m, key, cb, repeat=hk_id in repeating)

    def _quit_app(self):
        logger.info("Quit requested via hotkey")
        self._discard_audio()
        from PySide6.QtWidgets import QApplication
        QApplication.quit()

    def _register_dynamic_hotkey(self, hk_id):
        if hk_id in self.registered_dynamic_ids or self._hotkeys is None:
            return
        if hk_id == 201:
            from PySide6.QtCore import QTimer
            if self._hotkeys.register(
                201, platform_mod(), "enter", lambda: QTimer.singleShot(0, self._handle_send)
            ):
                self.registered_dynamic_ids.add(hk_id)

    def _unregister_dynamic_hotkey(self, hk_id):
        if hk_id not in self.registered_dynamic_ids or self._hotkeys is None:
            return
        self._hotkeys.unregister(hk_id)
        self.registered_dynamic_ids.discard(hk_id)

    def nativeEvent(self, event_type, message):
        if self._hotkeys is not None:
            handled = self._hotkeys.handle_native_event(event_type, message)
            if handled is not None:
                return handled
        return super().nativeEvent(event_type, message)

    def _setup_shortcuts(self):
        """Deprecated: handled by global hotkeys"""
        pass

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _handle_screenshot(self):
        try:
            from capture.screenshot import capture_screenshot
            from PySide6.QtWidgets import QApplication
            import time as _time

            if len(self.current_images) >= self._max_pages:
                self.response_viewer.setHtml(
                    f"Already have {self._max_pages} pages. "
                    f"<b>{chord_label('Enter')}</b> to send or "
                    f"<b>{chord_label('R')}</b> to reset."
                )
                from PySide6.QtCore import QTimer
                QTimer.singleShot(50, self.adjust_height_to_content)
                return

            center = self.overlay_center()

            if self.capture_excluded_by_compositor():
                # KWin already leaves every overlay window out of the capture;
                # hiding it would only make it flicker and lose its position.
                shot = capture_screenshot((center.x(), center.y()))
            else:
                # Hide overlay so it cannot leak into the captured page
                prev_privacy = self.privacy_enabled
                self.set_privacy_mode(False)
                prev_pos = self.pos()
                self.move(-10000, -10000)
                self.hide()

                QApplication.processEvents()
                _time.sleep(0.35)
                QApplication.processEvents()

                shot = capture_screenshot((center.x(), center.y()))

                self._privacy_enabled = prev_privacy
                self.move(prev_pos)
                self.show()
                self.setAttribute(_WA.WA_ShowWithoutActivating)
                self.set_privacy_mode(prev_privacy)

            if shot:
                self.current_images.append(shot)
                self._refresh_image_previews()
                n = len(self.current_images)
                self._show_capture_status()
                self._register_dynamic_hotkey(201)
                logger.info(f"Screenshot captured ({n}/{self._max_pages})")
            else:
                logger.warning("Screenshot capture returned None")
                self.response_viewer.setText("Screenshot failed — check logs.")

        except Exception as e:
            logger.error(f"Screenshot error: {e}", exc_info=True)
            self.response_viewer.setText(f"Screenshot error: {e}")

    # ------------------------------------------------------------------
    # PC audio recording
    # ------------------------------------------------------------------

    def _toggle_recording(self):
        if self.recorder.is_recording:
            self._stop_recording()
        else:
            self._start_recording()

    def _start_recording(self):
        if self._send_in_progress:
            return
        try:
            self.recorder.start()
        except Exception as e:
            logger.error(f"Could not start PC audio recording: {e}")
            self.response_viewer.setText(f"Recording failed: {e}")
            QTimer.singleShot(50, self.adjust_height_to_content)
            return
        self._rec_timer.start()
        self._register_dynamic_hotkey(201)
        self._show_capture_status()

    def _stop_recording(self, update_status: bool = True):
        self._rec_timer.stop()
        try:
            path = self.recorder.stop()
        except Exception as e:
            logger.error(f"Stopping PC audio recording failed: {e}", exc_info=True)
            path = None
        if path:
            self.current_audio.append(path)
        if update_status:
            self._show_capture_status()

    def _discard_audio(self):
        from capture.system_audio import discard
        self._rec_timer.stop()
        self.recorder.cancel()
        discard(self.current_audio)
        self.current_audio = []

    def _audio_seconds(self) -> float:
        import wave
        total = 0.0
        for path in self.current_audio:
            try:
                with wave.open(path, "rb") as w:
                    total += w.getnframes() / float(w.getframerate())
            except Exception:
                pass
        return total

    def _show_capture_status(self):
        """One status line for everything queued: pages, recordings, live recording."""
        def mmss(sec):
            sec = int(sec)
            return f"{sec // 60}:{sec % 60:02d}"

        lines = []
        if self.recorder.is_recording:
            lines.append(
                f"<span style='color:#ff4d4d;'>● Recording PC audio</span> "
                f"<b>{mmss(self.recorder.elapsed)}</b>"
            )
        queued = []
        n = len(self.current_images)
        if n:
            queued.append(f"<b>{n}</b> page{'s' if n != 1 else ''}")
        if self.current_audio:
            queued.append(f"<b>{mmss(self._audio_seconds())}</b> of PC audio")
        if queued:
            lines.append(" + ".join(queued) + " captured.")
        if not lines:
            self.response_viewer.setHtml("Listening...")
        else:
            hints = []
            hints.append(f"<b>{chord_label(']')}</b> "
                         f"{'stop' if self.recorder.is_recording else 'record'}")
            hints.append(f"<b>{chord_label('H')}</b> add page")
            hints.append(f"<b>{chord_label('Enter')}</b> send")
            hints.append(f"<b>{chord_label('R')}</b> reset")
            self.response_viewer.setHtml("<br>".join(lines) + "<br>" + " · ".join(hints))
        QTimer.singleShot(50, self.adjust_height_to_content)

    def _refresh_image_previews(self):
        """Rebuild the thumbnail strip from current_images."""
        from PySide6.QtGui import QPixmap
        import io

        while self.preview_layout.count():
            item = self.preview_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        if not self.current_images:
            self.preview_container.hide()
            return

        for i, img in enumerate(self.current_images):
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            pixmap = QPixmap()
            pixmap.loadFromData(buf.getvalue())
            thumb = QLabel()
            thumb.setPixmap(pixmap.scaledToHeight(88, _TM.SmoothTransformation))
            thumb.setToolTip(f"Page {i + 1} of {len(self.current_images)}")
            thumb.setStyleSheet(
                "background: transparent; border: 1px solid #555; border-radius: 4px;"
            )
            badge = QLabel(f"{i + 1}", thumb)
            badge.setStyleSheet(
                "color: #fff; background: rgba(0,0,0,180); font-size: 9px; "
                "padding: 1px 4px; border-radius: 3px;"
            )
            badge.move(4, 4)
            badge.adjustSize()
            self.preview_layout.addWidget(thumb)

        self.preview_layout.addStretch()
        self.preview_container.show()

    def _handle_send(self):
        if self.recorder.is_recording:
            self._stop_recording(update_status=False)
        if not self.current_images and not self.current_audio:
            logger.debug("_handle_send called but nothing queued")
            return
        if self._send_in_progress:
            logger.debug("_handle_send ignored — request already in flight")
            return

        if not self.llm_provider:
            logger.warning("LLM provider not configured")
            self.response_viewer.setText("LLM not configured.")
            return

        self._unregister_dynamic_hotkey(201)
        images = list(self.current_images)
        audio = list(self.current_audio)
        n = len(images)
        self.preview_container.hide()
        pages = "page" if n == 1 else "pages"
        what = f"{n} {pages}" if n else "PC audio"
        if n and audio:
            what += " + PC audio"
        self.response_viewer.setHtml(
            f"{'Transcribing PC audio' if audio else f'Sending {what} to LLM'}…"
            f"<br><span style='color:#888'>Waiting for response</span>"
        )
        self.adjust_height_to_content()
        self.repaint()
        QApplication.processEvents()

        if n == 0:
            text = ""
        elif n > 1:
            text = (
                f"These {n} images are sequential pages of the same problem. "
                "Use every page together as one context."
            )
        else:
            text = "Analyze this image."

        self._send_in_progress = True
        thread = QThread(self)
        worker = _LlmSendWorker(self.llm_provider, text, images, audio)
        worker.moveToThread(thread)
        worker.status.connect(self._on_send_status)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_llm_finished)
        worker.failed.connect(self._on_llm_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        self._llm_thread = thread
        self._llm_worker = worker
        logger.info(f"Sending {n} image(s) and {len(audio)} recording(s) to LLM provider...")
        thread.start()

    def _on_send_status(self, text: str):
        self.response_viewer.setHtml(
            f"{text}<br><span style='color:#888'>Waiting for response</span>"
        )

    def _on_llm_finished(self, response, elapsed, n):
        self._send_in_progress = False
        self._discard_audio()  # transcribed and answered; don't keep recordings
        logger.info(f"LLM response received in {elapsed:.1f}s")
        text_response = (response or {}).get("response", "No response")
        metadata = (response or {}).get("metadata", {}) or {}
        logger.debug(
            f"Response metadata: model={metadata.get('model')}, "
            f"timing={metadata.get('timing')}, usage={metadata.get('usage')}"
        )
        if self.debug_mode:
            text_response = self._append_debug_hud(text_response, metadata, n, elapsed)
        self.response_viewer.setHtml(self._render_markdown_to_html(text_response))
        QTimer.singleShot(100, self.adjust_height_to_content)

    def _on_llm_failed(self, error, elapsed, n):
        self._send_in_progress = False
        logger.error(f"LLM send failed after {elapsed:.1f}s: {error}")
        self.response_viewer.setText(f"Error: {error}")
        self.preview_container.show()
        self._register_dynamic_hotkey(201)
        QTimer.singleShot(50, self.adjust_height_to_content)

    @staticmethod
    def _append_debug_hud(text: str, metadata: dict, image_count: int, elapsed: float) -> str:
        usage = metadata.get("usage") or {}
        timing = metadata.get("timing") or {}
        bits = [
            f"model={metadata.get('model', '?')}",
            f"pages={image_count}",
            f"time={elapsed:.1f}s",
        ]
        if timing.get("requests"):
            bits.append(f"requests={timing['requests']}")
        if usage:
            bits.append(
                f"tokens in={usage.get('input_tokens', '?')} "
                f"out={usage.get('output_tokens', '?')} "
                f"total={usage.get('total_tokens', '?')}"
            )
        return text.rstrip() + "\n\n---\n**debug:** `" + " · ".join(bits) + "`"

    def _render_markdown_to_html(self, md_text: str) -> str:
        """Convert markdown text to styled HTML for the response viewer."""
        try:
            html_content = markdown.markdown(
                _normalize_markdown(md_text),
                extensions=['fenced_code', 'codehilite', 'sane_lists', 'tables']
            )
        except Exception as e:
            logger.warning(f"Markdown rendering failed, using plain text: {e}")
            # Escape HTML and preserve newlines  
            import html
            html_content = html.escape(md_text).replace('\n', '<br>')

        # Qt paints a <pre> background line by line (gaps between lines), so
        # put each code block in a one-cell table, which gets one solid box.
        html_content = re.sub(r"\n</code></pre>", "</code></pre>", html_content)
        html_content = re.sub(
            r'<div class="codehilite">(.*?)</div>',
            r'<table width="100%" cellspacing="0" cellpadding="10" class="code">'
            r'<tr><td class="codehilite">\1</td></tr></table>',
            html_content,
            flags=re.S,
        )

        formatter = HtmlFormatter(style='monokai', noclasses=True)
        pygments_css = formatter.get_style_defs('.codehilite')

        return f"""
        <style>
            body {{ color: #e4e4e7; margin: 0; line-height: 145%; }}
            p {{ margin-top: 0; margin-bottom: 12px; }}
            h1, h2, h3, h4 {{ color: #ffffff; margin-top: 14px; margin-bottom: 6px; }}
            h1 {{ font-size: 19px; }} h2 {{ font-size: 17px; }}
            h3, h4 {{ font-size: 15px; }}
            strong, b {{ color: #ffffff; }}
            ul, ol {{ margin-top: 2px; margin-bottom: 12px; margin-left: 4px; }}
            li {{ margin-bottom: 6px; }}
            blockquote {{
                margin: 4px 0 12px 12px;
                color: #bae6fd;
                font-style: italic;
            }}
            hr {{ color: #3f3f46; }}
            table {{ margin-bottom: 12px; border-collapse: collapse; }}
            th, td {{ padding: 4px 10px; border: 1px solid #3f3f46; }}
            th {{ color: #ffffff; background-color: #27272a; }}
            a {{ color: #4facfe; }}
            table.code {{
                background-color: #272822;
                border: 1px solid #3f3f46;
                margin-top: 4px;
                margin-bottom: 4px;
            }}
            table.code td {{ border: none; padding: 10px; }}
            pre {{ margin: 0; line-height: 125%; }}
            pre * {{ margin: 0; padding: 0; }}
            code {{ font-family: 'Consolas', 'Monaco', monospace; font-size: 13px; }}
            p code {{ background-color: #3e3e3e; padding: 2px 5px; border-radius: 4px; }}
            {pygments_css}
        </style>
        {html_content}
        """

    def adjust_height_to_content(self):
        """
        Resize to fit content exactly.
        - Width grows first (up to half screen), then height grows without limit.
        """
        screen    = self.screen().availableGeometry()
        max_width = min(screen.width() // 2, 760)
        overhead  = 60  # legend bar + layout margins
        if self.preview_container.isVisible():
            overhead += max(96, self.preview_container.sizeHint().height())

        # Always use max width so content wraps as little as possible
        self.response_viewer.document().setTextWidth(max_width - 40)
        doc_h = self.response_viewer.document().size().height()
        new_h = max(60, int(doc_h + overhead))

        # Shrink width back down if content doesn't need the full half-screen
        # Use ideal text width + some padding, capped at max_width
        ideal_w = int(self.response_viewer.document().idealWidth()) + 40
        new_w   = max(280, min(ideal_w, max_width))

        self.resize(new_w, new_h)

    @staticmethod
    def _apply_privacy(widget: QWidget):
        """Exclude any top-level widget (dialogs, etc.) from capture."""
        exclude_widget_from_capture(widget, True)

    def _handle_reset(self):
        self._discard_audio()
        self.current_images.clear()
        self._refresh_image_previews()
        self.response_viewer.setHtml("Listening...")
        from PySide6.QtCore import QTimer
        QTimer.singleShot(50, self.adjust_height_to_content)
        self.move(20, 50)
        self._unregister_dynamic_hotkey(201)
        logger.info("App reset")

    def _toggle_visibility(self):
        if self.isVisible():
            self.hide()
            logger.debug("Window hidden")
        else:
            self.show()
            self.setAttribute(_WA.WA_ShowWithoutActivating)
            logger.debug("Window shown")

    def _toggle_privacy(self):
        # Privacy off == debug on: overlay is visible in screenshots / share
        self.set_debug_mode(self.privacy_enabled)

    def set_debug_mode(self, enabled: bool):
        """
        Debug mode:
        - Overlay IS visible in screenshots and screen share
        - Console logs at DEBUG
        - Response HUD shows model / tokens / timing
        """
        self.debug_mode = enabled
        self.privacy_enabled = not enabled
        self.set_privacy_mode(self.privacy_enabled)
        set_debug_logging(enabled)
        if self.privacy_enabled:
            self.status_label.setText("⬤")
            self.status_label.setStyleSheet(
                "color: #00ffca; font-size: 8px; background: transparent; padding: 2px;"
            )
            logger.info("Privacy ON — hidden from screenshots / screen share")
        else:
            self.status_label.setText("◯")
            self.status_label.setStyleSheet(
                "color: #ff4444; font-size: 8px; background: transparent; padding: 2px;"
            )
            logger.info("DEBUG MODE — overlay is visible in screenshots / screen share")
        self.status_label.adjustSize()
        self.resizeEvent(None)

    def move_window(self, dx, dy):
        pos = self.overlay_pos()
        self.move_overlay(pos.x() + dx, pos.y() + dy)

    # ------------------------------------------------------------------
    # LLM Init
    # ------------------------------------------------------------------

    def _init_llm_provider(self):
        provider_name = self.settings.get("llm.provider", "zai")
        api_key = self.settings.get_api_key(provider_name)

        if not api_key:
            logger.warning(f"No API key found for {provider_name}")
            self._prompt_for_api_key(provider_name)
            return

        try:
            if provider_name == "gemini":
                model = self.settings.get("llm.gemini_model", "gemini-3.1-flash-lite-preview")
                logger.info(f"Initializing Gemini provider with model: {model}")
                self.llm_provider = SmartGeminiProvider(api_key, model)
            elif provider_name == "claude":
                from llm.claude_provider import ClaudeProvider
                model = self.settings.get("llm.claude_model", "claude-3-5-sonnet-20241022")
                logger.info(f"Initializing Claude provider with model: {model}")
                self.llm_provider = SmartProvider(ClaudeProvider(api_key, model))
            elif provider_name in ("zai", "z.ai", "z_ai"):
                from llm.zai_provider import ZaiProvider
                model = self.settings.get("llm.zai_model", "glm-5.3-flash")
                effort = self.settings.get("llm.zai_reasoning_effort", "high")
                logger.info(f"Initializing Z.AI provider with model: {model}")
                self.llm_provider = SmartProvider(
                    ZaiProvider(api_key, model, reasoning_effort=effort)
                )
            else:
                logger.error(f"Unknown provider: {provider_name}")
        except Exception as e:
            logger.error(f"Error initializing LLM provider: {e}", exc_info=True)
            msg = QMessageBox(self)
            msg.setWindowTitle("LLM Error")
            msg.setText(f"Failed to initialize {provider_name}: {e}")
            msg.setIcon(QMessageBox.Icon.Warning)
            msg.show()
            self._apply_privacy(msg)
            msg.exec()

    def _prompt_for_api_key(self, provider_name: str):
        self.show()
        self.activateWindow()

        dlg = QInputDialog(self)
        dlg.setWindowTitle(f"{provider_name.title()} API Key")
        dlg.setLabelText(
            f"Enter your {provider_name.title()} API key:\n"
            f"(Saved in .privacy_llm_assistant/config.json)"
        )
        dlg.setTextEchoMode(QLineEdit.EchoMode.Password)
        dlg.show()
        self._apply_privacy(dlg)
        ok = dlg.exec()
        api_key = dlg.textValue() if ok else ""

        if ok and api_key.strip():
            self.settings.set_api_key(provider_name, api_key)
            self._init_llm_provider()
            # On first launch this runs from __init__ before _setup_ui() has
            # created the viewer; _setup_ui() sets its own initial text.
            if hasattr(self, "response_viewer"):
                self.response_viewer.setText("Assistant Ready\nListening...")

    def _handle_send_unused(self, message_data):
        pass