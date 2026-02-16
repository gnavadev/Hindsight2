"""
Screenshot Capture Module

Fast screen capture using mss library
"""

from PIL import Image
import mss


def capture_screenshot(center_point=None):
    """
    Capture a screenshot of the monitor containing the center_point.
    If center_point is None, captures the primary monitor.
    
    Args:
        center_point: Tuple (x, y) coordinates
    """
    try:
        with mss.mss() as sct:
            monitor_idx = 1 # Default to primary
            
            if center_point:
                x, y = center_point
                # Find which monitor contains the point
                # sct.monitors[0] is 'all monitors combined', so skip it
                for i, m in enumerate(sct.monitors[1:], 1):
                    if (m['left'] <= x < m['left'] + m['width']) and \
                       (m['top'] <= y < m['top'] + m['height']):
                        monitor_idx = i
                        break
            
            monitor = sct.monitors[monitor_idx]
            screenshot = sct.grab(monitor)
            
            # Convert to PIL Image
            img = Image.frombytes('RGB', screenshot.size, screenshot.bgra, 'raw', 'BGRX')
            
            print(f"✓ Screenshot captured (Monitor {monitor_idx}): {img.size}")
            return img
    except Exception as e:
        print(f"Error capturing screenshot: {e}")
        return None


def capture_region(x, y, width, height):
    """
    Capture a specific region of the screen.
    
    Args:
        x: Left coordinate
        y: Top coordinate
        width: Width of region
        height: Height of region
    
    Returns:
        PIL Image object
    """
    try:
        with mss.mss() as sct:
            monitor = {"top": y, "left": x, "width": width, "height": height}
            screenshot = sct.grab(monitor)
            
            img = Image.frombytes('RGB', screenshot.size, screenshot.bgra, 'raw', 'BGRX')
            
            print(f"✓ Region captured: {img.size}")
            return img
    except Exception as e:
        print(f"Error capturing region: {e}")
        return None
