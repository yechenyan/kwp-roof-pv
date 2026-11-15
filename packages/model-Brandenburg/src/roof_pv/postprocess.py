"""Independent model observations and explicit, non-unioned review candidates."""
import numpy as np
from collections import Counter, defaultdict
import rasterio
from rasterio.features import shapes
from scipy import ndimage
from shapely.geometry import shape, mapping
from shapely.ops import transform
from pyproj import Transformer
from shapely import STRtree


def detections(directory, variant, run_id, config):
    features = []
    with rasterio.open(directory / f"{variant}_mask.tif") as src, rasterio.open(directory / f"{variant}_scores.tif") as scores:
        labels, count = ndimage.label(src.read(1) == 1)
        sizes = np.bincount(labels.ravel())
        keep = np.zeros(count + 1, dtype=bool)
        keep[1:] = sizes[1:] >= config["minimum_pixels"]
        project = Transformer.from_crs(src.crs, 4326, always_xy=True).transform
        score = scores.read(1)
        for geometry, value in shapes(labels.astype(np.int32), mask=keep[labels], transform=src.transform, connectivity=4):
            label = int(value)
            geom = shape(geometry)
            feature_id = f"{run_id}:{variant}:{label}"
            features.append({"type": "Feature", "id": feature_id, "geometry": mapping(transform(project, geom)), "properties": {"id": feature_id, "run_id": run_id, "model": variant, "pixels": int(sizes[label]), "projected_area_m2": float(geom.area), "score_mean": float(score[labels == label].mean()), "score_is_calibrated": False}})
    return features


def compare(rgbi, rgb, run_id, agreement_iou=0.5):
    """Preserve each RGBI geometry; attach intersecting RGB evidence without union."""
    project = Transformer.from_crs(4326, 3035, always_xy=True).transform
    left = [transform(project, shape(f["geometry"])) for f in rgbi]
    right = [transform(project, shape(f["geometry"])) for f in rgb]
    tree = STRtree(right)
    links = []
    for i, a in enumerate(left):
        for item in tree.query(a, predicate="intersects"):
            j = int(item)
            b = right[j]
            overlap = a.intersection(b).area
            if overlap > 0:
                links.append((i, j, overlap / a.union(b).area))
    candidates = []
    by_left = defaultdict(list)
    right_counts = Counter()
    for i, j, iou in links:
        by_left[i].append((j, iou))
        right_counts[j] += 1
    for i, feature in enumerate(rgbi):
        associated = by_left[i]
        one_to_one = len(associated) == 1 and right_counts[associated[0][0]] == 1
        category = "both" if one_to_one and associated[0][1] >= agreement_iou else "geometry_conflict" if associated else "rgbi_only"
        candidates.append({"type": "Feature", "id": f"{run_id}:candidate:{len(candidates) + 1}", "geometry": feature["geometry"], "properties": {"category": category, "default_model": "rgbi", "observation_ids": [feature["id"]] + [rgb[j]["id"] for j, _ in associated], "comparison_ious": [iou for _, iou in associated], "score_mean": feature["properties"]["score_mean"]}})
    used = {j for _, j, _ in links}
    for j, feature in enumerate(rgb):
        if j not in used:
            candidates.append({"type": "Feature", "id": f"{run_id}:candidate:{len(candidates) + 1}", "geometry": feature["geometry"], "properties": {"category": "rgb_only", "default_model": "rgb", "observation_ids": [feature["id"]], "comparison_ious": [], "score_mean": feature["properties"]["score_mean"]}})
    for feature in candidates:
        feature["properties"].update(id=feature["id"], run_id=run_id)
    return candidates


def collection(features):
    return {"type": "FeatureCollection", "features": features}
