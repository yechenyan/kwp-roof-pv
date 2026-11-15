"""Overview envelopes cannot replace exact edit geometry; viewport responses stay bounded."""
from shapely.geometry import shape


def test_overview_cache_and_selected_detail_have_distinct_geometry_contracts(review):
    client, _, _, run_id, feature = review
    path = f"/api/runs/{run_id}"
    response = client.get(path + "/workspace")
    assert response.status_code == 200
    overview = response.json()
    assert overview["candidates"]["features"][0]["properties"]["geometry_is_envelope"]
    assert "original_geometry" not in overview["candidates"]["features"][0]["properties"]
    assert client.get(path + "/workspace", headers={"If-None-Match": response.headers["etag"]}).status_code == 304
    detail = client.get(path + "/candidates/test-candidate").json()
    assert shape(detail["geometry"]).equals_exact(shape(feature["geometry"]), 0)
    assert "original_geometry" in detail["properties"]
    client.patch(path + "/candidates/test-candidate", json={"expected_version": 0, "status": "confirmed", "reviewer": "Test"})
    refreshed = client.get(path + "/workspace", headers={"If-None-Match": response.headers["etag"]})
    assert refreshed.status_code == 200 and refreshed.json()["revision"] == 1


def test_viewport_filters_and_zoom_gate_do_not_fetch_all_geometry(review):
    client, _, _, run_id, feature = review
    bounds = shape(feature["geometry"]).buffer(.001).bounds
    query = {"bbox": ",".join(map(str, bounds)), "zoom": 16}
    path = f"/api/runs/{run_id}/view"
    data = client.get(path, params=query).json()
    assert data["counts"]["candidates"] == 1
    assert len(data["candidates"]["features"]) == 1
    assert client.get(path, params={**query, "status": "confirmed"}).json()["counts"]["candidates"] == 0
    assert not client.get(path, params={**query, "zoom": 12}).json()["candidates"]["features"]
    assert client.get(path, params={**query, "bbox": "1,2,0,3"}).status_code == 422
