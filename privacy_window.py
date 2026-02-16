import tkinter as tk
import ctypes
from ctypes import wintypes

# Windows API Constants
WDA_NONE = 0x00000000
WDA_MONITOR = 0x00000001
WDA_EXCLUDEFROMCAPTURE = 0x00000011

GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000

def set_window_privacy(hwnd):
    """
    Sets the window display affinity to exclude it from capture.
    """
    try:
        user32 = ctypes.windll.user32
        result = user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)
        if result == 0:
            print(f"Failed to set display affinity. Error: {ctypes.get_last_error()}")
        else:
            print("Successfully set window privacy (WDA_EXCLUDEFROMCAPTURE).")
    except Exception as e:
        print(f"Error setting window privacy: {e}")

def set_stealth_mode(hwnd):
    """
    Sets extended window styles to hide from Taskbar and Alt-Tab.
    """
    try:
        user32 = ctypes.windll.user32
        
        # Get current extended style
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        
        # Add TOOLWINDOW style, Remove APPWINDOW style
        # WS_EX_TOOLWINDOW: Hides from Alt-Tab and Taskbar (usually)
        new_style = (style | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW
        
        result = user32.SetWindowLongW(hwnd, GWL_EXSTYLE, new_style)
        if result == 0:
            print(f"Failed to set stealh mode. Error: {ctypes.get_last_error()}")
        else:
            print("Successfully set stealth mode (WS_EX_TOOLWINDOW).")
            
    except Exception as e:
        print(f"Error setting stealth mode: {e}")

def main():
    root = tk.Tk()
    root.title("Privacy Protected Window")
    root.geometry("400x200")
    
    # Remove window decorations (Title bar, borders)
    root.overrideredirect(True)
    
    # Keep window on top so valid user can find it (optional, but helpful since no taskbar icon)
    root.attributes("-topmost", True)

    # Frame for content
    frame = tk.Frame(root, bg="black")
    frame.pack(fill=tk.BOTH, expand=True)

    label_text = "STEALTH MODE ACTIVE\n\n1. Invisible to Screen Capture\n2. Hidden from Taskbar\n3. Hidden from Alt-Tab\n\n[Double-click to Close]"
    label = tk.Label(frame, text=label_text, fg="white", bg="black", font=("Arial", 12), justify="center")
    label.pack(expand=True)
    
    # Add simple drag functionality since title bar is gone
    def start_move(event):
        root.x = event.x
        root.y = event.y

    def stop_move(event):
        root.x = None
        root.y = None

    def do_move(event):
        deltax = event.x - root.x
        deltay = event.y - root.y
        x = root.winfo_x() + deltax
        y = root.winfo_y() + deltay
        root.geometry(f"+{x}+{y}")

    frame.bind("<ButtonPress-1>", start_move)
    frame.bind("<ButtonRelease-1>", stop_move)
    frame.bind("<B1-Motion>", do_move)
    
    # Close on double click
    frame.bind("<Double-Button-1>", lambda e: root.destroy())

    # Force update to ensure window creation
    root.update()
    
    # Get window handle - For overrideredirect windows, we might need the parent or wrapper
    # But often winfo_id works, or we find by class/title if strictly necessary.
    # With overrideredirect, FindWindow might fail if title isn't published the same way.
    # Let's try winfo_id first as it should be the HWND wrapper.
    hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
    if not hwnd:
        hwnd = root.winfo_id()
        
    print(f"Window Handle: {hwnd}")
    
    set_window_privacy(hwnd)
    set_stealth_mode(hwnd)

    root.mainloop()

if __name__ == "__main__":
    main()
