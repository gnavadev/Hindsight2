import tkinter as tk
import ctypes

def test_privacy():
    root = tk.Tk()
    root.title("Privacy Test")
    root.geometry("300x200")
    
    label = tk.Label(root, text="Testing Tkinter + Privacy")
    label.pack(pady=20)
    
    # Test HWND access
    try:
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        if hwnd == 0:
            hwnd = root.winfo_id()
        print(f"Got HWND: {hwnd}")
        
        # Try setting display affinity
        WDA_EXCLUDEFROMCAPTURE = 0x00000011
        user32 = ctypes.windll.user32
        result = user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)
        if result != 0:
            print("Privacy mode set successfully")
        else:
            print(f"Failed to set privacy. Error: {ctypes.get_last_error()}")
            
    except Exception as e:
        print(f"Error: {e}")

    # Auto close after 2 seconds for test
    root.after(2000, root.destroy)
    root.mainloop()

if __name__ == "__main__":
    test_privacy()
