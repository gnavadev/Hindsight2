
import sys
import signal
import logging
from PySide6.QtWidgets import QApplication
from ui.main_window import MainWindow
from utils.logging_setup import configure_logging, debug_requested


logger = logging.getLogger(__name__)


def main():
    """Main application entry point"""
    configure_logging()
    logger.info("Starting Privacy LLM Assistant")
    if debug_requested():
        logger.info("Started with --debug / HINDSIGHT_DEBUG (verbose console logs)")

    # Allow Ctrl+C to exit the app from terminal
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    try:
        # Create application
        app = QApplication(sys.argv)
        app.setApplicationName("Privacy LLM Assistant")
        if sys.platform == "darwin":
            from utils.macos_window import hide_dock_icon
            hide_dock_icon()

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