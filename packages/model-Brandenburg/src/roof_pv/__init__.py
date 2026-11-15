"""Reproducible Brandenburg rooftop PV image processing."""
import os
from pathlib import Path
import pyproj

# Use the installed wheel database rather than an unrelated inherited GIS install.
_proj_data = Path(pyproj.__file__).parent / "proj_dir" / "share" / "proj"
pyproj.datadir.set_data_dir(str(_proj_data))
os.environ["PROJ_DATA"] = str(_proj_data)
os.environ.pop("PROJ_LIB", None)
