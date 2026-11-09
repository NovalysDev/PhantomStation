"""Observe F10 during a game without capturing other applications' keys."""
import ctypes
from ctypes import wintypes
import os
import queue
import threading

user32 = ctypes.WinDLL('user32', use_last_error=True)
kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
CALLBACK = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

class _Key(ctypes.Structure):
    _fields_ = [('vk', wintypes.DWORD), ('scan', wintypes.DWORD), ('flags', wintypes.DWORD),
                ('time', wintypes.DWORD), ('extra', ctypes.c_size_t)]

user32.SetWindowsHookExW.argtypes = [ctypes.c_int, CALLBACK, wintypes.HINSTANCE, wintypes.DWORD]
user32.SetWindowsHookExW.restype = wintypes.HANDLE
user32.CallNextHookEx.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
user32.CallNextHookEx.restype = ctypes.c_ssize_t
user32.UnhookWindowsHookEx.argtypes = [wintypes.HANDLE]
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.GetMessageW.restype = ctypes.c_int
user32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT]
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
kernel32.GetCurrentThreadId.restype = wintypes.DWORD
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE

class GameReportHotkey:
    def __init__(self):
        self.events = queue.SimpleQueue()
        self.pids = frozenset({os.getpid()})
        self.thread_id = 0
        self.ready = threading.Event()
        self.installed = False
        self.down = False
        self.thread = threading.Thread(target=self._run, daemon=True, name='phantom-f10')

    def start(self):
        self.thread.start()
        self.ready.wait(2)
        return self.installed

    def update_pids(self, pids):
        self.pids = frozenset(pids) | {os.getpid()}

    def _on_key(self, vk, event, foreground_pid):
        if vk != 0x79:
            return
        if event in (0x101, 0x105):
            self.down = False
        elif event in (0x100, 0x104) and not self.down:
            self.down = True
            if foreground_pid in self.pids:
                self.events.put(True)

    def _run(self):
        self.thread_id = kernel32.GetCurrentThreadId()
        msg = wintypes.MSG()
        user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)
        @CALLBACK
        def hook(code, event, address):
            if code >= 0:
                key = ctypes.cast(address, ctypes.POINTER(_Key)).contents
                if key.vk == 0x79:
                    pid = wintypes.DWORD()
                    user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), ctypes.byref(pid))
                    self._on_key(key.vk, event, pid.value)
            # F10 continues to the game, including its existing controls.
            return user32.CallNextHookEx(None, code, event, address)
        handle = user32.SetWindowsHookExW(13, hook, kernel32.GetModuleHandleW(None), 0)
        self.installed = bool(handle)
        self.ready.set()
        if not handle:
            return
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                pass
        finally:
            user32.UnhookWindowsHookEx(handle)
            self.installed = False

    def consume(self):
        found = False
        while True:
            try: self.events.get_nowait(); found = True
            except queue.Empty: return found

    def close(self):
        if self.thread_id and self.thread.is_alive():
            user32.PostThreadMessageW(self.thread_id, 0x0012, 0, 0)
            self.thread.join(timeout=2)
