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

    send_requested = Signal(dict)

    def __init__(self):
        super().__init__()
        self._images = []
        self._audio_file = None
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)

        # Header
        header_layout = QHBoxLayout()
        header_label = QLabel("📝 Input")
        header_label.setStyleSheet("font-size: 12pt; font-weight: bold; color: #4682d4;")
        header_layout.addWidget(header_label)
        header_layout.addStretch()
        layout.addLayout(header_layout)

        # Text input
        self.text_input = QTextEdit()
        self.text_input.setPlaceholderText("Type your message here... (Ctrl+Enter to send)")
        self.text_input.setMinimumHeight(100)
        layout.addWidget(self.text_input)

        # Attachments preview
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

        self.image_btn = QPushButton("📷 Image")
        self.image_btn.setToolTip("Add image from file")
        self.image_btn.clicked.connect(self._add_image_from_file)
        buttons_layout.addWidget(self.image_btn)

        self.mic_btn = QPushButton("🎤 Microphone")
        self.mic_btn.setToolTip("Record from microphone")
        self.mic_btn.clicked.connect(self._toggle_mic_recording)
        buttons_layout.addWidget(self.mic_btn)

        self.system_audio_btn = QPushButton("🔊 System Audio")
        self.system_audio_btn.setToolTip("Record system audio")
        self.system_audio_btn.clicked.connect(self._toggle_system_audio_recording)
        buttons_layout.addWidget(self.system_audio_btn)

        buttons_layout.addStretch()

        self.clear_btn = QPushButton("🗑️ Clear")
        self.clear_btn.setToolTip("Clear all inputs")
        self.clear_btn.clicked.connect(self._clear_all)
        buttons_layout.addWidget(self.clear_btn)

        self.send_btn = QPushButton("✉️ Send")
        self.send_btn.setToolTip("Send message (Ctrl+Enter)")
        self.send_btn.setStyleSheet("""
            QPushButton {
                background-color: #4682d4;
                color: white;
                font-weight: bold;
                padding: 8px 20px;
            }
            QPushButton:hover { background-color: #5792e4; }
            QPushButton:pressed { background-color: #3672c4; }
        """)
        self.send_btn.clicked.connect(self._send_message)
        buttons_layout.addWidget(self.send_btn)

        layout.addLayout(buttons_layout)

        send_shortcut = QShortcut(QKeySequence("Ctrl+Return"), self)
        send_shortcut.activated.connect(self._send_message)

    def add_image(self, image):
        if isinstance(image, Image.Image):
            self._images.append(image)
            self._update_attachments_preview()

    def _add_image_from_file(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Select Image", "",
            "Images (*.png *.jpg *.jpeg *.bmp *.gif)"
        )
        if file_path:
            try:
                self.add_image(Image.open(file_path))
            except Exception as e:
                print(f"Error loading image: {e}")

    def _toggle_mic_recording(self):
        if self.mic_btn.text() == "🎤 Microphone":
            self.mic_btn.setText("⏹️ Stop Recording")
            self.mic_btn.setStyleSheet("background-color: #c42b1c;")
        else:
            self.mic_btn.setText("🎤 Microphone")
            self.mic_btn.setStyleSheet("")

    def _toggle_system_audio_recording(self):
        if self.system_audio_btn.text() == "🔊 System Audio":
            self.system_audio_btn.setText("⏹️ Stop Recording")
            self.system_audio_btn.setStyleSheet("background-color: #c42b1c;")
        else:
            self.system_audio_btn.setText("🔊 System Audio")
            self.system_audio_btn.setStyleSheet("")

    def _update_attachments_preview(self):
        # Clear existing previews
        while self.attachments_layout.count():
            item = self.attachments_layout.takeAt(0)
            w = item.widget()
            if w is not None:           # fix: guard before calling deleteLater
                w.deleteLater()

        self.attachments_area.setVisible(len(self._images) > 0)

        for i, img in enumerate(self._images):
            buf = io.BytesIO()
            img.save(buf, format='PNG')
            pixmap = QPixmap()
            pixmap.loadFromData(buf.getvalue())

            # fix: use proper v6 enum namespaces
            thumbnail = pixmap.scaled(
                100, 100,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            )

            lbl = QLabel()
            lbl.setPixmap(thumbnail)
            lbl.setToolTip(f"Image {i + 1}")
            lbl.setStyleSheet("border: 1px solid #555; padding: 2px;")
            self.attachments_layout.addWidget(lbl)

        self.attachments_layout.addStretch()

    def _clear_all(self):
        self.text_input.clear()
        self._images.clear()
        self._audio_file = None
        self._update_attachments_preview()

    def _send_message(self):
        text = self.text_input.toPlainText().strip()
        if not text and not self._images and not self._audio_file:
            return
        self.send_requested.emit({
            'text': text,
            'images': self._images.copy(),
            'audio': self._audio_file,
        })
        self._clear_all()