"""Serve the delivered result page and versioned confidence annotations locally."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from threading import Lock

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.gzip import GZipMiddleware
from pydantic import BaseModel, Field
import uvicorn
from build import APP, read, export_gpkg, digest


class Annotation(BaseModel):
    version: int = Field(ge=0)
    confidence: str
    reason: str = Field(min_length=10, max_length=4000)


def create_app(directory=None, database=None):
    directory=Path(directory or APP/"dist").resolve()
    database=Path(database or APP/"state"/"annotations.sqlite3").resolve()
    database.parent.mkdir(parents=True,exist_ok=True)
    records=read(directory/"records.json")
    source_hash=digest(directory/"records.json")
    by_id={r["solar_id"]:r for r in records}
    lock=Lock()
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE IF NOT EXISTS annotations(solar_id TEXT PRIMARY KEY,confidence TEXT,reason TEXT,version INTEGER,updated_at TEXT)")
        if "source_hash" not in {r[1] for r in db.execute("PRAGMA table_info(annotations)")}:
            db.execute("ALTER TABLE annotations ADD COLUMN source_hash TEXT")
        db.execute("CREATE TABLE IF NOT EXISTS annotation_events(id INTEGER PRIMARY KEY,solar_id TEXT,payload TEXT,created_at TEXT)")
    app=FastAPI(title="Roof PV results",version="1.0")
    app.add_middleware(GZipMiddleware,minimum_size=1024)

    def current_source():
        if digest(directory/"records.json") != source_hash:
            raise HTTPException(409,"Result delivery changed. Restart the result server.")

    def effective():
        with sqlite3.connect(database) as db:
            rows={r[0]:r[1:] for r in db.execute("SELECT solar_id,confidence,reason,version,updated_at FROM annotations WHERE source_hash=?",(source_hash,))}
        result=[]
        for source in records:
            row={**source,"annotation_version":0}
            if source["solar_id"] in rows:
                level,reason,version,updated=rows[source["solar_id"]]
                row.update(confidence=level,reason=reason,annotation_version=version,annotation_updated_at=updated,
                           automatic_confidence=source["confidence"],automatic_reason=source["reason"])
            result.append(row)
        return result

    @app.get("/api/results")
    def results():
        current_source()
        return JSONResponse(effective(),headers={"Cache-Control":"no-store"})

    @app.put("/api/annotations/{solar_id}")
    def annotate(solar_id:str,body:Annotation):
        current_source()
        if solar_id not in by_id:
            raise HTTPException(404,"Unknown solar ID.")
        if body.confidence not in {"medium","low","very_low","unlocated"}:
            raise HTTPException(422,"Unsupported confidence level; identity verification requires independent evidence in the review application.")
        if not body.reason.strip() or len(body.reason.strip()) < 10:
            raise HTTPException(422,"A meaningful reason is required.")
        now=datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(database) as db:
            db.execute("BEGIN IMMEDIATE")
            current=db.execute("SELECT version FROM annotations WHERE solar_id=? AND source_hash=?",(solar_id,source_hash)).fetchone()
            version=current[0] if current else 0
            if body.version != version:
                raise HTTPException(409,"Annotation changed. Reload before saving.")
            db.execute("INSERT OR REPLACE INTO annotations VALUES(?,?,?,?,?,?)",(solar_id,body.confidence,body.reason.strip(),version+1,now,source_hash))
            db.execute("INSERT INTO annotation_events(solar_id,payload,created_at) VALUES(?,?,?)",(solar_id,json.dumps({"source_hash":source_hash,"before_version":version,"after":body.model_dump()}),now))
        return {"version":version+1,"updated_at":now}

    @app.get("/api/export")
    def export():
        current_source()
        with lock:
            context=read(directory/"context.json")
            with sqlite3.connect(database) as db:
                events=db.execute("SELECT id,solar_id,payload,created_at FROM annotation_events ORDER BY id").fetchall()
            context["metadata"]={**context["metadata"],"annotation_events":[{"id":r[0],"solar_id":r[1],"payload":json.loads(r[2]),"created_at":r[3]} for r in events]}
            path=database.parent/"roof-pv-results-edited.gpkg"
            export_gpkg(path,effective(),context)
        return FileResponse(path,filename="roof-pv-results-edited.gpkg",media_type="application/geopackage+sqlite3")

    app.mount("/",StaticFiles(directory=directory,html=True),name="results")
    return app


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port",type=int,default=8790)
    parser.add_argument("--directory",type=Path,default=APP/"dist")
    parser.add_argument("--database",type=Path,default=APP/"state"/"annotations.sqlite3")
    args=parser.parse_args()
    uvicorn.run(create_app(args.directory,args.database),host="127.0.0.1",port=args.port)
