"""Cross-platform master volume control with linear amplification mapping.

The UI slider produces a value ``x`` in the range ``[0, 1.5]``.  This module
converts ``x`` to a linear amplitude coefficient ``amp`` using:

    amp = 0.02 * x                 if x < 0.1
    amp = (exp(6 * x) - 1) / 402.42879349   otherwise

``amp`` is then applied to the system mixer in a platform-specific way:

* **Linux / PipeWire** – ``wpctl`` accepts linear values > 1.0 natively, so
  amplification up to ~150 % (and beyond) works without clipping.
* **Windows** – ``IAudioEndpointVolume`` uses an audio-tapered scalar and does
  not support > 100 %.  ``amp`` is clamped to 1.0 and mapped to the scalar.
* **macOS** – ``osascript`` sets a 0-100 slider and does not support > 100 %.
  ``amp`` is clamped to 1.0 and mapped to that range.
"""

from __future__ import annotations

import logging
import math
import subprocess
import sys
import threading
from collections.abc import Callable

logger = logging.getLogger("mediahive.volume")

# ---------------------------------------------------------------------------
# Linear amplitude formula
# ---------------------------------------------------------------------------

_AMP_DENOMINATOR = 402.42879349


def slider_to_amp(x: float) -> float:
    """Convert UI slider position ``x`` (0.0 .. 1.5) to linear amplitude.

    Returns a value in the range ``[0, ~20]`` where ``1.0`` means unity gain.
    """
    if x <= 0.0:
        return 0.0
    if x < 0.1:
        return 0.02 * x
    return (math.exp(6.0 * x) - 1.0) / _AMP_DENOMINATOR


def amp_to_slider(amp: float) -> float:
    """Inverse of :func:`slider_to_amp` for the ``x >= 0.1`` branch.

    Returns a slider position in ``[0.1, 1.5]`` (or slightly above when the
    platform reports amplification > 1.0).
    """
    if amp <= 0.0:
        return 0.0
    # For very small values we fall back to the linear branch
    if amp < 0.002042892590415348:  # value at x == 0.1
        return amp / 0.02
    return math.log(amp * _AMP_DENOMINATOR + 1.0) / 6.0


# ---------------------------------------------------------------------------
# Platform backends
# ---------------------------------------------------------------------------


def _set_volume_windows(amp: float) -> None:
    """Set master volume on Windows via ``IAudioEndpointVolume`` (ctypes)."""
    import ctypes
    import uuid
    from ctypes import (
        POINTER,
        Structure,
        byref,
        c_float,
        c_uint32,
        c_void_p,
        cast,
        wintypes,
    )

    class GUID(Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", wintypes.BYTE * 8),
        ]

    def _uuid_to_guid(u: uuid.UUID) -> GUID:
        g = GUID()
        g.Data1 = u.time_low
        g.Data2 = u.time_mid
        g.Data3 = u.time_hi_version
        g.Data4[0] = u.clock_seq_hi_variant & 0xFF
        g.Data4[1] = u.clock_seq_low & 0xFF
        for i in range(6):
            g.Data4[2 + i] = (u.node >> (40 - i * 8)) & 0xFF
        return g

    CLSID_MMDeviceEnumerator = _uuid_to_guid(
        uuid.UUID("{BCDE0395-E52F-467C-8E3D-C4579291692E}")
    )
    IID_IMMDeviceEnumerator = _uuid_to_guid(
        uuid.UUID("{A95664D2-9614-4F35-A746-DE8DB63617E6}")
    )
    IID_IAudioEndpointVolume = _uuid_to_guid(
        uuid.UUID("{5CDF2C82-841E-4546-9722-0CF74078229A}")
    )
    IID_IMMDevice = _uuid_to_guid(uuid.UUID("{D666063F-1587-4E43-81F1-B948E807363F}"))

    CLSCTX_ALL = 23

    ole32 = ctypes.windll.ole32
    ole32.CoInitializeEx(None, 0)

    CoCreateInstance = ole32.CoCreateInstance
    CoCreateInstance.argtypes = [
        POINTER(GUID),
        c_void_p,
        wintypes.DWORD,
        POINTER(GUID),
        POINTER(c_void_p),
    ]
    CoCreateInstance.restype = wintypes.HRESULT

    enumerator_ptr = c_void_p()
    hr = CoCreateInstance(
        byref(CLSID_MMDeviceEnumerator),
        None,
        CLSCTX_ALL,
        byref(IID_IMMDeviceEnumerator),
        byref(enumerator_ptr),
    )
    if hr != 0:
        raise OSError(f"CoCreateInstance failed: {hr:#x}")

    try:
        vtable_ptr = cast(enumerator_ptr, POINTER(c_void_p)).contents
        vtable = cast(vtable_ptr, POINTER(c_void_p * 6))
        GetDefaultAudioEndpoint = ctypes.WINFUNCTYPE(
            wintypes.HRESULT, c_void_p, c_uint32, c_uint32, POINTER(c_void_p)
        )(vtable[0][4])

        device_ptr = c_void_p()
        hr = GetDefaultAudioEndpoint(enumerator_ptr, 0, 0, byref(device_ptr))
        if hr != 0:
            raise OSError(f"GetDefaultAudioEndpoint failed: {hr:#x}")

        try:
            device_vtable_ptr = cast(device_ptr, POINTER(c_void_p)).contents
            device_vtable = cast(device_vtable_ptr, POINTER(c_void_p * 7))
            Activate = ctypes.WINFUNCTYPE(
                wintypes.HRESULT,
                c_void_p,
                POINTER(GUID),
                wintypes.DWORD,
                c_void_p,
                POINTER(c_void_p),
            )(device_vtable[0][3])

            volume_ptr = c_void_p()
            hr = Activate(
                device_ptr,
                byref(IID_IAudioEndpointVolume),
                CLSCTX_ALL,
                None,
                byref(volume_ptr),
            )
            if hr != 0:
                raise OSError(f"Activate(IAudioEndpointVolume) failed: {hr:#x}")

            try:
                vol_vtable_ptr = cast(volume_ptr, POINTER(c_void_p)).contents
                vol_vtable = cast(vol_vtable_ptr, POINTER(c_void_p * 22))

                GetVolumeRange = ctypes.WINFUNCTYPE(
                    wintypes.HRESULT,
                    c_void_p,
                    POINTER(c_float),
                    POINTER(c_float),
                    POINTER(c_float),
                )(vol_vtable[0][20])

                min_db = c_float()
                max_db = c_float()
                step_db = c_float()
                hr = GetVolumeRange(
                    volume_ptr, byref(min_db), byref(max_db), byref(step_db)
                )
                if hr != 0:
                    raise OSError(f"GetVolumeRange failed: {hr:#x}")

                SetMasterVolumeLevel = ctypes.WINFUNCTYPE(
                    wintypes.HRESULT, c_void_p, c_float, POINTER(GUID)
                )(vol_vtable[0][6])
                SetMasterVolumeLevelScalar = ctypes.WINFUNCTYPE(
                    wintypes.HRESULT, c_void_p, c_float, POINTER(GUID)
                )(vol_vtable[0][7])

                # Windows does not natively support >100% system volume.
                # Clamp to unity gain and map to the audio-tapered scalar.
                # The exponent 0.573 was empirically derived from measurements
                # on a reference Windows system.
                clamped = min(amp, 1.0)
                scalar = clamped**0.573
                SetMasterVolumeLevelScalar(volume_ptr, scalar, None)
            finally:
                Release = ctypes.WINFUNCTYPE(wintypes.ULONG, c_void_p)(vol_vtable[0][2])
                Release(volume_ptr)
        finally:
            Release = ctypes.WINFUNCTYPE(wintypes.ULONG, c_void_p)(device_vtable[0][2])
            Release(device_ptr)
    finally:
        Release = ctypes.WINFUNCTYPE(wintypes.ULONG, c_void_p)(vtable[0][2])
        Release(enumerator_ptr)


def _get_volume_windows() -> float:
    """Return current linear amplitude on Windows."""
    import ctypes
    import uuid
    from ctypes import (
        POINTER,
        Structure,
        byref,
        c_float,
        c_uint32,
        c_void_p,
        cast,
        wintypes,
    )

    class GUID(Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", wintypes.BYTE * 8),
        ]

    def _uuid_to_guid(u: uuid.UUID) -> GUID:
        g = GUID()
        g.Data1 = u.time_low
        g.Data2 = u.time_mid
        g.Data3 = u.time_hi_version
        g.Data4[0] = u.clock_seq_hi_variant & 0xFF
        g.Data4[1] = u.clock_seq_low & 0xFF
        for i in range(6):
            g.Data4[2 + i] = (u.node >> (40 - i * 8)) & 0xFF
        return g

    CLSID_MMDeviceEnumerator = _uuid_to_guid(
        uuid.UUID("{BCDE0395-E52F-467C-8E3D-C4579291692E}")
    )
    IID_IMMDeviceEnumerator = _uuid_to_guid(
        uuid.UUID("{A95664D2-9614-4F35-A746-DE8DB63617E6}")
    )
    IID_IAudioEndpointVolume = _uuid_to_guid(
        uuid.UUID("{5CDF2C82-841E-4546-9722-0CF74078229A}")
    )
    IID_IMMDevice = _uuid_to_guid(uuid.UUID("{D666063F-1587-4E43-81F1-B948E807363F}"))

    CLSCTX_ALL = 23

    ole32 = ctypes.windll.ole32
    ole32.CoInitializeEx(None, 0)

    CoCreateInstance = ole32.CoCreateInstance
    CoCreateInstance.argtypes = [
        POINTER(GUID),
        c_void_p,
        wintypes.DWORD,
        POINTER(GUID),
        POINTER(c_void_p),
    ]
    CoCreateInstance.restype = wintypes.HRESULT

    enumerator_ptr = c_void_p()
    hr = CoCreateInstance(
        byref(CLSID_MMDeviceEnumerator),
        None,
        CLSCTX_ALL,
        byref(IID_IMMDeviceEnumerator),
        byref(enumerator_ptr),
    )
    if hr != 0:
        raise OSError(f"CoCreateInstance failed: {hr:#x}")

    try:
        vtable_ptr = cast(enumerator_ptr, POINTER(c_void_p)).contents
        vtable = cast(vtable_ptr, POINTER(c_void_p * 6))
        GetDefaultAudioEndpoint = ctypes.WINFUNCTYPE(
            wintypes.HRESULT, c_void_p, c_uint32, c_uint32, POINTER(c_void_p)
        )(vtable[0][4])

        device_ptr = c_void_p()
        hr = GetDefaultAudioEndpoint(enumerator_ptr, 0, 0, byref(device_ptr))
        if hr != 0:
            raise OSError(f"GetDefaultAudioEndpoint failed: {hr:#x}")

        try:
            device_vtable_ptr = cast(device_ptr, POINTER(c_void_p)).contents
            device_vtable = cast(device_vtable_ptr, POINTER(c_void_p * 7))
            Activate = ctypes.WINFUNCTYPE(
                wintypes.HRESULT,
                c_void_p,
                POINTER(GUID),
                wintypes.DWORD,
                c_void_p,
                POINTER(c_void_p),
            )(device_vtable[0][3])

            volume_ptr = c_void_p()
            hr = Activate(
                device_ptr,
                byref(IID_IAudioEndpointVolume),
                CLSCTX_ALL,
                None,
                byref(volume_ptr),
            )
            if hr != 0:
                raise OSError(f"Activate failed: {hr:#x}")

            try:
                vol_vtable_ptr = cast(volume_ptr, POINTER(c_void_p)).contents
                vol_vtable = cast(vol_vtable_ptr, POINTER(c_void_p * 22))

                GetMasterVolumeLevel = ctypes.WINFUNCTYPE(
                    wintypes.HRESULT, c_void_p, POINTER(c_float)
                )(vol_vtable[0][8])
                GetMasterVolumeLevelScalar = ctypes.WINFUNCTYPE(
                    wintypes.HRESULT, c_void_p, POINTER(c_float)
                )(vol_vtable[0][9])

                db = c_float()
                hr = GetMasterVolumeLevel(volume_ptr, byref(db))
                if hr == 0 and db.value > -90.0:
                    return 10.0 ** (db.value / 20.0)

                scalar = c_float()
                hr = GetMasterVolumeLevelScalar(volume_ptr, byref(scalar))
                if hr != 0:
                    raise OSError(f"GetMasterVolumeLevelScalar failed: {hr:#x}")
                # Invert the audio-tapered mapping
                return scalar.value ** (1.0 / 0.573)
            finally:
                Release = ctypes.WINFUNCTYPE(wintypes.ULONG, c_void_p)(vol_vtable[0][2])
                Release(volume_ptr)
        finally:
            Release = ctypes.WINFUNCTYPE(wintypes.ULONG, c_void_p)(device_vtable[0][2])
            Release(device_ptr)
    finally:
        Release = ctypes.WINFUNCTYPE(wintypes.ULONG, c_void_p)(vtable[0][2])
        Release(enumerator_ptr)


def _set_volume_macos(amp: float) -> None:
    """Set master volume on macOS via AppleScript."""
    # macOS does not natively support >100% system volume.
    clamped = min(amp, 1.0)
    level = int(clamped * 100)
    subprocess.run(
        ["osascript", "-e", f"set volume output volume {level}"],
        check=False,
        capture_output=True,
    )


def _get_volume_macos() -> float:
    """Return current linear amplitude on macOS."""
    result = subprocess.run(
        ["osascript", "-e", "output volume of (get volume settings)"],
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        return int(result.stdout.strip()) / 100.0
    except ValueError, AttributeError:
        return 1.0


def _set_volume_linux(amp: float) -> None:
    """Set master volume on Linux via PipeWire (``wpctl``)."""
    # wpctl is the native PipeWire CLI and accepts linear values > 1.0
    # directly.  We do not fall back to PulseAudio tools.
    subprocess.run(
        ["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", str(amp)],
        check=True,
        capture_output=True,
    )


def _get_volume_linux() -> float:
    """Return current linear amplitude on Linux via PipeWire (``wpctl``)."""
    result = subprocess.run(
        ["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"],
        capture_output=True,
        text=True,
        check=True,
    )
    # Output: "Volume: 0.40" or "Volume: 0.50 [MUTED]"
    line = result.stdout.strip()
    parts = line.split()
    return float(parts[1])


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_setter: Callable[[float], None] | None = None
_getter: Callable[[], float] | None = None
_lock = threading.Lock()


def _init_backend() -> None:
    """Lazily select the platform backend."""
    global _setter, _getter
    with _lock:
        if _setter is not None:
            return
        if sys.platform == "win32":
            _setter = _set_volume_windows
            _getter = _get_volume_windows
        elif sys.platform == "darwin":
            _setter = _set_volume_macos
            _getter = _get_volume_macos
        else:
            _setter = _set_volume_linux
            _getter = _get_volume_linux


def set_volume(x: float) -> None:
    """Set master volume from slider position ``x`` (0.0 .. 1.5).

    The value is converted to a linear amplitude coefficient and applied to
    the system mixer using the platform-native API.
    """
    _init_backend()
    amp = slider_to_amp(x)
    assert _setter is not None
    try:
        _setter(amp)
    except Exception:
        logger.exception("Failed to set volume (x=%.3f, amp=%.6f)", x, amp)


def get_volume() -> float:
    """Return the current slider position (0.0 .. 1.5) by reading the OS mixer.

    On platforms that do not support amplification above 100 % the returned
    value will never exceed ``1.0``.
    """
    _init_backend()
    assert _getter is not None
    try:
        amp = _getter()
    except Exception:
        logger.exception("Failed to get volume")
        return 1.0
    return amp_to_slider(amp)


def volume_max() -> float:
    """Return the maximum slider position supported on this platform.

    * Linux / PipeWire – ``1.5`` (amplification above 100 % is supported).
    * Windows / macOS  – ``1.0`` (the OS mixer does not amplify above unity).
    """
    if sys.platform in ("win32", "darwin"):
        return 1.0
    return 1.5
