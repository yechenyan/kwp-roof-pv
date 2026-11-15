"""Resumable, bounded-range official ZIP extraction selected by municipality code."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import codecs
import hashlib
import io
import json
import os
from pathlib import Path
import re
import struct
import time
import xml.etree.ElementTree as ET
import zipfile
import zlib

import requests

DEFAULT_AGS = ("12065036", "12065144")
PREFIXES = {"solar": ("EinheitenSolar_", "EinheitSolar", "EinheitMastrNummer"),
            "storage": ("EinheitenStromSpeicher_", "EinheitStromSpeicher", "EinheitMastrNummer"),
            "locations": ("Lokationen_", "Lokation", "MastrNummer"),
            "systems": ("AnlagenStromSpeicher_", "AnlageStromSpeicher", "MaStRNummer")}


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    os.replace(temporary, path)


class RemoteZip(io.RawIOBase):
    def __init__(self, url):
        if not re.fullmatch(r"https://download\.marktstammdatenregister\.de/Gesamtdatenexport_\d{8}_[\d.]+\.zip", url):
            raise ValueError("Use a dated official MaStR export URL.")
        self.url, self.position = url, 0
        self.session = requests.Session()
        response = self.session.get(url, headers={"Range": "bytes=0-0", "Accept-Encoding": "identity"}, timeout=90)
        response.raise_for_status()
        if response.status_code != 206:
            raise ValueError("The source must support bounded HTTP ranges.")
        self.size = int(response.headers["Content-Range"].split("/")[-1])
        self.etag = response.headers.get("ETag")

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self.position

    def seek(self, offset, whence=0):
        self.position = offset if whence == 0 else self.position + offset if whence == 1 else self.size + offset
        return self.position

    def read(self, size=-1):
        size = self.size - self.position if size < 0 else min(size, self.size - self.position)
        if size <= 0:
            return b""
        start, end = self.position, self.position + size - 1
        headers = {"Range": f"bytes={start}-{end}", "Accept-Encoding": "identity"}
        if self.etag:
            headers["If-Match"] = self.etag
        for attempt in range(4):
            try:
                response = self.session.get(self.url, headers=headers, timeout=180)
                response.raise_for_status()
                if response.status_code != 206 or response.headers.get("Content-Range") != f"bytes {start}-{end}/{self.size}" or len(response.content) != size:
                    raise ValueError("Unexpected range response or changed source archive.")
                self.position += size
                return response.content
            except (requests.RequestException, ValueError):
                if attempt == 3:
                    raise
                time.sleep(2 ** attempt)

    def close(self):
        if hasattr(self, "session"):
            self.session.close()
        super().close()


def extract_xml(compressed, info, record_tag, keep):
    """Keep selected rows while checking every decompressed byte and ZIP CRC."""
    decoder = zlib.decompressobj(-15)
    text_decoder = codecs.getincrementaldecoder("utf-16")()
    crc, size, total, pending = 0, 0, 0, ""
    rows = []
    opening, closing = "<" + record_tag + ">", "</" + record_tag + ">"
    select_fields = re.compile(r"<(Gemeindeschluessel|MastrNummer|MaStRNummer)>([^<]+)</")

    def feed(chunk):
        nonlocal crc, size, total, pending
        crc = zlib.crc32(chunk, crc)
        size += len(chunk)
        records = (pending + text_decoder.decode(chunk)).split(closing)
        pending = records.pop()
        for record in records:
            start = record.rfind(opening)
            if start < 0:
                raise ValueError("Unexpected XML record boundary.")
            fragment = record[start:] + closing
            total += 1
            if keep(dict(select_fields.findall(fragment))):
                element = ET.fromstring(fragment)
                row = {child.tag: child.text for child in element}
                row["_source_member"] = info.filename
                row["_source_xml"] = fragment
                rows.append(row)

    # Select on public AGS/IDs before parsing; nationwide records are not retained.
    for position in range(0, len(compressed), 64 * 1024):
        feed(decoder.decompress(compressed[position:position + 64 * 1024]))
    feed(decoder.flush())
    pending += text_decoder.decode(b"", final=True)
    if opening in pending or not decoder.eof or crc != info.CRC or size != info.file_size:
        raise ValueError("ZIP member size/CRC verification failed.")
    return rows, total


def acquire(home, url, ags=DEFAULT_AGS, workers=2):
    ags = sorted(set(ags))
    stamp = re.search(r"_(\d{8})_", url).group(1)
    snapshot_id = "mastr-" + stamp + "-" + hashlib.sha256(json.dumps(ags).encode()).hexdigest()[:8]
    folder = Path(home) / "registry" / snapshot_id
    if (folder / "manifest.json").exists():
        from .snapshot import RegistrySnapshot
        existing = RegistrySnapshot(folder)
        if existing.manifest["source"]["url"] != url or existing.manifest["source"]["ags"] != ags:
            raise ValueError("The completed registry snapshot has a different source scope.")
        return folder
    folder.mkdir(parents=True, exist_ok=True)
    lock = folder / ".acquire.lock"
    with lock.open("x") as stream:
        stream.write(str(os.getpid()))
    try:
        with RemoteZip(url) as remote, zipfile.ZipFile(remote) as archive:
            infos = archive.infolist()
            identity = {"url": url, "archive_bytes": remote.size, "etag": remote.etag, "ags": ags,
                        "members": [{"name": i.filename, "crc32": i.CRC, "bytes": i.file_size, "compressed_bytes": i.compress_size} for i in infos]}
        identity_file = folder / "source.json"
        if identity_file.exists() and json.loads(identity_file.read_text()) != identity:
            raise ValueError("The frozen source archive identity changed.")
        atomic_json(identity_file, identity)
        expected = [i for i in infos if i.filename == "Katalogwerte.xml" or any(i.filename.startswith(p[0]) for p in PREFIXES.values())]
        progress = {"kind": "registry_acquisition", "name": "MaStR registry acquisition", "snapshot_id": snapshot_id,
                    "status": "running", "completed": 0, "total": len(expected)}
        job_file = Path(home) / "jobs" / "registry.json"

        def member(info, tag, selector, selection):
            path = folder / "members" / (info.filename + ".json")
            if path.exists():
                cached = json.loads(path.read_text())
                if cached["crc32"] == info.CRC and cached["selection"] == selection:
                    return cached
            packed_path = folder / "compressed" / (info.filename + ".deflate")
            packed_path.parent.mkdir(parents=True, exist_ok=True)
            was_cached = packed_path.exists() and packed_path.stat().st_size == info.compress_size
            if was_cached:
                compressed = packed_path.read_bytes()
            else:
                with RemoteZip(url) as remote:
                    if remote.size != identity["archive_bytes"] or remote.etag != identity["etag"]:
                        raise ValueError("Source identity changed during extraction.")
                    remote.seek(info.header_offset)
                    header = remote.read(30)
                    name_size, extra_size = struct.unpack_from("<HH", header, 26)
                    remote.seek(info.header_offset + 30 + name_size + extra_size)
                    compressed = remote.read(info.compress_size)
                temporary = packed_path.with_suffix(".tmp")
                temporary.write_bytes(compressed)
                os.replace(temporary, packed_path)
            try:
                rows, total = extract_xml(compressed, info, tag, selector)
            except ValueError:
                if not was_cached:
                    raise
                packed_path.unlink()
                return member(info, tag, selector, selection)
            result = {"member": info.filename, "crc32": info.CRC, "selection": selection, "nationwide_records": total,
                      "compressed_sha256": hashlib.sha256(compressed).hexdigest(), "rows": rows}
            atomic_json(path, result)
            return result

        outputs = {}
        for phase in [("solar", "storage"), ("locations", "systems"), ("catalog",)]:
            if phase == ("locations", "systems"):
                local = outputs["solar"] + outputs["storage"]
                location_ids = {r.get("LokationMaStRNummer") for r in local} - {None}
                system_ids = {r.get("SpeMastrNummer") for r in outputs["storage"]} - {None}
            tasks = []
            for name in phase:
                prefix, tag, id_field = PREFIXES[name] if name != "catalog" else ("Katalogwerte.xml", "Katalogwert", "Id")
                if name in ("solar", "storage"):
                    selector = lambda row: row.get("Gemeindeschluessel") in ags
                    selection = {"ags": ags}
                elif name == "catalog":
                    selector, selection = lambda row: True, {"all": True}
                else:
                    ids = location_ids if name == "locations" else system_ids
                    selector = lambda row, ids=ids, field=id_field: row.get(field) in ids
                    selection = {"ids_sha256": hashlib.sha256(json.dumps(sorted(ids)).encode()).hexdigest()}
                tasks.extend((name, i, tag, selector, selection) for i in infos if i.filename.startswith(prefix))
            for name in phase:
                outputs[name] = []
            with ThreadPoolExecutor(max_workers=workers) as pool:
                for name, result in pool.map(lambda task: (task[0], member(*task[1:])), tasks):
                    outputs[name].extend(result["rows"])
                    progress["completed"] += 1
                    progress["updated_at"] = datetime.now(timezone.utc).isoformat()
                    atomic_json(job_file, progress)
                    print(f"{result['member']}: {len(result['rows'])} retained; {progress['completed']}/{progress['total']}", flush=True)
        if progress["completed"] != progress["total"]:
            raise ValueError("Registry extraction is incomplete.")
        for name, rows in outputs.items():
            atomic_json(folder / (name + ".json"), rows)
        progress["status"] = "completed"
        atomic_json(job_file, progress)
        atomic_json(folder / "extraction.json", {**progress, "selection": "AGS for units; referenced IDs for locations/systems", "source_sha256": digest(identity_file),
                                               "member_artifacts": {p.name: digest(p) for p in sorted((folder / "members").glob("*.json"))}})
        return folder
    except Exception as exc:
        atomic_json(Path(home) / "jobs" / "registry.json", {"kind": "registry_acquisition", "name": "MaStR registry acquisition", "status": "failed", "error": str(exc), "completed": 0, "total": 0})
        raise
    finally:
        lock.unlink(missing_ok=True)
