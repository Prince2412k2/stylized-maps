from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Callable


ROOT = pathlib.Path(__file__).resolve().parents[1]
GDAL_IMAGE = "ghcr.io/osgeo/gdal@sha256:2f735873e76eaab9d422e5c72562399fe8ea5d5dc33c6e860ec38d1ceaaf64a5"
PMTILES_IMAGE = "protomaps/go-pmtiles@sha256:06574f01f55a78f78f887bc7ebf729a5c093c0d6e17d9876300cfcb0758b59d3"
OSMIUM_IMAGE = "stefda/osmium-tool@sha256:d2321d0e926f77ead7547b4b35f5cf98d9fd74043673cecc4fc2bb7cce06ff63"
STAGES = (
    ("preflight", 1), ("acquire", 8), ("mosaics", 2), ("vector", 14),
    ("rdr-raster", 15), ("elden-raster", 55), ("package", 4), ("cleanup", 1),
)


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def atomic_json(path: pathlib.Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".next")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def settings() -> tuple[pathlib.Path, pathlib.Path]:
    asset_value = os.environ.get("MAP_ASSET_ROOT")
    boundary_value = os.environ.get("MAP_INDIA_BOUNDARY")
    if not asset_value or not boundary_value:
        raise SystemExit("MAP_ASSET_ROOT and MAP_INDIA_BOUNDARY are required")
    asset_root = pathlib.Path(asset_value).expanduser().resolve()
    boundary = pathlib.Path(boundary_value).expanduser().resolve()
    if not boundary.is_file():
        raise SystemExit(f"Official India boundary does not exist: {boundary}")
    if boundary.suffix.lower() not in {".geojson", ".json", ".gpkg"}:
        raise SystemExit("MAP_INDIA_BOUNDARY must be a self-contained GeoJSON or GeoPackage file")
    if asset_root == ROOT or ROOT in asset_root.parents:
        raise SystemExit("MAP_ASSET_ROOT must be an external build volume, not a project directory")
    return asset_root, boundary


def fingerprint(boundary: pathlib.Path) -> str:
    paths = [
        ROOT / "config/regions/india.json", ROOT / "config/sources.lock.json",
        ROOT / "pipeline/acquire_india.py", ROOT / "pipeline/renderers/render_tracer.py",
        ROOT / "pipeline/renderers/render_india.py", ROOT / "pipeline/build_assets.py",
        ROOT / "scripts/build-vector",
    ] + sorted((ROOT / "planetiler").glob("*.java"))
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(path.read_bytes())
    digest.update(sha256(boundary).encode())
    return digest.hexdigest()[:12]


@dataclass
class Pipeline:
    asset_root: pathlib.Path
    boundary_input: pathlib.Path
    build_id: str

    @classmethod
    def create(cls) -> "Pipeline":
        asset_root, boundary = settings()
        build_id = os.environ.get("MAP_BUILD_ID", f"india-{fingerprint(boundary)}")
        if not build_id.startswith("india-") or "/" in build_id:
            raise SystemExit("MAP_BUILD_ID must use the form india-<id>")
        return cls(asset_root, boundary, build_id)

    @property
    def runtime(self) -> pathlib.Path:
        return self.asset_root / "runtime"

    @property
    def work(self) -> pathlib.Path:
        return self.asset_root / "work" / self.build_id

    @property
    def output(self) -> pathlib.Path:
        return self.asset_root / "output" / self.build_id

    @property
    def boundary(self) -> pathlib.Path:
        return self.work / "boundary/india.gpkg"

    @property
    def boundary_geojson(self) -> pathlib.Path:
        return self.work / "boundary/india.geojson"

    @property
    def status(self) -> pathlib.Path:
        return self.runtime / "status.json"

    def event(self, event: str, **fields: object) -> None:
        self.runtime.mkdir(parents=True, exist_ok=True)
        record = {"ts": now(), "event": event, "buildId": self.build_id, **fields}
        with (self.runtime / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, separators=(",", ":")) + "\n")

    def update(self, **fields: object) -> None:
        current = json.loads(self.status.read_text(encoding="utf-8")) if self.status.exists() else {}
        current.update(fields, schemaVersion=1, buildId=self.build_id, pid=os.getpid(), updatedAt=now())
        atomic_json(self.status, current)

    def progress(self, stage: str, completed: int = 0, total: int = 1) -> None:
        previous = 0
        weight = 0
        for name, value in STAGES:
            if name == stage:
                weight = value
                break
            previous += value
        percent = previous + weight * completed / max(total, 1)
        self.update(state="running", stage=stage, progress=round(percent, 2), stageCompleted=completed, stageTotal=total)

    def command(self, stage: str, command: list[str], env: dict[str, str] | None = None, track: bool = False) -> None:
        self.event("stage-command", stage=stage, command=command)
        process = subprocess.Popen(command, cwd=ROOT, env={**os.environ, **(env or {})}, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        assert process.stdout is not None
        for line in process.stdout:
            print(line, end="", flush=True)
            if track and line.startswith("PROGRESS "):
                _, completed, total = line.split()
                self.progress(stage, int(completed), int(total))
        if process.wait():
            raise subprocess.CalledProcessError(process.returncode, command)

    def stage(self, name: str, outputs: list[pathlib.Path], action: Callable[[], None]) -> None:
        marker = self.work / "stages" / f"{name}.json"
        if outputs and marker.exists() and all(path.exists() for path in outputs):
            self.progress(name, 1, 1)
            self.event("stage-resumed", stage=name)
            return
        self.progress(name)
        self.event("stage-started", stage=name)
        started = time.monotonic()
        action()
        missing = [str(path) for path in outputs if not path.exists()]
        if missing:
            raise RuntimeError(f"stage {name} did not create: {', '.join(missing)}")
        atomic_json(marker, {"stage": name, "completedAt": now(), "elapsedSeconds": round(time.monotonic() - started, 2)})
        self.progress(name, 1, 1)
        self.event("stage-completed", stage=name, elapsedSeconds=round(time.monotonic() - started, 2))

    def docker(self, image: str, arguments: list[str]) -> list[str]:
        return [
            "docker", "run", "--rm", "--cpus", "4", "--memory", "16g", "--memory-swap", "18g",
            "--user", f"{os.getuid()}:{os.getgid()}",
            "--volume", f"{ROOT}:/workspace", "--volume", f"{self.asset_root}:/assets",
            "--workdir", "/workspace", image, *arguments,
        ]

    def execute(self) -> None:
        self.runtime.mkdir(parents=True, exist_ok=True)
        lock = (self.runtime / "pipeline.lock").open("w")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as cause:
            raise RuntimeError("another India asset pipeline is already running") from cause
        try:
            completed = self.output / "cleanup.json"
            if completed.exists():
                self.verify_web(self.output / "web")
                self.update(state="completed", stage="complete", progress=100, webRoot=str(self.output / "web"))
                self.event("pipeline-reused", reason="verified-complete-release")
                return
            packaged = self.output / "web/maps/current.json"
            if packaged.exists():
                self.verify_web(self.output / "web")
                self.progress("cleanup")
                self.cleanup(completed)
                self.progress("cleanup", 1, 1)
                self.update(state="completed", stage="complete", progress=100, completedAt=now(), webRoot=str(self.output / "web"))
                self.event("pipeline-recovered", reason="verified-package-found")
                return
            self.work.mkdir(parents=True, exist_ok=True)
            (self.work / "stages").mkdir(exist_ok=True)
            shutil.rmtree(self.work / "scratch/contours", ignore_errors=True)
            atomic_json(self.status, {
                "schemaVersion": 1, "buildId": self.build_id, "pid": os.getpid(), "state": "starting",
                "stage": "preflight", "progress": 0, "startedAt": now(), "updatedAt": now(),
            })
            self.event("pipeline-started")
            self.stage("preflight", [self.boundary, self.boundary_geojson], self.preflight)
            inventory = self.asset_root / "sources/inventory.json"
            self.stage("acquire", [inventory], self.acquire)
            clipped_osm = self.work / "osm/india.osm.pbf"
            self.stage("vector", [clipped_osm], lambda: self.clip_osm(clipped_osm))
            dem_vrt = self.work / "mosaics/dem.vrt"
            landcover_vrt = self.work / "mosaics/landcover.vrt"
            self.stage("mosaics", [dem_vrt, landcover_vrt], self.mosaics)
            vector = self.work / "core/vector.pmtiles"
            self.stage("vector", [vector], lambda: self.build_vector(clipped_osm, vector))
            rdr = self.work / "rdr/terrain.mbtiles"
            self.stage("rdr-raster", [rdr], lambda: self.build_raster("rdr", rdr, 12, dem_vrt, landcover_vrt))
            elden = self.work / "elden/base.mbtiles"
            self.stage("elden-raster", [elden], lambda: self.build_raster("elden", elden, 13, dem_vrt, landcover_vrt))
            catalog = self.output / "web/maps/current.json"
            self.stage("package", [catalog], lambda: self.package(vector, rdr, elden))
            cleanup_marker = self.output / "cleanup.json"
            self.stage("cleanup", [cleanup_marker], lambda: self.cleanup(cleanup_marker))
            self.update(state="completed", stage="complete", progress=100, completedAt=now(), webRoot=str(self.output / "web"))
            self.event("pipeline-completed", webRoot=str(self.output / "web"))
            (self.runtime / "last-error.json").unlink(missing_ok=True)
        except Exception as cause:
            failure = {"ts": now(), "buildId": self.build_id, "stage": self.current_stage(), "error": type(cause).__name__, "message": str(cause)}
            atomic_json(self.runtime / "last-error.json", failure)
            self.update(state="failed", failedAt=now(), message=str(cause))
            self.event("pipeline-failed", **failure)
            raise
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
            lock.close()

    def current_stage(self) -> str:
        if not self.status.exists():
            return "startup"
        return str(json.loads(self.status.read_text(encoding="utf-8")).get("stage", "unknown"))

    def preflight(self) -> None:
        self.asset_root.mkdir(parents=True, exist_ok=True)
        required = int(float(os.environ.get("MAP_ASSET_MIN_FREE_GB", "80")) * 1024**3)
        available = shutil.disk_usage(self.asset_root).free
        if available < required:
            raise RuntimeError(f"insufficient external-volume disk: {available / 1024**3:.1f} GiB available, {required / 1024**3:.1f} GiB required")
        subprocess.run(["docker", "info"], stdout=subprocess.DEVNULL, check=True)
        self.boundary.parent.mkdir(parents=True, exist_ok=True)
        source = self.boundary.parent / f"source{self.boundary_input.suffix}"
        shutil.copy2(self.boundary_input, source)
        source_asset = f"/assets/{source.relative_to(self.asset_root)}"
        self.command("preflight", self.docker(GDAL_IMAGE, ["ogr2ogr", "-f", "GPKG", f"/assets/{self.boundary.relative_to(self.asset_root)}", source_asset, "-t_srs", "EPSG:4326", "-nln", "india", "-makevalid"]))
        self.command("preflight", self.docker(GDAL_IMAGE, ["ogr2ogr", "-f", "GeoJSON", f"/assets/{self.boundary_geojson.relative_to(self.asset_root)}", f"/assets/{self.boundary.relative_to(self.asset_root)}", "-lco", "RFC7946=YES"]))
        boundary_geojson = json.loads(self.boundary_geojson.read_text(encoding="utf-8"))
        features = boundary_geojson.get("features", [])
        if len(features) != 1:
            raise RuntimeError(f"official boundary must contain exactly one feature, received {len(features)}")
        self.boundary_geojson.write_text(json.dumps(features[0], separators=(",", ":")) + "\n", encoding="utf-8")
        self.event("preflight-passed", freeBytes=available, boundarySha256=sha256(self.boundary))

    def bounds(self) -> list[float]:
        command = self.docker(GDAL_IMAGE, ["ogrinfo", "-so", "-al", "-json", f"/assets/work/{self.build_id}/boundary/india.gpkg"])
        report = json.loads(subprocess.check_output(command, cwd=ROOT))
        extent = report["layers"][0]["geometryFields"][0]["extent"]
        return [extent[0], extent[1], extent[2], extent[3]]

    def acquire(self) -> None:
        self.command("acquire", [
            sys.executable, "pipeline/acquire_india.py", "--root", str(self.asset_root),
            "--lock", str(ROOT / "config/sources.lock.json"), "--bounds", *map(str, self.bounds()),
            "--boundary", str(self.boundary_geojson), "--min-free-gib", "5",
        ], track=True)

    def mosaics(self) -> None:
        target = f"/assets/work/{self.build_id}/mosaics"
        shell = f"mkdir -p {target}; gdalbuildvrt {target}/dem.vrt /assets/sources/terrain/*.tif; gdalbuildvrt {target}/landcover.vrt /assets/sources/landcover/*.tif"
        self.command("mosaics", self.docker(GDAL_IMAGE, ["sh", "-ceu", shell]))

    def clip_osm(self, output: pathlib.Path) -> None:
        output.parent.mkdir(parents=True, exist_ok=True)
        source = self.asset_root / "sources/osm/india-260812.osm.pbf"
        command = self.docker(OSMIUM_IMAGE, [
            "osmium", "extract", "--strategy=complete_ways", "--polygon", f"/assets/{self.boundary_geojson.relative_to(self.asset_root)}",
            "--overwrite", "--progress", "--output", f"/assets/{output.relative_to(self.asset_root)}", f"/assets/{source.relative_to(self.asset_root)}",
        ])
        self.command("vector", command)

    def build_vector(self, source: pathlib.Path, output: pathlib.Path) -> None:
        output.parent.mkdir(parents=True, exist_ok=True)
        self.command("vector", ["scripts/build-vector"], {
            "MAP_ASSET_ROOT": str(self.asset_root),
            "MAP_OSM_SOURCE": str(source),
            "MAP_PLANETILER_JAR": str(self.asset_root / "planetiler/planetiler.jar"),
            "MAP_VECTOR_OUTPUT": str(output),
            "JAVA_TOOL_OPTIONS": os.environ.get("JAVA_TOOL_OPTIONS", "-Xmx12g"),
        })

    def build_raster(self, renderer: str, output: pathlib.Path, zoom: int, dem: pathlib.Path, landcover: pathlib.Path) -> None:
        output.parent.mkdir(parents=True, exist_ok=True)
        arguments = [
            "python3", "pipeline/renderers/render_india.py", "--renderer", renderer,
            "--output", f"/assets/{output.relative_to(self.asset_root)}",
            "--work", f"/assets/work/{self.build_id}/scratch/{renderer}",
            "--bounds", *map(str, self.bounds()), "--zoom", str(zoom), "--metatile", "4",
            "--dem", f"/assets/{dem.relative_to(self.asset_root)}",
            "--landcover", f"/assets/{landcover.relative_to(self.asset_root)}",
            "--boundary", f"/assets/{self.boundary.relative_to(self.asset_root)}",
            "--min-free-gib", "5",
        ]
        self.command(f"{renderer}-raster", self.docker(GDAL_IMAGE, arguments), track=True)
        overviews = ["2", "4", "8", "16", "32"] if renderer == "elden" else ["2", "4", "8", "16"]
        self.command(f"{renderer}-raster", self.docker(GDAL_IMAGE, ["gdaladdo", "-r", "average", f"/assets/{output.relative_to(self.asset_root)}", *overviews]))

    def convert(self, source: pathlib.Path, output: pathlib.Path) -> None:
        output.parent.mkdir(parents=True, exist_ok=True)
        self.command("package", self.docker(PMTILES_IMAGE, ["convert", f"/assets/{source.relative_to(self.asset_root)}", f"/assets/{output.relative_to(self.asset_root)}"]))

    def package(self, vector: pathlib.Path, rdr_mbtiles: pathlib.Path, elden_mbtiles: pathlib.Path) -> None:
        release = self.output / "web/maps/releases" / self.build_id
        stage = self.output / f".{self.build_id}.staging"
        shutil.rmtree(stage, ignore_errors=True)
        for directory in ("core", "rdr", "elden"):
            (stage / directory).mkdir(parents=True, exist_ok=True)
        shutil.move(vector, stage / "core/vector.pmtiles")
        rdr = stage / "rdr/terrain.pmtiles"
        elden = stage / "elden/base.pmtiles"
        self.convert(rdr_mbtiles, rdr)
        self.convert(elden_mbtiles, elden)
        rdr_mbtiles.unlink()
        elden_mbtiles.unlink()
        products = {}
        for path in sorted(stage.rglob("*.pmtiles")):
            products[path.relative_to(stage).as_posix()] = {"sha256": sha256(path), "bytes": path.stat().st_size}
        bounds = self.bounds()
        atomic_json(stage / "artifact-manifest.json", {
            "schemaVersion": 1, "releaseId": self.build_id, "normalizedSchema": "normalized-vector-v1",
            "bounds": bounds, "boundarySha256": sha256(self.boundary), "products": products,
            "rendererStatus": {name: "ready" for name in ("sanandreas", "nfs", "rdr", "elden")},
            "nativeZoom": {"rdr": 12, "elden": 13},
        })
        release.parent.mkdir(parents=True, exist_ok=True)
        if not release.exists():
            stage.replace(release)
        else:
            shutil.rmtree(stage)
        base = f"/maps/releases/{self.build_id}"
        atomic_json(self.output / "web/maps/current.json", {
            "schemaVersion": 1, "releaseId": self.build_id,
            "region": {"id": "india", "label": "India", "bounds": bounds, "center": [82.0, 22.5], "zoom": {"initial": 4.5, "min": 4, "max": 18}},
            "products": {"coreVector": f"{base}/core/vector.pmtiles", "rdrTerrain": f"{base}/rdr/terrain.pmtiles", "eldenBase": f"{base}/elden/base.pmtiles"},
            "renderers": {
                "sanandreas": {"label": "Grand Theft Auto: San Andreas", "status": "ready", "model": "vector-first"},
                "nfs": {"label": "Need for Speed: Payback", "status": "ready", "model": "vector-first"},
                "rdr": {"label": "Red Dead Redemption 2", "status": "ready", "model": "vector-plus-terrain"},
                "elden": {"label": "Elden Ring", "status": "ready", "model": "raster-hybrid"},
            },
        })
        shutil.copytree(self.asset_root / "sources/glyphs", self.output / "web/fonts", dirs_exist_ok=True)
        checksums = []
        for path in sorted((self.output / "web").rglob("*")):
            if path.is_file():
                checksums.append(f"{sha256(path)}  {path.relative_to(self.output / 'web').as_posix()}")
        (self.output / "web/SHA256SUMS").write_text("\n".join(checksums) + "\n", encoding="utf-8")
        self.verify_web(self.output / "web")

    def verify_web(self, web: pathlib.Path) -> None:
        for line in (web / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
            expected, relative = line.split("  ", 1)
            path = web / relative
            if not path.is_file() or sha256(path) != expected:
                raise RuntimeError(f"final asset verification failed: {relative}")

    def cleanup(self, marker: pathlib.Path) -> None:
        self.verify_web(self.output / "web")
        shutil.rmtree(self.work / "scratch", ignore_errors=True)
        shutil.rmtree(self.work / "mosaics", ignore_errors=True)
        shutil.rmtree(self.work / "core", ignore_errors=True)
        shutil.rmtree(self.work / "rdr", ignore_errors=True)
        shutil.rmtree(self.work / "elden", ignore_errors=True)
        if os.environ.get("MAP_KEEP_SOURCES") != "1":
            shutil.rmtree(self.asset_root / "sources/terrain", ignore_errors=True)
            shutil.rmtree(self.asset_root / "sources/landcover", ignore_errors=True)
            shutil.rmtree(self.asset_root / "sources/osm", ignore_errors=True)
            (self.asset_root / "sources/inventory.json").unlink(missing_ok=True)
            (self.work / "stages/acquire.json").unlink(missing_ok=True)
        shutil.rmtree(self.work / "osm", ignore_errors=True)
        atomic_json(marker, {"completedAt": now(), "keptSources": os.environ.get("MAP_KEEP_SOURCES") == "1"})


def runtime_from_environment() -> pathlib.Path:
    asset_root, _ = settings()
    return asset_root / "runtime"


def print_status() -> None:
    path = runtime_from_environment() / "status.json"
    if not path.exists():
        print("No India asset pipeline has run.")
        return
    status = json.loads(path.read_text(encoding="utf-8"))
    if status.get("state") == "running":
        try:
            os.kill(int(status["pid"]), 0)
        except (ProcessLookupError, PermissionError):
            status.update(state="stale", message="process stopped; start again to resume")
    progress = float(status.get("progress", 0))
    filled = round(40 * progress / 100)
    print(f"[{'#' * filled}{'-' * (40 - filled)}] {progress:6.2f}%  {status['state']} / {status['stage']}")
    print(f"build: {status['buildId']}")
    print(f"stage: {status.get('stageCompleted', 0)}/{status.get('stageTotal', 1)}")
    if status.get("message"):
        print(f"error: {status['message']}")
    if status.get("webRoot"):
        print(f"web root: {status['webRoot']}")


def verify(path: pathlib.Path | None) -> None:
    if path is None:
        status = json.loads((runtime_from_environment() / "status.json").read_text(encoding="utf-8"))
        path = pathlib.Path(str(status["webRoot"]))
    checksum_file = path / "SHA256SUMS"
    if not checksum_file.is_file() or not (path / "maps/current.json").is_file():
        raise SystemExit("production web root is incomplete")
    for line in checksum_file.read_text(encoding="utf-8").splitlines():
        expected, relative = line.split("  ", 1)
        asset = path / relative
        if not asset.is_file() or sha256(asset) != expected:
            raise SystemExit(f"checksum mismatch: {relative}")
    print(f"Verified India production web root {path}")


def start() -> None:
    runtime = runtime_from_environment()
    runtime.mkdir(parents=True, exist_ok=True)
    lock = (runtime / "pipeline.lock").open("a+")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit("India asset pipeline is already running")
    fcntl.flock(lock, fcntl.LOCK_UN)
    lock.close()
    log = (runtime / "pipeline.log").open("a", encoding="utf-8")
    process = subprocess.Popen([sys.executable, __file__, "run"], cwd=ROOT, env=os.environ, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    log.close()
    print(f"Started India production build PID {process.pid}")
    print("Progress: scripts/map-assets status")
    print(f"Log: {runtime / 'pipeline.log'}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("run", "start", "status", "verify"))
    parser.add_argument("path", nargs="?", type=pathlib.Path)
    args = parser.parse_args()
    if args.command == "status":
        print_status()
    elif args.command == "start":
        start()
    elif args.command == "verify":
        verify(args.path)
    else:
        Pipeline.create().execute()


if __name__ == "__main__":
    main()
