"""Windows kill-on-close process containment. No PID enumeration."""

import ctypes as c
from ctypes import wintypes as w


class BasicLimits(c.Structure):
    _fields_ = [("ProcessTime", c.c_int64), ("JobTime", c.c_int64),
                ("Flags", w.DWORD), ("MinWorkingSet", c.c_size_t),
                ("MaxWorkingSet", c.c_size_t), ("ActiveLimit", w.DWORD),
                ("Affinity", c.c_size_t), ("Priority", w.DWORD), ("Scheduling", w.DWORD)]


class IoCounters(c.Structure):
    _fields_ = [(name, c.c_uint64) for name in
                ("ReadOps", "WriteOps", "OtherOps", "ReadBytes", "WriteBytes", "OtherBytes")]


class ExtendedLimits(c.Structure):
    _fields_ = [("Basic", BasicLimits), ("Io", IoCounters),
                ("ProcessMemory", c.c_size_t), ("JobMemory", c.c_size_t),
                ("PeakProcessMemory", c.c_size_t), ("PeakJobMemory", c.c_size_t)]


class WindowsJob:
    def __init__(self):
        self.api = c.WinDLL("kernel32", use_last_error=True)
        declarations = {
            "CreateJobObjectW": ([c.c_void_p, w.LPCWSTR], w.HANDLE),
            "SetInformationJobObject": ([w.HANDLE, c.c_int, c.c_void_p, w.DWORD], w.BOOL),
            "AssignProcessToJobObject": ([w.HANDLE, w.HANDLE], w.BOOL),
            "CloseHandle": ([w.HANDLE], w.BOOL),
        }
        for name, (args, result) in declarations.items():
            method = getattr(self.api, name)
            method.argtypes, method.restype = args, result
        # NULL security attributes: handle is not inherited by children.
        self.handle = self.api.CreateJobObjectW(None, None)
        if not self.handle:
            raise OSError("job_create_failed")
        limits = ExtendedLimits()
        limits.Basic.Flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.api.SetInformationJobObject(self.handle, 9, c.byref(limits), c.sizeof(limits)):
            self.close()
            raise OSError("job_limits_failed")

    def assign(self, process):
        # Popen retains its Windows process handle until it is released.
        if not self.api.AssignProcessToJobObject(self.handle, int(process._handle)):
            raise OSError("job_assign_failed")

    def close(self):
        if self.handle:
            if not self.api.CloseHandle(self.handle):
                raise OSError("job_close_failed")
            self.handle = None
