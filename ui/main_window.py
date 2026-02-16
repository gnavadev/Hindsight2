"""
Main Window Implementation

Combines all UI components into the main application window with:
- Multi-modal input panel
- Response display area
- Document sidebar
- Keyboard shortcuts
"""

from PySide6.QtWidgets import (
    QHBoxLayout, QVBoxLayout, QLabel, QWidget, QMessageBox, QInputDialog, QTextBrowser,
    QSizePolicy
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


class MainWindow(PrivacyWindow):
    """Main application window combining all UI components"""
    
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Privacy LLM Assistant")
        
        # 1. Window Flags: Frameless, Always on Top, Tool, No Focus
        # Qt.WindowDoesNotAcceptFocus is crucial to let clicks pass through logic work better
        # and not steal focus when shown.
        self.setWindowFlags(
            Qt.FramelessWindowHint | 
            Qt.WindowStaysOnTopHint | 
            Qt.Tool |
            Qt.WindowDoesNotAcceptFocus # Restore focus transparency
        )
        
        # 2. Transparency and Pay-through
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating) # Don't steal focus on show
        self.setWindowOpacity(0.85)  # Add some transparency as requested
        self.set_click_through(True) # Restore click-through (no input)
        
        # 3. Toast Size
        # Dynamic initial sizing
        # self.resize(400, 160) -> Removed
        from PySide6.QtCore import QTimer
        QTimer.singleShot(100, self.adjust_height_to_content) 
        
        # Load settings
        self.settings = Settings()
        
        # Privacy state
        self.privacy_enabled = True
        
        # Initialize LLM provider
        self.llm_provider = None
        self._init_llm_provider()
        
        # Position: Start Top-Right or specific place? User said "Stay in place". 
        # I'll default to top-right standard notification area for now.
        self._position_toast()
        
        # Setup components
        self._apply_dark_theme()
        self._setup_ui()
        
        # Native Hotkeys Setup
        self.hotkey_ids = {}
        self._setup_global_hotkeys()
        
        print("✓ Overlay initialized")

    def _position_toast(self):
        """Position window in top-left corner"""
        # 20px padding from top-left
        self.move(20, 50)

    def resizeEvent(self, event):
        """Handle resize to reposition overlay elements"""
        if hasattr(self, 'status_label') and self.status_label:
            # Position top-right of the container
            # Container has 15px side margins. 
            # We want it in top right corner.
            # self.response_viewer takes the space, so we float over it.
            
            # Find the container widget (it's the only child of main_layout)
            container = self.findChild(QWidget, "container")
            if container:
                # Position relative to container
                lb_width = self.status_label.width()
                lb_height = self.status_label.height()
                
                # Top right of container, with some padding
                # Container layout margins are 15, 10...
                # We put it at x = container_width - lb_width - 10
                x = container.width() - lb_width - 10
                y = 5  # Close to top
                
                self.status_label.move(x, y)
                self.status_label.raise_()
        
        super().resizeEvent(event)

    def _apply_dark_theme(self):
        """Apply a modern dark theme"""
        palette = QPalette()
        palette.setColor(QPalette.Window, QColor(30, 30, 30))
        palette.setColor(QPalette.WindowText, QColor(224, 224, 224))
        palette.setColor(QPalette.Base, QColor(43, 43, 43))
        palette.setColor(QPalette.AlternateBase, QColor(53, 53, 53))
        palette.setColor(QPalette.ToolTipBase, QColor(255, 255, 220))
        palette.setColor(QPalette.ToolTipText, QColor(0, 0, 0))
        palette.setColor(QPalette.Text, QColor(224, 224, 224))
        palette.setColor(QPalette.Button, QColor(53, 53, 53))
        palette.setColor(QPalette.ButtonText, QColor(224, 224, 224))
        palette.setColor(QPalette.BrightText, QColor(255, 0, 0))
        palette.setColor(QPalette.Link, QColor(42, 130, 218))
        palette.setColor(QPalette.Highlight, QColor(42, 130, 218))
        palette.setColor(QPalette.HighlightedText, QColor(0, 0, 0))
        self.setPalette(palette)
        
        # Global stylesheet
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


    def _setup_ui(self):
        """Setup the minimalist UI layout"""
        # Main layout
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 5) # Reduced bottom margin
        main_layout.setSpacing(2) # Tighter spacing
        
        # Container for visual style (rounded corners, background)
        container = QWidget()
        container.setObjectName("container")
        container.setAttribute(Qt.WA_StyledBackground, True) # Ensure background paints
        container.setStyleSheet("""
            QWidget#container {
                background-color: rgba(30, 30, 30, 240);
                border: 1px solid rgba(255, 255, 255, 30);
                border-radius: 10px;
            }
        """)
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(15, 10, 15, 5) # Reduced bottom margin
        
        # 1. Legend Bar (Header)
        # Use a simpler layout: 2 rows of items
        legend_widget = QWidget()
        legend_widget.setStyleSheet("background: transparent;")
        legend_layout = QVBoxLayout(legend_widget)
        legend_layout.setContentsMargins(0, 0, 0, 0)
        legend_layout.setSpacing(0) # minimal gap between rows
        
        row1_layout = QHBoxLayout()
        row1_layout.setSpacing(10) # gap between items
        row1_layout.setAlignment(Qt.AlignLeft)
        
        row2_layout = QHBoxLayout()
        row2_layout.setSpacing(10)
        row2_layout.setAlignment(Qt.AlignLeft)
        
        shortcuts = [
            ("Ctrl+H", "Screenshot"),
            ("Ctrl+R", "Reset"),
            ("Ctrl+B", "Hide/Show"),
            ("Ctrl+P", "Privacy"),
            ("Ctrl+Arrows", "Move")
        ]
        
        # Split into 2 rows (3 in first, rest in second)
        # or 3 and 2
        for i, (keys, desc) in enumerate(shortcuts):
            label = QLabel(f"<span style='color: #4facfe;'><b>{keys}</b></span> <span style='color: #888;'>{desc}</span>")
            label.setStyleSheet("font-size: 9px; margin: 0px; padding: 0px;")
            
            if i < 3:
                row1_layout.addWidget(label)
            else:
                row2_layout.addWidget(label)
                
        # Add stretch to right to push items left
        row1_layout.addStretch()
        row2_layout.addStretch()
        
        legend_layout.addLayout(row1_layout)
        legend_layout.addLayout(row2_layout)
        
        container_layout.addWidget(legend_widget)
        
        # 2. Image Preview Area (Hidden by default)
        self.image_preview_label = QLabel()
        self.image_preview_label.setAlignment(Qt.AlignCenter)
        self.image_preview_label.setStyleSheet("background: transparent; border: 1px solid #444; border-radius: 4px;")
        self.image_preview_label.hide()
        container_layout.addWidget(self.image_preview_label)

        # 3. Response / Main Content
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
        self.response_viewer.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.response_viewer.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.response_viewer.setHtml("Listening...") # Minimal text
        container_layout.addWidget(self.response_viewer)
        

        # Layout Stretches
        # Index 0: Legend (Fixed)
        # Index 1: Image Preview (Fixed/Hidden)
        # Index 2: Response Viewer (Expanded)
        container_layout.setStretch(0, 0)
        container_layout.setStretch(1, 0)
        container_layout.setStretch(2, 1)
        
        main_layout.addWidget(container)
        
        # Status Label (Overlay - Minimal Dot)
        self.status_label = QLabel("⬤", container)
        self.status_label.setStyleSheet("""
            color: #00ffca; 
            font-size: 8px; 
            background: transparent; 
            padding: 2px;
        """)
        self.status_label.adjustSize()
        self.status_label.show() 

    # ... Global Hotkeys skipped ...

    # ... Hotkey registration/unregistration skipped ...

    # ... Native Event skipped ...

    def _toggle_privacy(self):
        """Toggle privacy mode on/off"""
        self.privacy_enabled = not self.privacy_enabled
        self.set_privacy_mode(self.privacy_enabled)
        
        if self.privacy_enabled:
            self.status_label.setText("⬤")
            self.status_label.setStyleSheet("""
                color: #00ffca; 
                font-size: 8px; 
                background: transparent; 
                padding: 2px;
            """)
        else:
            self.status_label.setText("◯")
            self.status_label.setStyleSheet("""
                color: #ff4444; 
                font-size: 8px; 
                background: transparent; 
                padding: 2px;
            """)
            
        self.status_label.adjustSize()
        self.resizeEvent(None) 

    def _setup_global_hotkeys(self):
        """Register global system-wide hotkeys using user32.dll"""
        self.user32 = ctypes.windll.user32
        
        # Modifiers
        MOD_ALT = 0x0001
        MOD_CONTROL = 0x0002
        MOD_SHIFT = 0x0004
        MOD_WIN = 0x0008
        
        # Virtual Key Codes
        VK_ENTER = 0x0D
        VK_LEFT = 0x25
        VK_UP = 0x26
        VK_RIGHT = 0x27
        VK_DOWN = 0x28
        VK_H = 0x48
        VK_B = 0x42
        VK_P = 0x50
        VK_R = 0x52
        
        # Permanent Hotkeys
        step = 30 # Increased speed (was 10)
        self.hotkeys = {
            101: (MOD_CONTROL, VK_H, self._handle_screenshot),
            102: (MOD_CONTROL, VK_B, self._toggle_visibility),
            103: (MOD_CONTROL, VK_P, self._toggle_privacy),
            104: (MOD_CONTROL, VK_UP, lambda: self.move_window(0, -step)),
            105: (MOD_CONTROL, VK_DOWN, lambda: self.move_window(0, step)),
            106: (MOD_CONTROL, VK_LEFT, lambda: self.move_window(-step, 0)),
            107: (MOD_CONTROL, VK_RIGHT, lambda: self.move_window(step, 0)),
            108: (MOD_CONTROL, VK_R, self._handle_reset),
        }
        
        # Dynamic Hotkeys (Registered only when needed)
        self.dynamic_hotkeys = {
            201: (MOD_CONTROL, VK_ENTER, self._handle_send)
        }
        
        self.registered_dynamic_ids = set()
        
        hwnd = int(self.winId())
        
        # Register permanent hotkeys
        for hk_id, (mod, vk, func) in self.hotkeys.items():
            if self.user32.RegisterHotKey(hwnd, hk_id, mod, vk):
                self.hotkey_ids[hk_id] = func
                print(f"Registered hotkey ID {hk_id}")
            else:
                print(f"Failed to register hotkey ID {hk_id}")

    def _register_dynamic_hotkey(self, hk_id):
        """Register a dynamic hotkey"""
        if hk_id in self.registered_dynamic_ids:
            return
            
        if hk_id in self.dynamic_hotkeys:
            mod, vk, func = self.dynamic_hotkeys[hk_id]
            hwnd = int(self.winId())
            if self.user32.RegisterHotKey(hwnd, hk_id, mod, vk):
                self.hotkey_ids[hk_id] = func
                self.registered_dynamic_ids.add(hk_id)
                print(f"Registered dynamic hotkey ID {hk_id}")
            else:
                print(f"Failed to register dynamic hotkey ID {hk_id}")

    def _unregister_dynamic_hotkey(self, hk_id):
        """Unregister a dynamic hotkey"""
        if hk_id not in self.registered_dynamic_ids:
            return
            
        hwnd = int(self.winId())
        if self.user32.UnregisterHotKey(hwnd, hk_id):
            if hk_id in self.hotkey_ids:
                del self.hotkey_ids[hk_id]
            self.registered_dynamic_ids.remove(hk_id)
            print(f"Unregistered dynamic hotkey ID {hk_id}")

    def nativeEvent(self, event_type, message):
        """Handle native Windows events for hotkeys"""
        try:
            if event_type == b'windows_generic_MSG':
                msg = wintypes.MSG.from_address(message.__int__())
                if msg.message == 0x0312:  # WM_HOTKEY
                    hk_id = msg.wParam
                    if hk_id in self.hotkey_ids:
                        # Execute the callback
                        self.hotkey_ids[hk_id]()
                        return True, 0
        except Exception as e:
            print(f"Error in nativeEvent: {e}")
            
        return super().nativeEvent(event_type, message)

    def _setup_shortcuts(self):
        """Deprecated: Shortcuts now handled by global hotkeys"""
        pass

    def _handle_screenshot(self):
        """Handle screenshot request"""
        try:
            from capture.screenshot import capture_screenshot
            
            # Determine window center to find the correct monitor
            geo = self.geometry()
            center_x = geo.x() + geo.width() // 2
            center_y = geo.y() + geo.height() // 2
            
            # HIDE strategy: Move offscreen to avoid "Black Box" artifact completely
            # Toggle privacy OFF to be safe (though offscreen shouldn't matter)
            # Toggle privacy OFF to be safe (though offscreen shouldn't matter)
            self.set_privacy_mode(False)
            
            prev_pos = self.pos()
            self.move(-10000, -10000)
            self.hide() # Double safety
            
            from PySide6.QtWidgets import QApplication
            import time
            QApplication.processEvents()
            time.sleep(0.5) 
            QApplication.processEvents()
            
            self.current_image = capture_screenshot((center_x, center_y))
            
            # Restore
            self.move(prev_pos)
            self.show()
            self.setAttribute(Qt.WA_ShowWithoutActivating)
            
            if self.current_image:
                # Show preview (scaled)
                from PySide6.QtGui import QPixmap
                import io
                buffer = io.BytesIO()
                self.current_image.save(buffer, format='PNG')
                pixmap = QPixmap()
                pixmap.loadFromData(buffer.getvalue())
                
                # Scale for preview (max height 100)
                preview = pixmap.scaledToHeight(100, Qt.SmoothTransformation)
                self.image_preview_label.setPixmap(preview)
                self.image_preview_label.show()
                
                self.response_viewer.setText("Screenshot captured!\nPress <b>Ctrl+Enter</b> to send.")
                
                # Trigger resize to fit new content
                from PySide6.QtCore import QTimer
                QTimer.singleShot(50, self.adjust_height_to_content)
                
                # Enable Send Shortcut
                self._register_dynamic_hotkey(201) # Ctrl+Enter
                
        except Exception as e:
            print(f"Screenshot error: {e}")
            self.response_viewer.setText(f"Screenshot error: {str(e)}")

    def _handle_send(self):
        """Handle send command (Ctrl+Enter)"""
        if not hasattr(self, 'current_image') or not self.current_image:
            return
            
        # Unregister shortcut immediately to prevent double sends
        self._unregister_dynamic_hotkey(201)
        
        # Hide image preview to make room for text
        self.image_preview_label.hide()
        
        self.response_viewer.setHtml("Sending to LLM...")
        
        # Trigger resize
        from PySide6.QtCore import QTimer
        QTimer.singleShot(50, self.adjust_height_to_content)
        
        self.repaint() # Force update
        
        # Send to LLM (Thread this ideally, but for now simple sync or check provider)
        if self.llm_provider:
            try:
                response = self.llm_provider.send_message(
                    text="Analyze this image.", # Default prompt
                    images=[self.current_image]
                )
                
                text_response = response.get('response', 'No response')
                
                # Render Markdown with Pygments
                # extensions=['fenced_code', 'codehilite']
                html_content = markdown.markdown(
                    text_response, 
                    extensions=['fenced_code', 'codehilite']
                )
                
                # Get Pygments CSS (Monokai)
                formatter = HtmlFormatter(style='monokai', noclasses=True)
                pygments_css = formatter.get_style_defs('.codehilite')
                
                # Style tweaks for the HTML
                # We inject the pygments CSS directly
                styled_html = f"""
                <style>
                    body {{ font-family: 'Segoe UI', sans-serif; color: #e0e0e0; margin: 0; }}
                    p {{ margin-bottom: 10px; margin-top: 0; }}
                    a {{ color: #4facfe; }}
                    
                    /* Pre/Code blocks */
                    pre {{ 
                        background-color: #272822; /* Monokai bg */
                        padding: 12px; 
                        border-radius: 6px;
                        border: 1px solid #444;
                        overflow-x: auto;
                        margin-top: 8px;
                        margin-bottom: 8px;
                        line-height: 1.2; /* Tighter line height */
                    }}
                    
                    /* Force elements inside pre to have no margin/padding if they are block */
                    pre * {{
                        margin: 0;
                        padding: 0;
                    }}
                    
                    /* Improve code readability */
                    code {{ 
                        font-family: 'Consolas', 'Monaco', monospace; 
                        font-size: 13px;
                    }}
                    
                    /* Inline code */
                    p code {{
                        background-color: #3e3e3e;
                        padding: 2px 5px;
                        border-radius: 4px;
                    }}
                    
                    {pygments_css}
                </style>
                {html_content}
                """
                
                self.response_viewer.setHtml(styled_html)
                # self.status_label.setText("Response received") -> Removed
                
                # Allow one event loop cycle for text to render before measuring
                from PySide6.QtCore import QTimer
                QTimer.singleShot(100, self.adjust_height_to_content)
                
            except Exception as e:
                self.response_viewer.setText(f"Error: {e}")
                # self.status_label.setText("Error") -> Removed
        else:
             self.response_viewer.setText("LLM not configured.")

    def adjust_height_to_content(self):
        """Dynamically adjust window height/width based on content"""
        # Force layout reset
        self.response_viewer.document().setTextWidth(-1)
        
        screen = self.screen().availableGeometry()
        max_h = screen.height()
        
        # Iterative sizing: Start with base width and expand if too tall
        width_steps = [400, 600, 800, 1000]
        final_width = 400
        final_height = 110 # Min height default
        
        overhead = 80 # Legend + margins buffer
        
        for w in width_steps:
            # Set width to test
            self.response_viewer.document().setTextWidth(w - 40) # adjustment for margins
            
            # Measure height
            doc_h = self.response_viewer.document().size().height()
            req_h = int(doc_h + overhead)
            req_h = max(110, req_h) # Mnimum height
            
            # Condition: If height is reasonable (< 85% screen) OR we are at max width
            if req_h < (max_h * 0.85) or w == 1000:
                final_width = w
                final_height = req_h
                break
        
        # --- Smart Position Adjustment ---
        current_y = self.y()
        bottom_y = current_y + final_height
        screen_bottom = screen.y() + screen.height()
        
        if bottom_y > screen_bottom:
             # Shift up
             diff = bottom_y - screen_bottom
             new_y = max(screen.y() + 20, current_y - diff)
             self.move(self.x(), new_y)
             
        self.resize(final_width, final_height)
        
        # --- Smart Position Adjustment ---
        # Check if we are running off the bottom
        current_y = self.y()
        bottom_y = current_y + new_height
        screen_bottom = screen.y() + screen.height()
        
        # If we go off bottom, we move UP to fit as much as possible, 
        # BUT we prioritize keeping the Top visible so user can interact.
        if bottom_y > screen_bottom:
            # Shift up, but don't go off top
            diff = bottom_y - screen_bottom
            new_y = max(screen.y() + 20, current_y - diff) # 20px padding from top
            self.move(self.x(), new_y)
            
        # Resize
        self.resize(new_width, new_height)

    def _handle_reset(self):
        """Reset the application state (Ctrl+R)"""
        # Clear image
        if hasattr(self, 'current_image'):
            del self.current_image
            
        # Hide preview
        self.image_preview_label.clear()
        self.image_preview_label.hide()
        
        # Reset text
        self.response_viewer.setHtml("Listening...")
        # self.status_label.setText("Reset complete") -> Removed
        
        # Trigger dynamic resize
        from PySide6.QtCore import QTimer
        QTimer.singleShot(50, self.adjust_height_to_content)
        
        self.move(20, 50) # Reset position to top-left
        
        # Unregister dynamic keys
        self._unregister_dynamic_hotkey(201)
        
        print("App reset")

    def _toggle_visibility(self):
        """Toggle window visibility"""
        if self.isVisible():
            self.hide()
        else:
            self.show()
            # Do NOT activate window to keep focus elsewhere
            self.setAttribute(Qt.WA_ShowWithoutActivating)

    def move_window(self, dx, dy):
        """Move window by offset"""
        current_pos = self.pos()
        self.move(current_pos.x() + dx, current_pos.y() + dy)

    def _init_llm_provider(self):
        """Initialize the LLM provider based on settings"""
        provider_name = self.settings.get("llm.provider", "gemini")
        api_key = self.settings.get_api_key(provider_name)
        
        if not api_key:
            print(f"⚠ No API key found for {provider_name}")
            self.response_viewer.setText(f"<b>Setup Required</b><br>API Key missing for {provider_name}.<br>Check window popup.")
            self._prompt_for_api_key(provider_name)
            return
        
        try:
            if provider_name == "gemini":
                model = self.settings.get("llm.gemini_model", "gemini-2.5-flash-lite")
                # Use Smart Provider by default for Gemini
                self.llm_provider = SmartGeminiProvider(api_key, model)
            elif provider_name == "claude":
                model = self.settings.get("llm.claude_model", "claude-3-5-sonnet-20241022")
                self.llm_provider = ClaudeProvider(api_key, model)
            else:
                print(f"Unknown provider: {provider_name}")
        except Exception as e:
            print(f"Error initializing LLM provider: {e}")
            QMessageBox.warning(self, "LLM Error", f"Failed to initialize {provider_name}: {str(e)}")
    
    def _prompt_for_api_key(self, provider_name: str):
        """Prompt user to enter API key"""
        # Ensure window is visible for prompt
        self.show()
        self.activateWindow()
        
        api_key, ok = QInputDialog.getText(
            self,
            f"{provider_name.title()} API Key",
            f"Enter your {provider_name.title()} API key:\n(Saved in .privacy_llm_assistant/config.json)",
            echo=QInputDialog.Password
        )
        
        if ok and api_key:
            self.settings.set_api_key(provider_name, api_key)
            self._init_llm_provider()
            self.response_viewer.setText("Assistant Ready\nListening...")
    
    def _handle_send_unused(self, message_data):
        """Handle send request from input panel (Unused)"""
        pass
