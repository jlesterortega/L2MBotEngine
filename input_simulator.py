import ctypes
import ctypes.wintypes
import time
import logging

logger = logging.getLogger(__name__)

# Win32 Native Constants Definitions
INPUT_MOUSE    = 0
INPUT_KEYBOARD = 1
KEYEVENTF_SCANCODE = 0x0008
KEYEVENTF_KEYUP    = 0x0002
MAPVK_VK_TO_VSC    = 0

VK_MAP = {
    '0': 48, '1': 49, '2': 50, '3': 51, '4': 52, '5': 53, '6': 54, '7': 55, '8': 56, '9': 57,
    'a': 65, 'b': 66, 'c': 67, 'd': 68, 'e': 69, 'f': 70, 'g': 71, 'h': 72, 'i': 73, 'j': 74,
    'k': 75, 'l': 76, 'm': 77, 'n': 78, 'o': 79, 'p': 80, 'q': 81, 'r': 82, 's': 83, 't': 84,
    'u': 85, 'v': 86, 'w': 87, 'x': 88, 'y': 89, 'z': 90,
    'f1': 112, 'f2': 113, 'f3': 114, 'f4': 115, 'f5': 116, 'f6': 117, 'f7': 118, 'f8': 119, 'f9': 120, 'f10': 121, 'f11': 122, 'f12': 123,
    'space': 32, 'enter': 13, 'esc': 27, 'tab': 9, 'ctrl': 17, 'shift': 16, 'alt': 18
}

class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ('wVk', ctypes.wintypes.WORD),
        ('wScan', ctypes.wintypes.WORD),
        ('dwFlags', ctypes.wintypes.DWORD),
        ('time', ctypes.wintypes.DWORD),
        ('dwExtraInfo', ctypes.POINTER(ctypes.wintypes.ULONG))
    ]

class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ('dx', ctypes.wintypes.LONG),
        ('dy', ctypes.wintypes.LONG),
        ('mouseData', ctypes.wintypes.DWORD),
        ('dwFlags', ctypes.wintypes.DWORD),
        ('time', ctypes.wintypes.DWORD),
        ('dwExtraInfo', ctypes.POINTER(ctypes.wintypes.ULONG))
    ]

class _INPUT_UNION(ctypes.Union):
    _fields_ = [('mi', MOUSEINPUT), ('ki', KEYBDINPUT)]

class INPUT(ctypes.Structure):
    _fields_ = [('type', ctypes.wintypes.DWORD), ('union', _INPUT_UNION)]

def _get_scan_code(key_str: str) -> int:
    vk = VK_MAP.get(key_str.lower(), 0)
    if vk == 0 and len(key_str) == 1:
        vk = ctypes.windll.user32.VkKeyScanW(ord(key_str)) & 0xFF
    return ctypes.windll.user32.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC)

def press_key(key_str: str, times: int = 1):
    scan_code = _get_scan_code(key_str)
    if scan_code == 0:
        return
        
    for _ in range(times):
        # Keyboard press sequence creation structure arrays
        inp_down = INPUT(type=INPUT_KEYBOARD, union=_INPUT_UNION(ki=KEYBDINPUT(wVk=0, wScan=scan_code, dwFlags=KEYEVENTF_SCANCODE, time=0, dwExtraInfo=None)))
        inp_up   = INPUT(type=INPUT_KEYBOARD, union=_INPUT_UNION(ki=KEYBDINPUT(wVk=0, wScan=scan_code, dwFlags=KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP, time=0, dwExtraInfo=None)))
        
        ctypes.windll.user32.SendInput(1, ctypes.byref(inp_down), ctypes.sizeof(INPUT))
        time.sleep(0.02)
        ctypes.windll.user32.SendInput(1, ctypes.byref(inp_up), ctypes.sizeof(INPUT))
        if times > 1:
            time.sleep(0.1)

def press_combo(modifier_str: str, key_str: str):
    """Executes composite inputs cleanly via low level serialization chains"""
    mod_codes = []
    for part in modifier_str.lower().split('+'):
        sc = _get_scan_code(part.strip())
        if sc > 0: mod_codes.append(sc)
        
    target_sc = _get_scan_code(key_str)
    if target_sc == 0: return

    # Trigger down sequence down the stack structural alignment configurations
    for sc in mod_codes:
        inp = INPUT(type=INPUT_KEYBOARD, union=_INPUT_UNION(ki=KEYBDINPUT(wVk=0, wScan=sc, dwFlags=KEYEVENTF_SCANCODE, time=0, dwExtraInfo=None)))
        ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
    
    time.sleep(0.02)
    inp_t_down = INPUT(type=INPUT_KEYBOARD, union=_INPUT_UNION(ki=KEYBDINPUT(wVk=0, wScan=target_sc, dwFlags=KEYEVENTF_SCANCODE, time=0, dwExtraInfo=None)))
    ctypes.windll.user32.SendInput(1, ctypes.byref(inp_t_down), ctypes.sizeof(INPUT))
    
    time.sleep(0.03)
    
    # Trigger up release tracking validation mechanisms 
    inp_t_up = INPUT(type=INPUT_KEYBOARD, union=_INPUT_UNION(ki=KEYBDINPUT(wVk=0, wScan=target_sc, dwFlags=KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP, time=0, dwExtraInfo=None)))
    ctypes.windll.user32.SendInput(1, ctypes.byref(inp_t_up), ctypes.sizeof(INPUT))
    
    time.sleep(0.02)
    for sc in reversed(mod_codes):
        inp = INPUT(type=INPUT_KEYBOARD, union=_INPUT_UNION(ki=KEYBDINPUT(wVk=0, wScan=sc, dwFlags=KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP, time=0, dwExtraInfo=None)))
        ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))

def click_at(x: int, y: int):
    # Absolute dynamic conversion coordinates calculations
    w = ctypes.windll.user32.GetSystemMetrics(0)
    h = ctypes.windll.user32.GetSystemMetrics(1)
    abs_x = int(x * 65535 / w)
    abs_y = int(y * 65535 / h)
    
    # Move mouse event payload creation actions tracking parameters
    m_move = INPUT(type=INPUT_MOUSE, union=_INPUT_UNION(mi=MOUSEINPUT(dx=abs_x, dy=abs_y, mouseData=0, dwFlags=0x0001 | 0x8000, time=0, dwExtraInfo=None)))
    m_down = INPUT(type=INPUT_MOUSE, union=_INPUT_UNION(mi=MOUSEINPUT(dx=abs_x, dy=abs_y, mouseData=0, dwFlags=0x0002 | 0x8000, time=0, dwExtraInfo=None)))
    m_up   = INPUT(type=INPUT_MOUSE, union=_INPUT_UNION(mi=MOUSEINPUT(dx=abs_x, dy=abs_y, mouseData=0, dwFlags=0x0004 | 0x8000, time=0, dwExtraInfo=None)))
    
    ctypes.windll.user32.SendInput(1, ctypes.byref(m_move), ctypes.sizeof(INPUT))
    time.sleep(0.02)
    ctypes.windll.user32.SendInput(1, ctypes.byref(m_down), ctypes.sizeof(INPUT))
    time.sleep(0.02)
    ctypes.windll.user32.SendInput(1, ctypes.byref(m_up), ctypes.sizeof(INPUT))