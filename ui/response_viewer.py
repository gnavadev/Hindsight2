"""
Response Viewer Component

Displays LLM responses with:
- Markdown rendering support (via markdown library)
- Code syntax highlighting (via pygments)
- Structured JSON display
"""

from PySide6.QtWidgets import QWidget, QVBoxLayout, QTextBrowser, QHBoxLayout, QLabel
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
import markdown
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_by_name, guess_lexer


class ResponseViewer(QWidget):
    """Component for displaying LLM responses with markdown support"""
    
    def __init__(self):
        super().__init__()
        self._setup_ui()
        self._init_markdown_css()
    
    def _setup_ui(self):
        """Setup the response viewer UI"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        
        # Header
        header_layout = QHBoxLayout()
        header_label = QLabel("💬 Response")
        header_label.setStyleSheet("font-size: 14pt; font-weight: bold; color: #4682d4;")
        header_layout.addWidget(header_label)
        header_layout.addStretch()
        layout.addLayout(header_layout)
        
        # Text browser for Markdown content
        self.browser = QTextBrowser()
        self.browser.setOpenExternalLinks(True)
        # Default styling
        self.browser.setStyleSheet("""
            QTextBrowser {
                background-color: #2b2b2b;
                color: #e0e0e0;
                border: 1px solid #3e3e42;
                font-family: 'Segoe UI', sans-serif;
                font-size: 14px;
                padding: 10px;
            }
        """)
        layout.addWidget(self.browser)
    
    def _init_markdown_css(self):
        """Initialize CSS for code blocks and markdown elements"""
        # Get Pygments CSS
        formatter = HtmlFormatter(style='monokai', cssclass='codehilite')
        pygments_css = formatter.get_style_defs('.codehilite')
        
        # Custom CSS for other markdown elements
        self.markdown_css = f"""
        <style>
            body {{ font-family: 'Segoe UI', sans-serif; color: #e0e0e0; }}
            h1, h2, h3, h4 {{ color: #4682d4; margin-top: 20px; }}
            a {{ color: #5792e4; text-decoration: none; }}
            code {{ background-color: #3e3e42; padding: 2px 4px; border-radius: 3px; font-family: 'Consolas', monospace; }}
            pre {{ background-color: #272822; padding: 10px; border-radius: 5px; overflow-x: auto; }}
            blockquote {{ border-left: 4px solid #4682d4; margin: 0; padding-left: 10px; color: #a0a0a0; }}
            table {{ border-collapse: collapse; width: 100%; margin: 15px 0; }}
            th, td {{ border: 1px solid #3e3e42; padding: 8px; text-align: left; }}
            th {{ background-color: #3e3e42; color: #ffffff; }}
            tr:nth-child(even) {{ background-color: #323232; }}
            
            /* Pygments CSS */
            {pygments_css}
        </style>
        """
    
    def set_response(self, text: str, is_markdown: bool = True):
        """Display a response"""
        if not is_markdown:
            # Plain text
            self.browser.setPlainText(text)
            return
            
        # Convert Markdown to HTML
        try:
            html_content = markdown.markdown(
                text,
                extensions=[
                    'fenced_code',
                    'codehilite',
                    'tables',
                    'nl2br',
                    'pymdownx.superfences'
                ],
                extension_configs={
                    'codehilite': {
                        'css_class': 'codehilite',
                        'noclasses': False,
                        'use_pygments': True
                    }
                }
            )
            
            # Add CSS and set HTML
            full_html = f"{self.markdown_css}\n{html_content}"
            self.browser.setHtml(full_html)
            
        except Exception as e:
            print(f"Markdown rendering error: {e}")
            self.browser.setPlainText(text)
    
    def append_response(self, text: str):
        """Append to existing response (basic implementation)"""
        current = self.browser.toPlainText()
        self.set_response(current + text)
        self.browser.moveCursor(self.browser.textCursor().End)
    
    def clear(self):
        """Clear the response display"""
        self.browser.clear()
