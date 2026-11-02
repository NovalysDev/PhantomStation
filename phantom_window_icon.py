"""Identité Windows et icônes des fenêtres du compilateur."""
import ctypes
import os
from pathlib import Path

ICON = Path(__file__).resolve().parent / 'assets' / 'phantomstation.ico'
APP_ID = 'NovalysApps.PhantomStation.Compilateur'
_icons = []  # Windows requires the icon handles to remain alive with the windows.


def set_process_identity():
    """Give the Python-hosted UI its own taskbar identity before creating Tk."""
    if os.name != 'nt':
        return
    try:
        set_id = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID
        set_id.argtypes = [ctypes.c_wchar_p]
        set_id.restype = ctypes.c_long
        set_id(APP_ID)
    except (AttributeError, OSError):
        pass


def set_window_icon(window, default=False):
    """Set the Tk and Windows title-bar/taskbar icons to the four symbols."""
    if not ICON.is_file():
        return
    if default:
        window.iconbitmap(default=str(ICON))
    window.iconbitmap(str(ICON))
    if os.name != 'nt':
        return

    def apply_native():
        try:
            if not window.winfo_exists():
                return
            user32 = ctypes.windll.user32
            user32.GetParent.argtypes = [ctypes.c_void_p]
            user32.GetParent.restype = ctypes.c_void_p
            user32.LoadImageW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint,
                                          ctypes.c_int, ctypes.c_int, ctypes.c_uint]
            user32.LoadImageW.restype = ctypes.c_void_p
            user32.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
            user32.SendMessageW.restype = ctypes.c_ssize_t
            hwnd = user32.GetParent(window.winfo_id())
            icon = user32.LoadImageW(None, str(ICON), 1, 256, 256, 0x10)  # IMAGE_ICON, LR_LOADFROMFILE
            if hwnd and icon:
                _icons.append(icon)
                user32.SendMessageW(hwnd, 0x80, 0, icon)  # WM_SETICON, ICON_SMALL
                user32.SendMessageW(hwnd, 0x80, 1, icon)  # WM_SETICON, ICON_BIG
        except (AttributeError, OSError, RuntimeError):
            pass

    window.after_idle(apply_native)
