
import sys
import signal
import logging
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from ui.main_window import MainWindow


def _setup_logging():
    """Configure centralized logging: console (INFO) + file (DEBUG)."""
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)-28s | %(message)s",
        datefmt="%H:%M:%S",
    )

    # Console handler — INFO and above
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(fmt)
    root.addHandler(console)

    # File handler — DEBUG and above (full trace)
    try:
        file_h = logging.FileHandler("app.log", encoding="utf-8")
        file_h.setLevel(logging.DEBUG)
        file_h.setFormatter(fmt)
        root.addHandler(file_h)
    except OSError as e:
        console.setLevel(logging.DEBUG)  # fallback: send DEBUG to console
        logging.warning(f"Could not create app.log: {e}")

    # Quieten noisy third-party loggers
    logging.getLogger("PIL").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("google").setLevel(logging.WARNING)


logger = logging.getLogger(__name__)


def main():
    """Main application entry point"""
    _setup_logging()
    logger.info("Starting Privacy LLM Assistant")

    # Allow Ctrl+C to exit the app from terminal
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    try:
        # Create application
        app = QApplication(sys.argv)
        app.setApplicationName("Privacy LLM Assistant")

        # Create main window
        window = MainWindow()
        window.show()

        logger.info("Application window shown — entering event loop")
        # Run event loop
        sys.exit(app.exec())
    except Exception as e:
        logger.critical(f"Fatal error: {e}", exc_info=True)
        # Basic error handling for crash prevention
        # We need to create a simple app context if it doesn't exist to show the message
        if not QApplication.instance():
            app = QApplication(sys.argv)

        from PySide6.QtWidgets import QMessageBox
        error_msg = f"An unexpected error occurred:\n{str(e)}"
        QMessageBox.critical(None, "Application Error", error_msg)
        sys.exit(1)


if __name__ == "__main__":
    main()