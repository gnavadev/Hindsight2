"""
Document Sidebar Component

Collapsible sidebar for viewing documents while keeping them invisible to screen sharing.
Supports: PDF, TXT, DOCX, MD files
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QTextBrowser, QLabel,
    QPushButton, QFileDialog, QHBoxLayout
)
from PySide6.QtCore import Qt, Signal
import os


class DocumentSidebar(QWidget):
    """Sidebar for viewing documents privately"""
    
    document_loaded = Signal(str)  # Emits file path
    
    def __init__(self):
        super().__init__()
        self.setFixedWidth(300)
        self._current_file = None
        self._setup_ui()
        
        # Enable drag and drop
        self.setAcceptDrops(True)
        
    def _setup_ui(self):
        """Setup the sidebar UI"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        
        # Header layout
        header_layout = QHBoxLayout()
        
        header_label = QLabel("📄 Documents")
        header_label.setStyleSheet("font-size: 14pt; font-weight: bold; color: #4682d4;")
        header_layout.addWidget(header_label)
        
        header_layout.addStretch()
        
        # Open file button
        self.open_btn = QPushButton("📁")
        self.open_btn.setToolTip("Open Document")
        self.open_btn.setFixedWidth(30)
        self.open_btn.clicked.connect(self._open_file)
        header_layout.addWidget(self.open_btn)
        
        layout.addLayout(header_layout)
        
        # File info label
        self.file_label = QLabel("No document loaded")
        self.file_label.setStyleSheet("color: gray; font-style: italic;")
        self.file_label.setWordWrap(True)
        layout.addWidget(self.file_label)
        
        # Document viewer
        self.doc_viewer = QTextBrowser()
        self.doc_viewer.setPlaceholderText("Drag and drop a PDF, TXT, or DOCX file here to view it privately.")
        layout.addWidget(self.doc_viewer)
        
        # Navigation/Close buttons
        controls_layout = QHBoxLayout()
        
        self.close_btn = QPushButton("✖ Close Document")
        self.close_btn.setStyleSheet("""
            QPushButton {
                background-color: #c42b1c;
                color: white;
                border: none;
                padding: 5px;
            }
            QPushButton:hover {
                background-color: #e81123;
            }
        """)
        self.close_btn.clicked.connect(self._close_document)
        self.close_btn.setVisible(False)
        controls_layout.addWidget(self.close_btn)
        
        layout.addLayout(controls_layout)
    
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.accept()
        else:
            event.ignore()
            
    def dropEvent(self, event):
        files = [u.toLocalFile() for u in event.mimeData().urls()]
        if files:
            self.load_document(files[0])
    
    def _open_file(self):
        """Open file dialog to load document"""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Open Document",
            "",
            "Documents (*.pdf *.txt *.docx *.md);;All Files (*.*)"
        )
        if file_path:
            self.load_document(file_path)
    
    def load_document(self, file_path: str):
        """Load and display a document"""
        try:
            from utils.file_handler import read_document
            content = read_document(file_path)
            
            self.doc_viewer.setPlainText(content)
            self._current_file = file_path
            
            # Update UI
            filename = os.path.basename(file_path)
            self.file_label.setText(f"📄 {filename}")
            self.file_label.setStyleSheet("color: #4682d4; font-weight: bold;")
            self.close_btn.setVisible(True)
            
            self.document_loaded.emit(file_path)
            print(f"Loaded document: {filename}")
        except Exception as e:
            self.doc_viewer.setPlainText(f"Error loading document:\n{str(e)}")
            print(f"Error loading document: {e}")
    
    def _close_document(self):
        """Close the current document"""
        self.doc_viewer.clear()
        self._current_file = None
        self.file_label.setText("No document loaded")
        self.file_label.setStyleSheet("color: gray; font-style: italic;")
        self.close_btn.setVisible(False)
