"""Local-only review API and built React application."""
import argparse
import json
import os
from pathlib import Path
from typing import Literal
from fastapi import FastAPI, HTTPException, Request, Query, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator
from .store import Store, Conflict, checksum
from .export import export_snapshot
from .tiles import render_tile
from fastapi.middleware.gzip import GZipMiddleware
from .viewport import ViewportService
from .matching import MatchingService


class Review(BaseModel):
    expected_version: int = Field(ge=0)
    status: Literal["pending", "confirmed", "rejected", "uncertain"]
    reason: str = Field(default="", max_length=1000)
    note: str = Field(default="", max_length=5000)
    reviewer: str = Field(min_length=1, max_length=100)
    geometry: dict | None = None
    reset_geometry: bool = False
    mounting: Literal["unknown", "roof", "ground", "other"] | None = None

    @model_validator(mode="after")
    def reasons(self):
        if not self.reviewer.strip():
            raise ValueError("Reviewer is required.")
        if self.status in ["rejected", "uncertain"] and not self.reason.strip():
            raise ValueError("A reason is required for rejected or uncertain candidates.")
        return self


class Addition(BaseModel):
    geometry: dict
    reviewer: str = Field(min_length=1, max_length=100)
    note: str = Field(default="", max_length=5000)

    @model_validator(mode="after")
    def reviewer_required(self):
        if not self.reviewer.strip():
            raise ValueError("Reviewer is required.")
        return self


class TransformRequest(BaseModel):
    operation: Literal["merge", "split"]
    expected_versions: dict[str, int] = Field(min_length=1, max_length=30)
    reviewer: str = Field(min_length=1, max_length=100)
    note: str = Field(default="", max_length=5000)
    cut_line: dict | None = None

    @model_validator(mode="after")
    def valid_request(self):
        if not self.reviewer.strip() or any(v < 0 for v in self.expected_versions.values()):
            raise ValueError("Reviewer and non-negative expected versions are required.")
        if self.operation == "split" and not self.cut_line:
            raise ValueError("A cutting line is required for split.")
        return self


class UndoRequest(BaseModel):
    expected_revision: int = Field(ge=1)
    reviewer: str = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def reviewer_required(self):
        if not self.reviewer.strip():
            raise ValueError("Reviewer is required.")
        return self


class IdentityReview(BaseModel):
    batch_id: str = Field(min_length=1, max_length=100)
    solar_id: str = Field(pattern=r"^SEE\d+$")
    expected_version: int = Field(ge=0)
    state: Literal["pending", "rejected", "ambiguous", "probable_assignment", "verified_identity"]
    reviewer: str = Field(min_length=1, max_length=100)
    evidence_type: Literal["none", "attribute_comparison", "authorized_address", "operator_confirmation", "field_verification", "registry_coordinate_review"] = "none"
    evidence: str = Field(default="", max_length=3000)
    note: str = Field(default="", max_length=5000)
    group_confirmed: bool = False

    @model_validator(mode="after")
    def valid_identity_review(self):
        if not self.reviewer.strip():
            raise ValueError("Reviewer is required.")
        if self.state != "pending" and not self.evidence.strip():
            raise ValueError("Record the evidence or reason for this identity decision.")
        return self


def create_app(root=None, frontend=None):
    root = Path(root or os.environ.get("ROOF_PV_HOME", "var")).resolve()
    store = Store(root)
    store.import_runs()
    app = FastAPI(title="Roof PV Review", version="0.1.0")
    app.state.store = store
    viewport = ViewportService(store)
    app.state.viewport = viewport
    matching = MatchingService(store)
    app.state.matching = matching
    app.add_middleware(GZipMiddleware, minimum_size=1000, compresslevel=4)

    @app.middleware("http")
    async def same_origin_writes(request: Request, call_next):
        # A local service still needs to reject cross-origin browser writes.
        origin = request.headers.get("origin")
        allowed = {f"http://{request.headers.get('host')}", "http://127.0.0.1:5173", "http://localhost:5173"}
        if request.method not in ["GET", "HEAD", "OPTIONS"] and origin and origin not in allowed:
            return JSONResponse({"detail": "Cross-origin writes are disabled."}, status_code=403)
        return await call_next(request)

    @app.exception_handler(Conflict)
    async def conflict_handler(request, error):
        return JSONResponse({"detail": str(error)}, status_code=409)

    @app.exception_handler(KeyError)
    async def missing_handler(request, error):
        return JSONResponse({"detail": str(error)}, status_code=404)

    @app.exception_handler(ValueError)
    async def value_handler(request, error):
        return JSONResponse({"detail": str(error)}, status_code=422)

    @app.get("/api/health")
    def health():
        return {"status": "ok", "scope": "pv_image_and_registry_review", "database_schema": 4}

    @app.get("/api/jobs")
    def jobs():
        values = []
        acquisition = root / "data/municipal/acquisition.json"
        if acquisition.exists():
            data = json.loads(acquisition.read_text())
            values.append({"name": "Official imagery acquisition", "status": data["status"], "completed": sum(t["status"] == "completed" for t in data["tiles"]), "total": data["expected"]})
        for path in (root / "jobs").glob("*.json"):
            data = json.loads(path.read_text())
            values.append({"name": data.get("name", "Municipal PV inference"), "run_id": data.get("run_id"), "status": data["status"], "completed": data.get("completed", data.get("completed_blocks", 0)), "total": data.get("total", data.get("total_blocks", 0)), "updated_at": data.get("updated_at")})
        return values

    @app.get("/api/runs")
    def runs():
        store.import_runs()
        with store.connection() as db:
            return [{"run_id": row["id"], "revision": row["revision"], "manifest": json.loads(row["manifest"])} for row in db.execute("SELECT * FROM runs ORDER BY id")]

    @app.get("/api/runs/{run_id}")
    def snapshot(run_id: str):
        return store.snapshot(run_id, event_limit=50)

    @app.get("/api/runs/{run_id}/workspace")
    def workspace(run_id: str, request: Request):
        data = viewport.state(run_id)["overview"]
        tag = f'"{data["manifest_sha256"][:16]}-r{data["revision"]}-overview-v1"'
        headers = {"ETag": tag, "Cache-Control": "private, no-cache"}
        if request.headers.get("if-none-match") == tag:
            return Response(status_code=304, headers=headers)
        return JSONResponse(data, headers=headers)

    @app.get("/api/runs/{run_id}/candidates/{candidate_id}")
    def candidate_detail(run_id: str, candidate_id: str):
        return viewport.detail(run_id, candidate_id)

    @app.get("/api/runs/{run_id}/view")
    def view(run_id: str, bbox: str, zoom: int = Query(ge=0, le=24), layers: str = "",
             status: str = "all", category: str = "all", building: str = "all"):
        return viewport.view(run_id, [float(v) for v in bbox.split(",")], zoom, [v for v in layers.split(",") if v],
                             {"status": status, "category": category, "building": building})

    @app.get("/api/runs/{run_id}/matching")
    def matching_summary(run_id: str):
        return matching.summary(run_id)

    @app.post("/api/runs/{run_id}/matching/build", status_code=202)
    def matching_build(run_id: str, tasks: BackgroundTasks):
        with store.connection() as db:
            store.manifest(db, run_id)
        matching.registry()
        matching.reserve_build(run_id)
        tasks.add_task(matching.rebuild, run_id, True)
        return {"status": "scheduled"}

    @app.get("/api/runs/{run_id}/matching/units")
    def registry_units(run_id: str, q: str = Query(default="", max_length=100), cohort: Literal["all", "target", "coordinates"] = "target", offset: int = Query(default=0, ge=0), limit: int = Query(default=30, ge=1, le=100), association: Literal["all", "unreviewed", "associated", "verified"] = "all"):
        return matching.units(run_id, q, cohort, offset, limit, association)

    @app.get("/api/runs/{run_id}/matching/units/{solar_id}")
    def registry_unit(run_id: str, solar_id: str):
        return matching.unit(run_id, solar_id)

    @app.get("/api/runs/{run_id}/matching/candidates/{candidate_id}")
    def candidate_associations(run_id: str, candidate_id: str):
        return matching.candidate(run_id, candidate_id)

    @app.get("/api/runs/{run_id}/matching/groups/{group_id}")
    def group_associations(run_id: str, group_id: str):
        return matching.group(run_id, group_id)

    @app.patch("/api/runs/{run_id}/matching/groups/{group_id}")
    def identity_review(run_id: str, group_id: str, data: IdentityReview):
        return matching.review(run_id, group_id, data.model_dump())

    @app.get("/api/runs/{run_id}/tiles/{display}/{z}/{x}/{y}.png")
    def tile(run_id: str, display: str, z: int, x: int, y: int):
        with store.connection() as db:
            store.manifest(db, run_id)
        return FileResponse(render_tile(root, run_id, display, z, x, y), media_type="image/png", headers={"Cache-Control": "private, max-age=31536000, immutable"})

    @app.post("/api/runs/{run_id}/transform", status_code=201)
    def transform_candidates(run_id: str, data: TransformRequest):
        return {"ids": store.transform_candidates(run_id, data.model_dump())}

    @app.post("/api/runs/{run_id}/undo")
    def undo(run_id: str, data: UndoRequest):
        store.undo(run_id, data.expected_revision, data.reviewer)
        return {"status": "undone"}

    @app.get("/api/runs/{run_id}/assets/{asset}")
    def asset(run_id: str, asset: str):
        if asset not in ["preview.png", "preview_cir.png", "rgbi.geojson", "rgb.geojson", "buildings.geojson", "municipalities.geojson", "pixel_coverage.geojson"]:
            raise HTTPException(404, "Unknown preview asset.")
        with store.connection() as db:
            manifest = store.manifest(db, run_id)
        file = root / "runs" / run_id / asset
        if asset not in manifest["artifacts"]:
            raise HTTPException(404, "Asset was not included in this run.")
        if checksum(file) != manifest["artifacts"][asset]:
            raise Conflict("Immutable preview asset changed.")
        return FileResponse(file)

    @app.patch("/api/runs/{run_id}/candidates/{candidate_id}")
    def review(run_id: str, candidate_id: str, data: Review):
        return store.update(run_id, candidate_id, data.model_dump())

    @app.post("/api/runs/{run_id}/candidates", status_code=201)
    def add(run_id: str, data: Addition):
        return {"id": store.add(run_id, data.geometry, data.reviewer, data.note)}

    @app.post("/api/runs/{run_id}/reviewed-areas", status_code=201)
    def area(run_id: str, data: Addition):
        return {"id": store.add(run_id, data.geometry, data.reviewer, data.note, "area")}

    @app.post("/api/runs/{run_id}/exports", status_code=201)
    def export(run_id: str):
        try:
            matching.batch(run_id)
        except KeyError:
            bundle = None
        else:
            bundle = matching.export_bundle(run_id)
        result = export_snapshot(root, bundle["image_snapshot"] if bundle else store.snapshot(run_id), bundle)
        result["download_url"] = f"/api/exports/{result['export_id']}.gpkg"
        result["report_url"] = f"/api/exports/{result['export_id']}/report"
        return result

    @app.get("/api/exports/{export_id}/report")
    def report(export_id: str):
        if Path(export_id).name != export_id:
            raise HTTPException(404, "Unknown report.")
        file = root / "exports" / (export_id + ".html")
        metadata = file.with_suffix(".json")
        if not file.exists() or not metadata.exists():
            raise HTTPException(404, "Unknown report.")
        data = json.loads(metadata.read_text())
        if checksum(file) != data.get("report_sha256"):
            raise Conflict("Report failed integrity verification.")
        return FileResponse(file, media_type="text/html")

    @app.get("/api/exports/{filename}")
    def download(filename: str):
        if Path(filename).name != filename or not filename.endswith(".gpkg"):
            raise HTTPException(404, "Unknown export.")
        output = root / "exports" / filename
        metadata = output.with_suffix(".json")
        if not output.is_file() or not metadata.is_file():
            raise HTTPException(404, "Unknown export.")
        if checksum(output) != json.loads(metadata.read_text())["sha256"]:
            raise Conflict("Export failed integrity verification.")
        return FileResponse(output, filename=filename, media_type="application/geopackage+sqlite3")

    comparison_path = Path("packages/model-diff").resolve()
    if comparison_path.is_dir():
        @app.get("/comparison/")
        def comparison():
            return RedirectResponse("/comparison/report/")

        app.mount("/comparison", StaticFiles(directory=comparison_path, html=True), name="comparison")
    frontend_path = Path(frontend or os.environ.get("ROOF_PV_FRONTEND", "packages/web-app/frontend/dist")).resolve()
    if frontend_path.is_dir():
        app.mount("/", StaticFiles(directory=frontend_path, html=True), name="frontend")
    return app


def main():
    import uvicorn
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path)
    parser.add_argument("--frontend", type=Path)
    parser.add_argument("--port", type=int, default=8780)
    args = parser.parse_args()
    uvicorn.run(create_app(args.home, args.frontend), host="127.0.0.1", port=args.port)
