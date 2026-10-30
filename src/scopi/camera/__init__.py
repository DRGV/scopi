from .base import CameraBackend, CameraError, CameraInfo, PermissionDenied, Range
from .simulated import SimulatedCamera

BACKENDS = {"simulated": SimulatedCamera}


def available_backends() -> dict:
    """Backend name -> class, including those whose import/SDK is available on this machine."""
    from .touptek import TouptekCamera
    from .v4l2 import V4L2Camera

    return {"touptek": TouptekCamera, "v4l2": V4L2Camera, "simulated": SimulatedCamera}


def enumerate_all() -> list[tuple[type, CameraInfo]]:
    found = []
    for name, cls in available_backends().items():
        if name == "simulated":
            continue
        try:
            for info in cls.enumerate():
                found.append((cls, info))
        except Exception:
            continue
    return found


__all__ = ["BACKENDS", "CameraBackend", "CameraError", "CameraInfo", "PermissionDenied", "Range", "SimulatedCamera",
           "available_backends", "enumerate_all"]
