from importlib.metadata import version as _version, PackageNotFoundError


def _read_version() -> str:
    """Read version from installed metadata, falling back to pyproject.toml."""
    try:
        return _version("pinstack")
    except PackageNotFoundError:
        pass
    # Running from a source checkout without installation — parse pyproject.toml.
    import os
    import re
    pyproject = os.path.join(os.path.dirname(__file__), os.pardir, "pyproject.toml")
    try:
        with open(pyproject, encoding="utf-8") as fh:
            m = re.search(r'^version\s*=\s*"([^"]+)"', fh.read(), re.MULTILINE)
            if m:
                return m.group(1)
    except OSError:
        pass
    return "0.0.0"


__version__ = _read_version()
