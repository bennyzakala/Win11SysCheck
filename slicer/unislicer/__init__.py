"""UniSlicer - a universal slicer for FDM and resin (SLA/MSLA) 3D printers."""
__version__ = "1.0.0"
try:                                   # the Windows build stamps its own number: 1.0.<build number>
    from ._build import VERSION as __version__  # noqa: F401
except Exception:                      # run from source: no stamp
    pass
