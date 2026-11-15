"""Evidence-preserving array groups and provisional MaStR association rankings."""
from pathlib import Path
import pyproj

pyproj.datadir.set_data_dir(str(Path(pyproj.__file__).parent / "proj_dir/share/proj"))
