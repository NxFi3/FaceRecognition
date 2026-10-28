"""Never let a pilot run touch the normal school database or production build."""
import os
from pathlib import Path


def active_pilot_root(source_root):
    if os.environ.get("NXFI_LOCAL_PILOT") != "1":
        return None
    if getattr(__import__("sys"), "frozen", False):
        raise RuntimeError("Pilot paths are forbidden in packaged releases")
    raw = os.environ.get("NXFI_PILOT_ROOT", "")
    if not raw or not Path(raw).is_absolute():
        raise RuntimeError("Pilot requires an absolute NXFI_PILOT_ROOT")
    target = Path(raw).resolve()
    source = Path(source_root).resolve()
    if target == source or source in target.parents or target == Path.home().resolve():
        raise RuntimeError("Pilot data must be separate from source and home directories")
    if not (target / ".nxfi-local-pilot").is_file():
        raise RuntimeError("Pilot data directory lacks its isolated ownership marker")
    return target
