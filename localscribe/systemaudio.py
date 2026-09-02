"""System-audio capture without a virtual audio driver.

macOS 14.4 added Core Audio *process taps*: you can tap what the machine is
playing and wrap that tap in an aggregate device, which then appears as an
ordinary input device. No kernel extension, no admin password, no reboot, and
no Multi-Output Device to assemble by hand — which is everything BlackHole
costs a first-time user.

The device exists only while the process that made it is alive, so a tap is
opened for the duration of a recording and torn down afterwards. If we die
before tearing it down, the aggregate device outlives us as a broken entry in
everyone's device list, so `cleanup_stale()` sweeps up anything left behind by
a LocalScribe that is no longer running.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import os
import platform
import struct
import time

DEVICE_PREFIX = "LocalScribe System Audio"
UID_PREFIX = "com.localscribe.tap."
# The tap object is itself visible to Core Audio, so it needs a different name
# from the aggregate device or the two cannot be told apart.
_TAP_NAME = "LocalScribe Tap"

_MIN_MACOS = (14, 4)   # when process taps landed


class SystemAudioError(RuntimeError):
    pass


# --------------------------------------------------------------- ctypes glue

def _fourcc(code: str) -> int:
    return struct.unpack(">I", code.encode())[0]


_PROP_DEVICES = _fourcc("dev#")
_PROP_UID = _fourcc("uid ")
_SCOPE_GLOBAL = _fourcc("glob")
_SYSTEM_OBJECT = 1
_UTF8 = 0x08000100


class _Address(ctypes.Structure):
    _fields_ = [
        ("mSelector", ctypes.c_uint32),
        ("mScope", ctypes.c_uint32),
        ("mElement", ctypes.c_uint32),
    ]


def _frameworks():
    core = ctypes.CDLL(ctypes.util.find_library("CoreAudio"))
    foundation = ctypes.CDLL(ctypes.util.find_library("CoreFoundation"))
    foundation.CFStringGetCString.argtypes = [
        ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32
    ]
    foundation.CFStringGetCString.restype = ctypes.c_bool
    core.AudioHardwareDestroyAggregateDevice.argtypes = [ctypes.c_uint32]
    return core, foundation


def _device_ids(core) -> list[int]:
    addr = _Address(_PROP_DEVICES, _SCOPE_GLOBAL, 0)
    size = ctypes.c_uint32(0)
    if core.AudioObjectGetPropertyDataSize(
        _SYSTEM_OBJECT, ctypes.byref(addr), 0, None, ctypes.byref(size)
    ) != 0:
        return []
    ids = (ctypes.c_uint32 * (size.value // 4))()
    if core.AudioObjectGetPropertyData(
        _SYSTEM_OBJECT, ctypes.byref(addr), 0, None, ctypes.byref(size), ids
    ) != 0:
        return []
    return list(ids)


def _device_uid(core, foundation, device_id: int) -> str | None:
    addr = _Address(_PROP_UID, _SCOPE_GLOBAL, 0)
    size = ctypes.c_uint32(ctypes.sizeof(ctypes.c_void_p))
    ref = ctypes.c_void_p()
    if core.AudioObjectGetPropertyData(
        device_id, ctypes.byref(addr), 0, None, ctypes.byref(size), ctypes.byref(ref)
    ) != 0 or not ref.value:
        return None
    buf = ctypes.create_string_buffer(512)
    if foundation.CFStringGetCString(ref.value, buf, 512, _UTF8):
        return buf.value.decode()
    return None


def _process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def cleanup_stale() -> int:
    """Destroy tap devices left behind by LocalScribe processes that have died."""
    if platform.system() != "Darwin":
        return 0
    try:
        core, foundation = _frameworks()
    except (OSError, TypeError):
        return 0
    removed = 0
    for device_id in _device_ids(core):
        uid = _device_uid(core, foundation, device_id) or ""
        if not uid.startswith(UID_PREFIX):
            continue
        owner = uid[len(UID_PREFIX):]
        if owner.isdigit() and _process_alive(int(owner)):
            continue   # another LocalScribe is recording right now
        if core.AudioHardwareDestroyAggregateDevice(device_id) == 0:
            removed += 1
    return removed


# ------------------------------------------------------------- availability

def _macos_version() -> tuple[int, ...]:
    try:
        return tuple(int(p) for p in platform.mac_ver()[0].split(".")[:2])
    except ValueError:
        return (0,)


def available() -> tuple[bool, str]:
    """-> (usable, reason). The reason is what we show the user when it isn't."""
    if platform.system() != "Darwin":
        return False, "Core Audio taps are macOS-only"
    version = _macos_version()
    if version < _MIN_MACOS:
        got = ".".join(str(v) for v in version) or "unknown"
        return False, f"needs macOS 14.4 or newer (this is {got})"
    try:
        import objc  # noqa: F401
        from CoreAudio import AudioHardwareCreateProcessTap  # noqa: F401
    except ImportError:
        return False, "pyobjc is not installed"
    return True, ""


# --------------------------------------------------------------------- tap

class SystemAudioTap:
    """While open, exposes what the Mac is playing as a normal input device."""

    def __init__(self) -> None:
        self.name = f"{DEVICE_PREFIX} ({os.getpid()})"
        self.uid = f"{UID_PREFIX}{os.getpid()}"
        self.tap_id: int | None = None
        self.device_id: int | None = None

    def __enter__(self) -> SystemAudioTap:
        usable, reason = available()
        if not usable:
            raise SystemAudioError(reason)

        import objc
        from CoreAudio import (
            AudioHardwareCreateAggregateDevice,
            AudioHardwareCreateProcessTap,
        )

        cleanup_stale()

        description = objc.lookUpClass("CATapDescription")
        desc = description.alloc().initStereoGlobalTapButExcludeProcesses_([])
        desc.setName_(_TAP_NAME)
        desc.setPrivate_(True)     # keep it out of everyone else's device list
        desc.setMuteBehavior_(0)   # unmuted: the call still plays out loud
        tap_uuid = desc.UUID().UUIDString()

        status, tap_id = AudioHardwareCreateProcessTap(desc, None)
        if status != 0:
            raise SystemAudioError(_explain(status))
        self.tap_id = tap_id

        status, device_id = AudioHardwareCreateAggregateDevice({
            "name": self.name,
            "uid": self.uid,
            "private": True,
            "stacked": False,
            "subdevices": [],
            "taps": [{"uid": tap_uuid, "drift": True}],
            "tapautostart": True,
        }, None)
        if status != 0:
            self.close()
            raise SystemAudioError(f"Could not build the audio device (OSStatus {status})")
        self.device_id = device_id

        if not _wait_until_visible(self.name):
            self.close()
            raise SystemAudioError(
                "The system-audio device was created but never showed up in the "
                "audio device list."
            )
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        from CoreAudio import (
            AudioHardwareDestroyAggregateDevice,
            AudioHardwareDestroyProcessTap,
        )
        had_device = self.device_id is not None
        if self.device_id is not None:
            AudioHardwareDestroyAggregateDevice(self.device_id)
            self.device_id = None
        if self.tap_id is not None:
            AudioHardwareDestroyProcessTap(self.tap_id)
            self.tap_id = None
        if had_device:
            _refresh_portaudio()


def _refresh_portaudio() -> None:
    """PortAudio snapshots the device list at init, so make it look again."""
    import sounddevice as sd

    sd._terminate()
    sd._initialize()


def _wait_until_visible(name: str, timeout: float = 5.0) -> bool:
    """A new aggregate device takes a moment to reach every audio client.

    Core Audio returns success before the device is enumerable, so creating one
    and immediately looking for it finds nothing — roughly 0.4s on an M-series
    Mac, but it is a propagation delay, not a fixed cost, so poll for it.
    """
    import sounddevice as sd

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _refresh_portaudio()
        for device in sd.query_devices():
            if device["name"] == name and device["max_input_channels"] > 0:
                return True
        time.sleep(0.2)
    return False


def _explain(status: int) -> str:
    # Core Audio reports a refusal as a generic failure, so name the likely cause.
    return (
        f"macOS refused the system-audio tap (OSStatus {status}). Grant this "
        "terminal permission under System Settings → Privacy & Security → "
        "Screen & System Audio Recording, then try again."
    )
