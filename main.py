
import sys
import signal
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from ui.main_window import MainWindow


def main():
    """Main application entry point"""
    # Allow Ctrl+C to exit the app from terminal
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    # Enable high DPI scaling BEFORE creating QApplication
    # Note: In PySide6 6.0+, high DPI is enabled by default.
    # if hasattr(Qt.ApplicationAttribute, 'AA_EnableHighDpiScaling'):
    #     QApplication.setAttribute(Qt.ApplicationAttribute.AA_EnableHighDpiScaling, True)
    # if hasattr(Qt.ApplicationAttribute, 'AA_UseHighDpiPixmaps'):
    #     QApplication.setAttribute(Qt.ApplicationAttribute.AA_UseHighDpiPixmaps, True)
    
    try:
        # Create application
        app = QApplication(sys.argv)
        app.setApplicationName("Privacy LLM Assistant")
        
        # Create main window
        window = MainWindow()
        window.show()
        
        # Run event loop
        sys.exit(app.exec())
    except Exception as e:
        # Basic error handling for crash prevention
        # We need to create a simple app context if it doesn't exist to show the message
        if not QApplication.instance():
            app = QApplication(sys.argv)
        
        from PySide6.QtWidgets import QMessageBox
        error_msg = f"An unexpected error occurred:\n{str(e)}"
        print(error_msg)
        QMessageBox.critical(None, "Application Error", error_msg)
        sys.exit(1)


if __name__ == "__main__":
    main()