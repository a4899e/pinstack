import os as _os
import re as _re


def _read_version() -> str:
    """Read version, preferring the local pyproject.toml for source checkouts."""
    # If running from a source checkout, pyproject.toml is the authoritative
    # version — it avoids stale egg-info/dist-info from previous installs.
    pyproject = _os.path.join(_os.path.dirname(__file__), _os.pardir, "pyproject.toml")
    try:
        with open(pyproject, encoding="utf-8") as fh:
            m = _re.search(r'^version\s*=\s*"([^"]+)"', fh.read(), _re.MULTILINE)
            if m:
                return m.group(1)
    except OSError:
        pass
    # Installed package without adjacent pyproject.toml (e.g. site-packages).
    from importlib.metadata import version, PackageNotFoundError

    try:
        return version("pinstack")
    except PackageNotFoundError:
        pass
    return "0.0.0"


__version__ = _read_version()
