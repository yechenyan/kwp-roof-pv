"""Small topology normalization is explicit; substantive shape changes are rejected."""
import pytest
from roof_pv_review.export import layer


def feature(coordinates):
    return {"type":"Feature", "geometry":{"type":"Polygon","coordinates":[coordinates]}, "properties":{"id":"context"}}


def test_zero_area_context_spike_is_repaired_and_documented():
    ring = [[13,52],[13.0001,52],[13.0001,52.0001],[13.00005,52.0001],[13.00005,52.000100001],[13.00005,52.0001],[13,52.0001],[13,52]]
    frame=layer([feature(ring)])
    assert frame.geometry.is_valid.all()
    assert frame.geometry.iloc[0].geom_type == "Polygon"
    assert bool(frame.geometry_repaired_for_export.iloc[0])
    assert frame.geometry_repair_area_delta_m2.iloc[0] <= .01
    assert "make_valid" in frame.geometry_export_repair_reason.iloc[0]


def test_large_bowtie_change_requires_manual_correction():
    ring = [[13,52],[13.0001,52.0001],[13,52.0001],[13.0001,52],[13,52]]
    with pytest.raises(ValueError,match="manual correction"):
        layer([feature(ring)])
