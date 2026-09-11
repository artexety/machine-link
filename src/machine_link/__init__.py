"""One command to prepare a rented or local machine for work."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("machine-link")
except PackageNotFoundError:  # a checkout that was never installed
    __version__ = "0+unknown"
