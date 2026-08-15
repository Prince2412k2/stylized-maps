from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
import pathlib
import shutil
import signal
import subprocess
import sys
import time
import zipfile
from dataclasses import dataclass
from typing import Callable


ROOT = pathlib.Path(__file__).resolve().parents[1]
GDAL_IMAGE = "ghcr.io/osgeo/gdal@sha256:2f735873e76eaab9d422e5c72562399fe8ea5d5dc33c6e860ec38d1ceaaf64a5"
PMTILES_IMAGE = "protomaps/go-pmtiles@sha256:06574f01f55a78f78f887bc7ebf729a5c093c0d6e17d9876300cfcb0758b59d3"
OSMIUM_IMAGE = "stefda/osmium-tool@sha256:d2321d0e926f77ead7547b4b35f5cf98d9fd74043673cecc4fc2bb7cce06ff63"
DISPLAY_MIN_ZOOM = 4
BOUNDARY_URL = "https://www.surveyofindia.gov.in/documents/OUTLINE_OF_INDIA_SHP.zip"
BOUNDARY_MIRROR = "https://raw.githubusercontent.com/yashveeeeeeer/india-geodata/08ecb3fdf3122b1eb1b1bc149cd7bd6418223588/data/survey-of-india/outline-maps/india-outline-vector"
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


@dataclass(frozen=True)
class LocalResources:
    total_cpus: int
    reserved_cpus: int
    cpu_limit: int
    total_memory_gib: float
    reserved_memory_gib: float
    memory_limit_gib: int


def local_resources() -> LocalResources:
    total_cpus = len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else (os.cpu_count() or 1)
    values: dict[str, int] = {}
    for line in pathlib.Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
        name, value = line.split(":", 1)
        values[name] = int(value.split()[0]) * 1024
    total_memory = values["MemTotal"]
    available_memory = values["MemAvailable"]
    reserved_memory = int(total_memory * 0.2 + 0.999)
    usable_memory = min(int(total_memory * 0.8), available_memory - reserved_memory)
    memory_limit_gib = usable_memory // 1024**3
    if memory_limit_gib < 4:
        raise SystemExit("not enough available RAM to run the pipeline while reserving 20% for the system")
    reserved_cpus = min(5, max(1, total_cpus - 1))
    return LocalResources(
        total_cpus=total_cpus,
        reserved_cpus=reserved_cpus,
        cpu_limit=total_cpus - reserved_cpus,
        total_memory_gib=total_memory / 1024**3,
        reserved_memory_gib=reserved_memory / 1024**3,
        memory_limit_gib=memory_limit_gib,
    )


def configure_local_environment(detect_resources: bool = True) -> None:
    has_explicit_paths = bool(os.environ.get("MAP_ASSET_ROOT") and os.environ.get("MAP_INDIA_BOUNDARY"))
    if has_explicit_paths and os.environ.get("MAP_LOCAL_AUTO") != "1":
        return
    asset_root = pathlib.Path(os.environ.get("MAP_ASSET_ROOT", pathlib.Path.home() / "StylizedMapsAssets")).expanduser().resolve()
    os.environ.setdefault("MAP_ASSET_ROOT", str(asset_root))
    if not os.environ.get("MAP_INDIA_BOUNDARY"):
        os.environ["MAP_INDIA_BOUNDARY"] = str(asset_root / "sources/boundary/india.gpkg")
    os.environ["MAP_LOCAL_AUTO"] = "1"
    if not detect_resources:
        return
    resources = local_resources()
    os.environ.update({
        "MAP_CPU_LIMIT": str(resources.cpu_limit),
        "MAP_MEMORY_LIMIT_GB": str(resources.memory_limit_gib),
        "MAP_MEMORY_SWAP_LIMIT_GB": str(resources.memory_limit_gib),
        "MAP_DOWNLOAD_WORKERS": str(min(12, resources.total_cpus)),
        "MAP_RASTER_WORKERS": str(resources.total_cpus),
    })


def prepare_local_boundary() -> None:
    boundary = pathlib.Path(os.environ["MAP_INDIA_BOUNDARY"])
    boundary_root = boundary.parent
    marker = boundary_root / "normalized.json"
    if boundary.exists() and marker.exists() and json.loads(marker.read_text(encoding="utf-8")).get("schemaVersion") == 2:
        return
    boundary_root.mkdir(parents=True, exist_ok=True)
    archive = boundary_root / "OUTLINE_OF_INDIA_SHP.zip"
    source_root = boundary_root / "source"
    if archive.exists() and not zipfile.is_zipfile(archive):
        archive.unlink()
    existing_shapes = list(source_root.rglob("*.shp")) if source_root.exists() else []
    source_complete = len(existing_shapes) == 1 and all(existing_shapes[0].with_suffix(f".{suffix}").exists() for suffix in ("dbf", "prj", "shx"))
    if not archive.exists() and not source_complete:
        partial = archive.with_suffix(".zip.part")
        command = ["curl", "--fail", "--location", "--output", str(partial), BOUNDARY_URL]
        try:
            subprocess.run(command, check=True)
        except subprocess.CalledProcessError:
            partial.unlink(missing_ok=True)
            try:
                subprocess.run(["curl", "--insecure", *command[1:]], check=True)
            except subprocess.CalledProcessError:
                partial.unlink(missing_ok=True)
                source_root.mkdir(parents=True, exist_ok=True)
                for suffix in ("cpg", "dbf", "prj", "shp", "shx"):
                    target = source_root / f"polymap15m_area.{suffix}"
                    partial_target = target.with_suffix(target.suffix + ".part")
                    subprocess.run([
                        "curl", "--fail", "--location", "--output", str(partial_target),
                        f"{BOUNDARY_MIRROR}/polymap15m_area.{suffix}",
                    ], check=True)
                    partial_target.replace(target)
                atomic_json(boundary_root / "source.json", {
                    "publisher": "Survey of India",
                    "origin": BOUNDARY_URL,
                    "mirror": BOUNDARY_MIRROR,
                })
            else:
                partial.replace(archive)
        else:
            partial.replace(archive)
    if archive.exists() and not source_complete:
        shutil.rmtree(source_root, ignore_errors=True)
        resolved_source_root = source_root.resolve()
        with zipfile.ZipFile(archive) as bundle:
            for member in bundle.infolist():
                target = (source_root / member.filename).resolve()
                if resolved_source_root not in target.parents and target != resolved_source_root:
                    raise RuntimeError(f"invalid path in Survey of India archive: {member.filename}")
            bundle.extractall(source_root)
    shapes = list(source_root.rglob("*.shp"))
    if len(shapes) != 1:
        raise RuntimeError("Survey of India boundary source did not contain one shapefile")
    relative_shape = shapes[0].relative_to(boundary_root)
    memory = os.environ["MAP_MEMORY_LIMIT_GB"]
    memory_swap = os.environ.get("MAP_MEMORY_SWAP_LIMIT_GB", memory)
    next_boundary = boundary.with_suffix(".next.gpkg")
    next_boundary.unlink(missing_ok=True)
    subprocess.run([
        "docker", "run", "--rm", "--cpus", os.environ["MAP_CPU_LIMIT"],
        "--memory", f"{memory}g", "--memory-swap", f"{memory_swap}g",
        "--user", f"{os.getuid()}:{os.getgid()}", "--volume", f"{boundary_root}:/boundary",
        GDAL_IMAGE, "ogr2ogr", "-f", "GPKG", f"/boundary/{next_boundary.name}",
        f"/boundary/{relative_shape.as_posix()}", "-dialect", "sqlite",
        "-sql", f'SELECT ST_CollectionExtract(ST_Union(geometry), 3) AS geometry FROM "{shapes[0].stem}"',
        "-t_srs", "EPSG:4326", "-nln", "india", "-nlt", "MULTIPOLYGON", "-makevalid",
    ], check=True)
    next_boundary.replace(boundary)
    atomic_json(marker, {"schemaVersion": 2, "geometryType": "MultiPolygon"})


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

    def resource_limits(self) -> tuple[str, int, int]:
        configured = self.runtime / "resources.json"
        if configured.exists():
            values = json.loads(configured.read_text(encoding="utf-8"))
            return str(values["cpuLimit"]), int(values["memoryLimitGiB"]), int(values["rasterWorkers"])
        return (
            os.environ.get("MAP_CPU_LIMIT", "4"),
            int(os.environ.get("MAP_MEMORY_LIMIT_GB", "16")),
            int(os.environ.get("MAP_RASTER_WORKERS", "1")),
        )

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
        cpus, memory, _ = self.resource_limits()
        has_live_limits = (self.runtime / "resources.json").exists()
        memory_swap = memory if has_live_limits or os.environ.get("MAP_LOCAL_AUTO") == "1" else int(os.environ.get("MAP_MEMORY_SWAP_LIMIT_GB", "18"))
        return [
            "docker", "run", "--rm", "--cpus", cpus, "--memory", f"{memory}g", "--memory-swap", f"{memory_swap}g",
            "--label", f"map.pipeline={self.build_id}",
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
            cpu_limit, memory_limit, raster_workers = self.resource_limits()
            atomic_json(self.status, {
                "schemaVersion": 1, "buildId": self.build_id, "pid": os.getpid(), "state": "starting",
                "stage": "preflight", "progress": 0, "startedAt": now(), "updatedAt": now(),
                "resources": {
                    "cpuLimit": float(cpu_limit),
                    "memoryLimitGiB": memory_limit,
                    "rasterWorkers": raster_workers,
                },
            })
            self.event("pipeline-started")
            if self.boundary_geojson.exists():
                existing_boundary = json.loads(self.boundary_geojson.read_text(encoding="utf-8"))
                if existing_boundary.get("geometry", {}).get("type") not in {"Polygon", "MultiPolygon"}:
                    (self.work / "stages/preflight.json").unlink(missing_ok=True)
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
        self.boundary.unlink(missing_ok=True)
        self.boundary_geojson.unlink(missing_ok=True)
        self.command("preflight", self.docker(GDAL_IMAGE, ["ogr2ogr", "-f", "GPKG", f"/assets/{self.boundary.relative_to(self.asset_root)}", source_asset, "-t_srs", "EPSG:4326", "-nln", "india", "-makevalid"]))
        self.command("preflight", self.docker(GDAL_IMAGE, ["ogr2ogr", "-f", "GeoJSON", f"/assets/{self.boundary_geojson.relative_to(self.asset_root)}", f"/assets/{self.boundary.relative_to(self.asset_root)}"]))
        boundary_geojson = json.loads(self.boundary_geojson.read_text(encoding="utf-8"))
        features = boundary_geojson.get("features", [])
        if len(features) != 1:
            raise RuntimeError(f"official boundary must contain exactly one feature, received {len(features)}")
        if features[0].get("geometry", {}).get("type") not in {"Polygon", "MultiPolygon"}:
            raise RuntimeError("official boundary must contain Polygon or MultiPolygon geometry")
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
            "--workers", os.environ.get("MAP_DOWNLOAD_WORKERS", "1"),
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
        cpu_limit, memory_limit, _ = self.resource_limits()
        has_live_limits = (self.runtime / "resources.json").exists()
        memory_swap = memory_limit if has_live_limits or os.environ.get("MAP_LOCAL_AUTO") == "1" else int(os.environ.get("MAP_MEMORY_SWAP_LIMIT_GB", "18"))
        self.command("vector", ["scripts/build-vector"], {
            "MAP_ASSET_ROOT": str(self.asset_root),
            "MAP_OSM_SOURCE": str(source),
            "MAP_PLANETILER_JAR": str(self.asset_root / "planetiler/planetiler.jar"),
            "MAP_VECTOR_OUTPUT": str(output),
            "MAP_BUILD_ID": self.build_id,
            "MAP_CPU_LIMIT": cpu_limit,
            "MAP_MEMORY_LIMIT_GB": str(memory_limit),
            "MAP_MEMORY_SWAP_LIMIT_GB": str(memory_swap),
            "JAVA_TOOL_OPTIONS": f"-Xmx{max(2, memory_limit - 4)}g -XX:ActiveProcessorCount={cpu_limit}",
        })

    def build_raster(self, renderer: str, output: pathlib.Path, zoom: int, dem: pathlib.Path, landcover: pathlib.Path) -> None:
        output.parent.mkdir(parents=True, exist_ok=True)
        cpu_limit, memory_limit, raster_workers = self.resource_limits()
        if renderer == "elden":
            raster_workers = min(raster_workers, max(1, memory_limit - 3))
        self.update(resources={
            "cpuLimit": float(cpu_limit),
            "memoryLimitGiB": memory_limit,
            "rasterWorkers": raster_workers,
        })
        arguments = [
            "python3", "pipeline/renderers/render_india.py", "--renderer", renderer,
            "--output", f"/assets/{output.relative_to(self.asset_root)}",
            "--work", f"/assets/work/{self.build_id}/scratch/{renderer}",
            "--bounds", *map(str, self.bounds()), "--zoom", str(zoom), "--metatile", "4",
            "--workers", str(raster_workers),
            "--dem", f"/assets/{dem.relative_to(self.asset_root)}",
            "--landcover", f"/assets/{landcover.relative_to(self.asset_root)}",
            "--boundary", f"/assets/{self.boundary.relative_to(self.asset_root)}",
            "--min-free-gib", "5",
        ]
        self.command(f"{renderer}-raster", self.docker(GDAL_IMAGE, arguments), track=True)
        overviews = [str(1 << level) for level in range(1, zoom - DISPLAY_MIN_ZOOM + 1)]
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
        shutil.copyfile(self.boundary_geojson, stage / "core/boundary.geojson")
        products = {}
        for path in sorted(path for path in stage.rglob("*") if path.is_file()):
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
            "region": {"id": "india", "label": "India", "bounds": bounds, "center": [82.0, 22.5], "zoom": {"initial": 4.5, "min": DISPLAY_MIN_ZOOM, "max": 18}},
            "products": {
                "coreVector": f"{base}/core/vector.pmtiles", "rdrTerrain": f"{base}/rdr/terrain.pmtiles",
                "eldenBase": f"{base}/elden/base.pmtiles", "boundary": f"{base}/core/boundary.geojson",
            },
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
    asset_root = pathlib.Path(os.environ.get("MAP_ASSET_ROOT", pathlib.Path.home() / "StylizedMapsAssets")).expanduser().resolve()
    return asset_root / "runtime"


def configured_resources() -> dict[str, int | float]:
    path = runtime_from_environment() / "resources.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {
        "cpuLimit": float(os.environ.get("MAP_CPU_LIMIT", "4")),
        "memoryLimitGiB": int(os.environ.get("MAP_MEMORY_LIMIT_GB", "16")),
        "rasterWorkers": int(os.environ.get("MAP_RASTER_WORKERS", "1")),
    }


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
    resources = status.get("resources") or configured_resources()
    if resources:
        print(f"resources: {resources['cpuLimit']:g} CPUs, {resources['memoryLimitGiB']} GiB, {resources['rasterWorkers']} raster workers")


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
    configure_local_environment()
    runtime = runtime_from_environment()
    runtime.mkdir(parents=True, exist_ok=True)
    lock = (runtime / "pipeline.lock").open("a+")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit("India asset pipeline is already running")
    fcntl.flock(lock, fcntl.LOCK_UN)
    lock.close()
    status_path = runtime / "status.json"
    if status_path.exists():
        state = json.loads(status_path.read_text(encoding="utf-8"))
        try:
            os.kill(int(state["pid"]), 0)
            raise SystemExit("India asset pipeline is already running or paused")
        except (ProcessLookupError, PermissionError):
            pass
    log = (runtime / "pipeline.log").open("a", encoding="utf-8")
    process = subprocess.Popen([sys.executable, __file__, "run"], cwd=ROOT, env=os.environ, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    log.close()
    resources = configured_resources()
    atomic_json(status_path, {
        "schemaVersion": 1, "buildId": "pending", "pid": process.pid, "state": "running",
        "stage": "bootstrap", "progress": 0, "stageCompleted": 0, "stageTotal": 1,
        "startedAt": now(), "updatedAt": now(),
        "resources": resources,
    })
    print(f"Started India production build PID {process.pid}")
    print("Progress: scripts/map-assets status")
    print("Pause: scripts/map-assets pause")
    print("Resources: scripts/map-assets resources")
    print(f"Log: {runtime / 'pipeline.log'}")


def update_runtime(**fields: object) -> dict[str, object]:
    path = runtime_from_environment() / "status.json"
    if not path.exists():
        raise SystemExit("No India asset pipeline has run")
    state = json.loads(path.read_text(encoding="utf-8"))
    state.update(fields, updatedAt=now())
    atomic_json(path, state)
    return state


def docker_containers(build_id: str) -> list[str]:
    if build_id == "pending":
        return []
    output = subprocess.check_output([
        "docker", "ps", "--filter", f"label=map.pipeline={build_id}", "--format", "{{.ID}}",
    ], text=True)
    return output.split()


def set_resources(mode: str | None, memory: int | None) -> None:
    current = configured_resources()
    if mode is None:
        print(f"{current['cpuLimit']:g} CPUs, {current['memoryLimitGiB']} GiB RAM")
        print("Set: scripts/map-assets resources auto|full|<cpus> <memory-gib>")
        return
    detected = local_resources()
    if mode == "auto":
        cpu_limit = detected.cpu_limit
        memory_limit = detected.memory_limit_gib
    elif mode == "full":
        cpu_limit = detected.total_cpus
        memory_limit = max(4, int(detected.total_memory_gib) - 2)
    else:
        try:
            cpu_limit = int(mode)
        except ValueError as cause:
            raise SystemExit("resource profile must be auto, full, or an integer CPU count") from cause
        if memory is None:
            raise SystemExit("manual resource limits require CPU count and memory in GiB")
        memory_limit = memory
    if not 1 <= cpu_limit <= detected.total_cpus:
        raise SystemExit(f"CPU limit must be between 1 and {detected.total_cpus}")
    max_memory = int(detected.total_memory_gib) - 1
    if not 4 <= memory_limit <= max_memory:
        raise SystemExit(f"memory limit must be between 4 and {max_memory} GiB")
    values: dict[str, int | float] = {
        "cpuLimit": cpu_limit,
        "memoryLimitGiB": memory_limit,
        "rasterWorkers": detected.total_cpus,
    }
    status_path = runtime_from_environment() / "status.json"
    containers: list[str] = []
    if status_path.exists():
        state = json.loads(status_path.read_text(encoding="utf-8"))
        containers = docker_containers(str(state.get("buildId", "pending")))
    if containers:
        subprocess.run([
            "docker", "update", "--cpus", str(cpu_limit), "--memory", f"{memory_limit}g",
            "--memory-swap", f"{memory_limit}g", *containers,
        ], check=True)
    atomic_json(runtime_from_environment() / "resources.json", values)
    if status_path.exists():
        update_runtime(resources=values)
    print(f"Pipeline resources set to {cpu_limit} CPUs and {memory_limit} GiB RAM.")
    if containers:
        print(f"Updated {len(containers)} running container(s) without restarting the build.")


def pause() -> None:
    state = update_runtime(state="paused", message="paused by user")
    pid = int(state["pid"])
    try:
        os.kill(pid, 0)
    except ProcessLookupError as cause:
        raise SystemExit("Pipeline process is not running; use resume to restart from checkpoints") from cause
    containers = docker_containers(str(state["buildId"]))
    if containers:
        subprocess.run(["docker", "pause", *containers], check=True)
    os.killpg(pid, signal.SIGSTOP)
    print("Paused India build. Run scripts/map-assets resume to continue.")


def resume() -> None:
    path = runtime_from_environment() / "status.json"
    if path.exists():
        state = json.loads(path.read_text(encoding="utf-8"))
        pid = int(state["pid"])
        try:
            os.kill(pid, 0)
            if state.get("state") != "paused":
                raise SystemExit("India asset pipeline is already running")
            containers = docker_containers(str(state["buildId"]))
            if containers:
                subprocess.run(["docker", "unpause", *containers], check=True)
            os.killpg(pid, signal.SIGCONT)
            update_runtime(state="running", message=None)
            print("Resumed India build.")
            return
        except ProcessLookupError:
            pass
        build_id = str(state.get("buildId", ""))
        if build_id.startswith("india-") and build_id != "india-pending":
            os.environ["MAP_BUILD_ID"] = build_id
    start()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("run", "start", "pause", "resume", "status", "resources", "verify"), nargs="?", default="status")
    parser.add_argument("argument", nargs="?")
    parser.add_argument("memory", nargs="?", type=int)
    args = parser.parse_args()
    configure_local_environment(detect_resources=args.command in {"run", "start"})
    if args.command == "status":
        print_status()
    elif args.command == "start":
        start()
    elif args.command == "pause":
        pause()
    elif args.command == "resume":
        resume()
    elif args.command == "resources":
        set_resources(args.argument, args.memory)
    elif args.command == "verify":
        verify(pathlib.Path(args.argument) if args.argument else None)
    else:
        if os.environ.get("MAP_LOCAL_AUTO") == "1":
            prepare_local_boundary()
        Pipeline.create().execute()


if __name__ == "__main__":
    main()
