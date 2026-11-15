"""Pinned inference; torch is imported only by the inference process."""
from pathlib import Path, PosixPath
import numpy as np
import rasterio
from rasterio.windows import Window
from .io import digest


def starts(length, size=256, step=232):
    if length < size:
        raise ValueError("Image is smaller than a native inference window.")
    return sorted({min(position, length - size) for position in range(0, length, step)})


def load_model(checkpoint, expected, channels):
    import torch
    import segmentation_models_pytorch as smp
    if digest(checkpoint) != expected:
        raise ValueError("Checkpoint SHA-256 mismatch.")
    torch.set_num_threads(2)
    with torch.serialization.safe_globals([(PosixPath, "pathlib._local.PosixPath")]):
        data = torch.load(checkpoint, map_location="cpu", weights_only=True)
    cfg = data["config"]
    if cfg["backbone"] != "timm-regnety_080" or cfg["num_classes"] != 2 or (4 if cfg.get("add_nir") else 3) != channels or any(cfg.get(key) for key in ["add_land_use", "add_ndvi", "add_gndvi"]):
        raise ValueError("Checkpoint architecture differs from the pinned contract.")
    model = smp.DeepLabV3Plus(encoder_name=cfg["backbone"], encoder_weights=None, in_channels=channels, classes=2)
    model.load_state_dict({key.removeprefix("model."): value for key, value in data["model_state_dict"].items()}, strict=True)
    return model.eval()


def run_model(source, checkpoint, config, variant, destination):
    import torch
    destination = Path(destination)
    channels = len(config["models"][variant]["channels"])
    model = load_model(checkpoint, config["models"][variant]["sha256"], channels)
    with rasterio.open(source) as src:
        shape = (src.height, src.width)
        total_path, count_path = destination / "sum.tmp", destination / "count.tmp"
        total = np.memmap(total_path, dtype="float32", mode="w+", shape=shape)
        count = np.memmap(count_path, dtype="uint16", mode="w+", shape=shape)
        total[:] = 0
        count[:] = 0
        means = np.asarray(config["mean"][:channels], dtype=np.float32)[:, None, None]
        stds = np.asarray(config["std"][:channels], dtype=np.float32)[:, None, None]
        windows = 0
        with torch.inference_mode():
            for y in starts(src.height):
                for x in starts(src.width):
                    window = Window(x, y, 256, 256)
                    bands = src.read(list(range(1, channels + 1)), window=window)
                    valid = np.all(src.read_masks(window=window) > 0, axis=0)
                    bands[:, ~valid] = 0
                    normalized = (bands.astype(np.float32) / config["scale"] - means) / stds
                    prediction = torch.sigmoid(model(torch.from_numpy(normalized).unsqueeze(0))[:, 1]).squeeze().numpy()
                    total[y:y + 256, x:x + 256] += prediction * valid
                    count[y:y + 256, x:x + 256] += valid
                    windows += 1
        profile = src.profile.copy()
        profile.update(count=1, dtype="float32", nodata=-1, compress="deflate")
        with rasterio.open(destination / f"{variant}_scores.tif", "w", **profile) as scores:
            mask_profile = {**profile, "dtype": "uint8", "nodata": 255}
            with rasterio.open(destination / f"{variant}_mask.tif", "w", **mask_profile) as masks:
                for _, window in scores.block_windows(1):
                    rows, cols = window.toslices()
                    n = np.asarray(count[rows, cols])
                    score = np.divide(total[rows, cols], n, out=np.full(n.shape, -1, np.float32), where=n > 0)
                    scores.write(score, 1, window=window)
                    masks.write(np.where(n > 0, score >= config["pixel_threshold"], 255).astype(np.uint8), 1, window=window)
        del total, count
        total_path.unlink()
        count_path.unlink()
    del model
    return {"windows": windows, "device": "cpu", "threads": 2, "score_dtype": "float32"}
