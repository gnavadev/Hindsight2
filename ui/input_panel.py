"""
Input Panel Component

Multi-modal input interface supporting:
- Text input
- Image attachments (screenshot, file upload)
- Audio recording (microphone, system audio)
- Send button and Ctrl+Enter shortcut
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTextEdit,
    QPushButton, QLabel, QFileDialog, QScrollArea
)
from PySide6.QtCore import Signal, Qt, QSize
from PySide6.QtGui import QKeySequence, QShortcut, QPixmap
from PIL import Image
import io


class InputPanel(QWidget):
    """Multi-modal input panel for sending messages to LLMs"""
    
    send_requested = Signal(dict)  # Emits message data
    
    def __init__(self):
        super().__init__()
        self._images = []
        self._audio_file = None
        self._setup_ui()
    
    def _setup_ui(self):
        """Setup the input panel UI"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        
        # Header
        header_layout = QHBoxLayout()
        header_label = QLabel("📝 Input")
        header_label.setStyleSheet("font-size: 12pt; font-weight: bold; color: #4682d4;")
        header_layout.addWidget(header_label)
        header_layout.addStretch()
        layout.addLayout(header_layout)
        
        # Text input area
        self.text_input = QTextEdit()
        self.text_input.setPlaceholderText("Type your message here... (Ctrl+Enter to send)")
        self.text_input.setMinimumHeight(100)
        layout.addWidget(self.text_input)
        
        # Attachments preview area
        self.attachments_area = QScrollArea()
        self.attachments_area.setMaximumHeight(120)
        self.attachments_area.setWidgetResizable(True)
        self.attachments_area.setVisible(False)
        self.attachments_widget = QWidget()
        self.attachments_layout = QHBoxLayout(self.attachments_widget)
        self.attachments_area.setWidget(self.attachments_widget)
        layout.addWidget(self.attachments_area)
        
        # Buttons bar
        buttons_layout = QHBoxLayout()
        
        # Add image button
        self.image_btn = QPushButton("📷 Image")
        self.image_btn.setToolTip("Add image from file")
        self.image_btn.clicked.connect(self._add_image_from_file)
        buttons_layout.addWidget(self.image_btn)
        
        # Record microphone button
        self.mic_btn = QPushButton("🎤 Microphone")
        self.mic_btn.setToolTip("Record from microphone")
        self.mic_btn.clicked.connect(self._toggle_mic_recording)
        buttons_layout.addWidget(self.mic_btn)
        
        # Record system audio button
        self.system_audio_btn = QPushButton("🔊 System Audio")
        self.system_audio_btn.setToolTip("Record system audio")
        self.system_audio_btn.clicked.connect(self._toggle_system_audio_recording)
        buttons_layout.addWidget(self.system_audio_btn)
        
        buttons_layout.addStretch()
        
        # Clear button
        self.clear_btn = QPushButton("🗑️ Clear")
        self.clear_btn.setToolTip("Clear all inputs")
        self.clear_btn.clicked.connect(self._clear_all)
        buttons_layout.addWidget(self.clear_btn)
        
        # Send button
        self.send_btn = QPushButton("✉️ Send")
        self.send_btn.setToolTip("Send message (Ctrl+Enter)")
        self.send_btn.setStyleSheet("""
            QPushButton {
                background-color: #4682d4;
                color: white;
                font-weight: bold;
                padding: 8px 20px;
            }
            QPushButton:hover {
                background-color: #5792e4;
            }
            QPushButton:pressed {
                background-color: #3672c4;
            }
        """)
        self.send_btn.clicked.connect(self._send_message)
        buttons_layout.addWidget(self.send_btn)
        
        layout.addLayout(buttons_layout)
        
        # Ctrl+Enter to send
        send_shortcut = QShortcut(QKeySequence("Ctrl+Return"), self)
        send_shortcut.activated.connect(self._send_message)
    
    def add_image(self, image):
        """Add an image to the input (from screenshot or file)"""
        if isinstance(image, Image.Image):
            self._images.append(image)
            self._update_attachments_preview()
    
    def _add_image_from_file(self):
        """Open file dialog to add image"""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Image",
            "",
            "Images (*.png *.jpg *.jpeg *.bmp *.gif)"
        )
        if file_path:
            try:
                image = Image.open(file_path)
                self.add_image(image)
            except Exception as e:
                print(f"Error loading image: {e}")
    
    def _toggle_mic_recording(self):
        """Toggle microphone recording"""
        # TODO: Implement microphone recording
        if self.mic_btn.text() == "🎤 Microphone":
            self.mic_btn.setText("⏹️ Stop Recording")
            self.mic_btn.setStyleSheet("background-color: #c42b1c;")
            print("Started mic recording")
        else:
            self.mic_btn.setText("🎤 Microphone")
            self.mic_btn.setStyleSheet("")
            print("Stopped mic recording")
    
    def _toggle_system_audio_recording(self):
        """Toggle system audio recording"""
        # TODO: Implement system audio recording
        if self.system_audio_btn.text() == "🔊 System Audio":
            self.system_audio_btn.setText("⏹️ Stop Recording")
            self.system_audio_btn.setStyleSheet("background-color: #c42b1c;")
            print("Started system audio recording")
        else:
            self.system_audio_btn.setText("🔊 System Audio")
            self.system_audio_btn.setStyleSheet("")
            print("Stopped system audio recording")
    
    def _update_attachments_preview(self):
        """Update the attachments preview area"""
        # Clear existing previews
        while self.attachments_layout.count():
            child = self.attachments_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        
        # Show area if we have attachments
        self.attachments_area.setVisible(len(self._images) > 0)
        
        # Add image previews
        for i, img in enumerate(self._images):
            # Convert PIL Image to QPixmap
            buffer = io.BytesIO()
            img.save(buffer, format='PNG')
            pixmap = QPixmap()
            pixmap.loadFromData(buffer.getvalue())
            
            # Create thumbnail
            thumbnail = pixmap.scaled(100, 100, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            
            # Create label with thumbnail
            img_label = QLabel()
            img_label.setPixmap(thumbnail)
            img_label.setToolTip(f"Image {i+1}")
            img_label.setStyleSheet("border: 1px solid #555; padding: 2px;")
            self.attachments_layout.addWidget(img_label)
        
        self.attachments_layout.addStretch()
    
    def _clear_all(self):
        """Clear all inputs"""
        self.text_input.clear()
        self._images.clear()
        self._audio_file = None
        self._update_attachments_preview()
    
    def _send_message(self):
        """Collect and send message data"""
        text = self.text_input.toPlainText().strip()
        
        if not text and not self._images and not self._audio_file:
            print("Nothing to send")
            return
        
        message_data = {
            'text': text,
            'images': self._images.copy(),
            'audio': self._audio_file
        }
        
        self.send_requested.emit(message_data)
        self._clear_all()
