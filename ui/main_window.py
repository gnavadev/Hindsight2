"""
Main Window Implementation

Combines all UI components into the main application window with:
- Multi-modal input panel
- Response display area
- Document sidebar
- Keyboard shortcuts
"""

from PySide6.QtWidgets import (
    QHBoxLayout, QVBoxLayout, QLabel, QWidget, QMessageBox, QInputDialog,
    QTextBrowser, QSizePolicy, QLineEdit
)
from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QKeySequence, QShortcut, QPalette, QColor, QDesktopServices
import ctypes
import markdown
from pygments.formatters import HtmlFormatter
from ctypes import wintypes
from ui.privacy_window import PrivacyWindow
from config.settings import Settings
from llm.gemini_provider import GeminiProvider
from llm.smart_provider import SmartGeminiProvider
from llm.claude_provider import ClaudeProvider

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
        self.setWindowOpacity(0.85)
        self.set_click_through(True)

        # 3. Start small; expand once content is ready
        self.resize(280, 60)

        # Load settings
        self.settings = Settings()

        # Privacy state
        self.privacy_enabled = True

        # Initialize LLM provider
        self.llm_provider = None
        self._init_llm_provider()

        self._position_toast()
        self._apply_dark_theme()
        self._setup_ui()

        # Native Hotkeys
        self.hotkey_ids = {}
        self._setup_global_hotkeys()

        print("✓ Overlay initialized")

    # ------------------------------------------------------------------
    # Positioning
    # ------------------------------------------------------------------

    def _position_toast(self):
        """Position window in top-left corner"""
        self.move(20, 50)

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
                background-color: rgba(30, 30, 30, 240);
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
            ("Ctrl+H", "Screenshot"),
            ("Ctrl+R", "Reset"),
            ("Ctrl+B", "Hide/Show"),
            ("Ctrl+P", "Privacy"),
            ("Ctrl+Arrows", "Move"),
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

        # 2. Image preview (hidden by default)
        self.image_preview_label = QLabel()
        self.image_preview_label.setAlignment(_AF.AlignCenter)
        self.image_preview_label.setStyleSheet(
            "background: transparent; border: 1px solid #444; border-radius: 4px;"
        )
        self.image_preview_label.hide()
        container_layout.addWidget(self.image_preview_label)

        # 3. Response viewer
        self.response_viewer = QTextBrowser()
        self.response_viewer.setStyleSheet("""
            QTextBrowser {
                color: #e0e0e0;
                font-size: 14px;
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
        """Register global system-wide hotkeys via user32.dll"""
        self.user32 = ctypes.windll.user32

        MOD_CONTROL = 0x0002
        VK_H, VK_B, VK_P, VK_R   = 0x48, 0x42, 0x50, 0x52
        VK_LEFT, VK_UP, VK_RIGHT, VK_DOWN = 0x25, 0x26, 0x27, 0x28
        VK_ENTER = 0x0D
        step = 30

        self.hotkeys = {
            101: (MOD_CONTROL, VK_H,     self._handle_screenshot),
            102: (MOD_CONTROL, VK_B,     self._toggle_visibility),
            103: (MOD_CONTROL, VK_P,     self._toggle_privacy),
            104: (MOD_CONTROL, VK_UP,    lambda: self.move_window(0, -step)),
            105: (MOD_CONTROL, VK_DOWN,  lambda: self.move_window(0,  step)),
            106: (MOD_CONTROL, VK_LEFT,  lambda: self.move_window(-step, 0)),
            107: (MOD_CONTROL, VK_RIGHT, lambda: self.move_window( step, 0)),
            108: (MOD_CONTROL, VK_R,     self._handle_reset),
        }

        self.dynamic_hotkeys = {
            201: (MOD_CONTROL, VK_ENTER, self._handle_send),
        }
        self.registered_dynamic_ids = set()

        hwnd = int(self.winId())
        for hk_id, (mod, vk, _) in self.hotkeys.items():
            if self.user32.RegisterHotKey(hwnd, hk_id, mod, vk):
                self.hotkey_ids[hk_id] = self.hotkeys[hk_id][2]
                print(f"Registered hotkey ID {hk_id}")
            else:
                print(f"Failed to register hotkey ID {hk_id}")

    def _register_dynamic_hotkey(self, hk_id):
        if hk_id in self.registered_dynamic_ids or hk_id not in self.dynamic_hotkeys:
            return
        mod, vk, func = self.dynamic_hotkeys[hk_id]
        if self.user32.RegisterHotKey(int(self.winId()), hk_id, mod, vk):
            self.hotkey_ids[hk_id] = func
            self.registered_dynamic_ids.add(hk_id)
            print(f"Registered dynamic hotkey ID {hk_id}")
        else:
            print(f"Failed to register dynamic hotkey ID {hk_id}")

    def _unregister_dynamic_hotkey(self, hk_id):
        if hk_id not in self.registered_dynamic_ids:
            return
        if self.user32.UnregisterHotKey(int(self.winId()), hk_id):
            self.hotkey_ids.pop(hk_id, None)
            self.registered_dynamic_ids.discard(hk_id)
            print(f"Unregistered dynamic hotkey ID {hk_id}")

    def nativeEvent(self, event_type, message):
        try:
            if event_type == b'windows_generic_MSG':
                msg = wintypes.MSG.from_address(message.__int__())
                if msg.message == 0x0312:  # WM_HOTKEY
                    hk_id = msg.wParam
                    if hk_id in self.hotkey_ids:
                        self.hotkey_ids[hk_id]()
                        return True, 0
        except Exception as e:
            print(f"Error in nativeEvent: {e}")
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
            import time

            geo = self.geometry()
            center_x = geo.x() + geo.width() // 2
            center_y = geo.y() + geo.height() // 2

            self.set_privacy_mode(False)
            prev_pos = self.pos()
            self.move(-10000, -10000)
            self.hide()

            QApplication.processEvents()
            time.sleep(0.5)
            QApplication.processEvents()

            self.current_image = capture_screenshot((center_x, center_y))

            self.move(prev_pos)
            self.show()
            self.setAttribute(_WA.WA_ShowWithoutActivating)
            self.set_privacy_mode(True)  # re-enable after screenshot

            if self.current_image:
                from PySide6.QtGui import QPixmap
                import io
                buf = io.BytesIO()
                self.current_image.save(buf, format='PNG')
                pixmap = QPixmap()
                pixmap.loadFromData(buf.getvalue())
                self.image_preview_label.setPixmap(
                    pixmap.scaledToHeight(100, _TM.SmoothTransformation)
                )
                self.image_preview_label.show()
                self.response_viewer.setText(
                    "Screenshot captured!\nPress <b>Ctrl+Enter</b> to send."
                )
                from PySide6.QtCore import QTimer
                QTimer.singleShot(50, self.adjust_height_to_content)
                self._register_dynamic_hotkey(201)

        except Exception as e:
            print(f"Screenshot error: {e}")
            self.response_viewer.setText(f"Screenshot error: {e}")

    def _handle_send(self):
        if not hasattr(self, 'current_image') or not self.current_image:
            return

        self._unregister_dynamic_hotkey(201)
        self.image_preview_label.hide()
        self.response_viewer.setHtml("Sending to LLM...")

        from PySide6.QtCore import QTimer
        QTimer.singleShot(50, self.adjust_height_to_content)
        self.repaint()

        if self.llm_provider:
            try:
                response = self.llm_provider.send_message(
                    text="Analyze this image.",
                    images=[self.current_image]
                )
                text_response = response.get('response', 'No response')

                html_content = markdown.markdown(
                    text_response,
                    extensions=['fenced_code', 'codehilite']
                )
                formatter = HtmlFormatter(style='monokai', noclasses=True)
                pygments_css = formatter.get_style_defs('.codehilite')

                styled_html = f"""
                <style>
                    body {{ font-family: 'Segoe UI', sans-serif; color: #e0e0e0; margin: 0; }}
                    p {{ margin-bottom: 10px; margin-top: 0; }}
                    a {{ color: #4facfe; }}
                    pre {{
                        background-color: #272822;
                        padding: 12px;
                        border-radius: 6px;
                        border: 1px solid #444;
                        overflow-x: auto;
                        margin: 8px 0;
                        line-height: 1.2;
                    }}
                    pre * {{ margin: 0; padding: 0; }}
                    code {{ font-family: 'Consolas', 'Monaco', monospace; font-size: 13px; }}
                    p code {{ background-color: #3e3e3e; padding: 2px 5px; border-radius: 4px; }}
                    {pygments_css}
                </style>
                {html_content}
                """
                self.response_viewer.setHtml(styled_html)
                QTimer.singleShot(100, self.adjust_height_to_content)

            except Exception as e:
                self.response_viewer.setText(f"Error: {e}")
        else:
            self.response_viewer.setText("LLM not configured.")

    def adjust_height_to_content(self):
        """
        Resize to fit content exactly.
        - Width grows first (up to half screen), then height grows without limit.
        """
        screen    = self.screen().availableGeometry()
        max_width = screen.width() // 2
        overhead  = 60  # legend bar + layout margins

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
        """Apply WDA_EXCLUDEFROMCAPTURE to any top-level widget (dialogs, etc.)"""
        import ctypes
        hwnd = int(widget.winId())
        if hwnd:
            ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, 0x00000011)

    def _handle_reset(self):
        if hasattr(self, 'current_image'):
            del self.current_image
        self.image_preview_label.clear()
        self.image_preview_label.hide()
        self.response_viewer.setHtml("Listening...")
        from PySide6.QtCore import QTimer
        QTimer.singleShot(50, self.adjust_height_to_content)
        self.move(20, 50)
        self._unregister_dynamic_hotkey(201)
        print("App reset")

    def _toggle_visibility(self):
        if self.isVisible():
            self.hide()
        else:
            self.show()
            self.setAttribute(_WA.WA_ShowWithoutActivating)

    def _toggle_privacy(self):
        self.privacy_enabled = not self.privacy_enabled
        self.set_privacy_mode(self.privacy_enabled)
        if self.privacy_enabled:
            self.status_label.setText("⬤")
            self.status_label.setStyleSheet(
                "color: #00ffca; font-size: 8px; background: transparent; padding: 2px;"
            )
        else:
            self.status_label.setText("◯")
            self.status_label.setStyleSheet(
                "color: #ff4444; font-size: 8px; background: transparent; padding: 2px;"
            )
        self.status_label.adjustSize()
        self.resizeEvent(None)

    def move_window(self, dx, dy):
        pos = self.pos()
        self.move(pos.x() + dx, pos.y() + dy)

    # ------------------------------------------------------------------
    # LLM Init
    # ------------------------------------------------------------------

    def _init_llm_provider(self):
        provider_name = self.settings.get("llm.provider", "gemini")
        api_key = self.settings.get_api_key(provider_name)

        if not api_key:
            print(f"⚠ No API key found for {provider_name}")
            self._prompt_for_api_key(provider_name)
            return

        try:
            if provider_name == "gemini":
                model = self.settings.get("llm.gemini_model", "gemini-2.5-flash-lite")
                print(f"Using Gemini model: {model}")
                self.llm_provider = SmartGeminiProvider(api_key, model)
            elif provider_name == "claude":
                model = self.settings.get("llm.claude_model", "claude-3-5-sonnet-20241022")
                print(f"Using Claude model: {model}")
                self.llm_provider = ClaudeProvider(api_key, model)
            else:
                print(f"Unknown provider: {provider_name}")
        except Exception as e:
            print(f"Error initializing LLM provider: {e}")
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
            self.response_viewer.setText("Assistant Ready\nListening...")

    def _handle_send_unused(self, message_data):
        pass