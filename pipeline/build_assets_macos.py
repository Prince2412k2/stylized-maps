from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import shutil
import signal
import subprocess
import sys
import urllib.request
import zipfile

from build_assets import Pipeline, atomic_json, now, sha256


ROOT = pathlib.Path(__file__).resolve().parents[1]
ASSET_ROOT = pathlib.Path.home() / "StylizedMapsAssets"
BOUNDARY_URL = "https://www.surveyofindia.gov.in/documents/OUTLINE_OF_INDIA_SHP.zip"
PMTILES_URL = "https://github.com/protomaps/go-pmtiles/releases/download/v1.31.2/go-pmtiles-1.31.2_Darwin_arm64.zip"
PMTILES_SHA256 = "40528f7f616fcbf91207cd48c8fc023d213f6d86c0cbf1f748732803d1880f3d"


def run(command: list[str], **kwargs) -> None:
    subprocess.run(command, check=True, **kwargs)


def brew_path() -> pathlib.Path:
    existing = shutil.which("brew")
    if existing:
        return pathlib.Path(existing)
    installer = '/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
    run(["/bin/bash", "-c", installer], env={**os.environ, "NONINTERACTIVE": "1"})
    for candidate in (pathlib.Path("/opt/homebrew/bin/brew"), pathlib.Path("/usr/local/bin/brew")):
        if candidate.exists():
            return candidate
    raise RuntimeError("Homebrew installation completed but brew was not found")


def install_dependencies() -> dict[str, str]:
    brew = brew_path()
    run([str(brew), "install", "gdal", "osmium-tool", "openjdk@21", "python@3.12"])
    prefix = subprocess.check_output([str(brew), "--prefix"], text=True).strip()
    java_prefix = subprocess.check_output([str(brew), "--prefix", "openjdk@21"], text=True).strip()
    python_prefix = subprocess.check_output([str(brew), "--prefix", "python@3.12"], text=True).strip()
    environment = {
        **os.environ,
        "PATH": f"{java_prefix}/bin:{python_prefix}/bin:{prefix}/bin:{os.environ.get('PATH', '')}",
        "GDAL_NUM_THREADS": "1",
        "VECLIB_MAXIMUM_THREADS": "1",
        "OPENBLAS_NUM_THREADS": "1",
    }
    tools = ASSET_ROOT / "tools"
    tools.mkdir(parents=True, exist_ok=True)
    pmtiles = tools / "pmtiles"
    if not pmtiles.exists():
        archive = tools / "pmtiles.zip"
        urllib.request.urlretrieve(PMTILES_URL, archive)
        if sha256(archive) != PMTILES_SHA256:
            archive.unlink()
            raise RuntimeError("PMTiles CLI checksum mismatch")
        with zipfile.ZipFile(archive) as bundle:
            member = next(name for name in bundle.namelist() if pathlib.Path(name).name == "pmtiles")
            with bundle.open(member) as source, pmtiles.open("wb") as output:
                shutil.copyfileobj(source, output)
        pmtiles.chmod(0o755)
        archive.unlink()

    venv = tools / "python"
    python = venv / "bin/python"
    if not python.exists():
        run([f"{python_prefix}/bin/python3.12", "-m", "venv", str(venv)], env=environment)
    gdal_version = subprocess.check_output([f"{prefix}/bin/gdal-config", "--version"], text=True, env=environment).strip()
    marker = venv / f".gdal-{gdal_version}"
    if not marker.exists():
        run([str(python), "-m", "pip", "install", "--upgrade", "pip", "wheel", "setuptools"], env=environment)
        run([str(python), "-m", "pip", "install", "numpy<2", f"GDAL=={gdal_version}"], env=environment)
        marker.touch()
    environment["MAP_NATIVE_PYTHON"] = str(python)
    environment["MAP_NATIVE_PMTILES"] = str(pmtiles)
    return environment


def download_boundary() -> pathlib.Path:
    boundary_root = ASSET_ROOT / "sources/boundary"
    boundary_root.mkdir(parents=True, exist_ok=True)
    project_shape = ROOT / "data/OUTLINE_OF_INDIA.shp"
    if project_shape.exists():
        for source in project_shape.parent.glob("OUTLINE_OF_INDIA.*"):
            shutil.copy2(source, boundary_root / source.name)
        return boundary_root / project_shape.name
    shape = boundary_root / "OUTLINE_OF_INDIA.shp"
    if shape.exists():
        return shape
    archive = boundary_root / "OUTLINE_OF_INDIA_SHP.zip"
    normal = ["curl", "--fail", "--location", "--output", str(archive), BOUNDARY_URL]
    try:
        run(normal)
    except subprocess.CalledProcessError:
        run(["curl", "--insecure", "--fail", "--location", "--output", str(archive), BOUNDARY_URL])
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(boundary_root)
    matches = list(boundary_root.rglob("OUTLINE_OF_INDIA.shp"))
    if len(matches) != 1:
        raise RuntimeError("Survey of India archive did not contain one OUTLINE_OF_INDIA.shp")
    if matches[0] != shape:
        for source in matches[0].parent.glob("OUTLINE_OF_INDIA.*"):
            shutil.move(source, boundary_root / source.name)
    return shape


def native_fingerprint(boundary: pathlib.Path) -> str:
    digest = hashlib.sha256()
    paths = [
        ROOT / "config/regions/india.json", ROOT / "config/sources.lock.json",
        ROOT / "pipeline/acquire_india.py", ROOT / "pipeline/renderers/render_tracer.py",
        ROOT / "pipeline/renderers/render_india.py", ROOT / "pipeline/build_assets.py",
        ROOT / "pipeline/build_assets_macos.py",
    ] + sorted((ROOT / "planetiler").glob("*.java"))
    for path in paths:
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(path.read_bytes())
    for path in sorted(boundary.parent.glob("OUTLINE_OF_INDIA.*")):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


class MacPipeline(Pipeline):
    environment: dict[str, str]

    @classmethod
    def create(cls, environment: dict[str, str], boundary: pathlib.Path) -> "MacPipeline":
        pipeline = cls(ASSET_ROOT, boundary, f"india-mac-{native_fingerprint(boundary)}")
        pipeline.environment = environment
        return pipeline

    @property
    def python(self) -> str:
        return self.environment["MAP_NATIVE_PYTHON"]

    def native_command(self, stage: str, command: list[str], track: bool = False) -> None:
        self.command(stage, command, env=self.environment, track=track)

    def preflight(self) -> None:
        self.asset_root.mkdir(parents=True, exist_ok=True)
        required = 200 * 1024**3
        available = shutil.disk_usage(self.asset_root).free
        if available < required:
            raise RuntimeError(f"Mac build requires 200 GiB free; {available / 1024**3:.1f} GiB available")
        self.boundary.parent.mkdir(parents=True, exist_ok=True)
        self.native_command("preflight", ["ogr2ogr", "-overwrite", "-f", "GPKG", str(self.boundary), str(self.boundary_input), "-t_srs", "EPSG:4326", "-nln", "india", "-makevalid"])
        self.native_command("preflight", ["ogr2ogr", "-overwrite", "-f", "GeoJSON", str(self.boundary_geojson), str(self.boundary), "-lco", "RFC7946=YES"])
        collection = json.loads(self.boundary_geojson.read_text(encoding="utf-8"))
        features = collection.get("features", [])
        if len(features) != 1:
            raise RuntimeError(f"official boundary must contain one feature, received {len(features)}")
        self.boundary_geojson.write_text(json.dumps(features[0], separators=(",", ":")) + "\n", encoding="utf-8")
        self.event("preflight-passed", freeBytes=available, boundarySha256=sha256(self.boundary), platform="macos-arm64")

    def bounds(self) -> list[float]:
        report = json.loads(subprocess.check_output(["ogrinfo", "-so", "-al", "-json", str(self.boundary)], text=True, env=self.environment))
        extent = report["layers"][0]["geometryFields"][0]["extent"]
        return [extent[0], extent[1], extent[2], extent[3]]

    def acquire(self) -> None:
        self.native_command("acquire", [
            self.python, str(ROOT / "pipeline/acquire_india.py"), "--root", str(self.asset_root),
            "--lock", str(ROOT / "config/sources.lock.json"), "--bounds", *map(str, self.bounds()),
            "--boundary", str(self.boundary_geojson), "--min-free-gib", "25",
            "--workers", "12",
        ], track=True)

    def clip_osm(self, output: pathlib.Path) -> None:
        output.parent.mkdir(parents=True, exist_ok=True)
        source = self.asset_root / "sources/osm/india-260812.osm.pbf"
        self.native_command("vector", [
            "osmium", "extract", "--strategy=complete_ways", "--polygon", str(self.boundary_geojson),
            "--overwrite", "--progress", "--output", str(output), str(source),
        ])

    def mosaics(self) -> None:
        target = self.work / "mosaics"
        target.mkdir(parents=True, exist_ok=True)
        dem = sorted((self.asset_root / "sources/terrain").glob("*.tif"))
        landcover = sorted((self.asset_root / "sources/landcover").glob("*.tif"))
        self.native_command("mosaics", ["gdalbuildvrt", str(target / "dem.vrt"), *map(str, dem)])
        self.native_command("mosaics", ["gdalbuildvrt", str(target / "landcover.vrt"), *map(str, landcover)])

    def build_vector(self, source: pathlib.Path, output: pathlib.Path) -> None:
        output.parent.mkdir(parents=True, exist_ok=True)
        classes = self.work / "classes"
        classes.mkdir(parents=True, exist_ok=True)
        jar = self.asset_root / "planetiler/planetiler.jar"
        java_sources = [str(path) for path in sorted((ROOT / "planetiler").glob("*.java"))]
        self.native_command("vector", ["javac", "-proc:none", "-cp", str(jar), "-d", str(classes), *java_sources])
        environment = {
            **self.environment,
            "MAP_OSM_SOURCE": str(source), "MAP_VECTOR_OUTPUT": str(output),
            "JAVA_TOOL_OPTIONS": "-Xmx96g -XX:ActiveProcessorCount=20",
        }
        self.command("vector", ["java", "-cp", f"{jar}:{classes}", "IndiaPoc", "--force"], env=environment)

    def build_raster(self, renderer: str, output: pathlib.Path, zoom: int, dem: pathlib.Path, landcover: pathlib.Path) -> None:
        output.parent.mkdir(parents=True, exist_ok=True)
        self.native_command(f"{renderer}-raster", [
            self.python, str(ROOT / "pipeline/renderers/render_india.py"), "--renderer", renderer,
            "--output", str(output), "--work", str(self.work / "scratch" / renderer),
            "--bounds", *map(str, self.bounds()), "--zoom", str(zoom), "--metatile", "4",
            "--workers", "12", "--dem", str(dem), "--landcover", str(landcover),
            "--boundary", str(self.boundary), "--min-free-gib", "25",
        ], track=True)
        overviews = ["2", "4", "8", "16", "32"] if renderer == "elden" else ["2", "4", "8", "16"]
        self.native_command(f"{renderer}-raster", ["gdaladdo", "-r", "average", str(output), *overviews])

    def convert(self, source: pathlib.Path, output: pathlib.Path) -> None:
        output.parent.mkdir(parents=True, exist_ok=True)
        self.native_command("package", [self.environment["MAP_NATIVE_PMTILES"], "convert", str(source), str(output)])


def runtime() -> pathlib.Path:
    return ASSET_ROOT / "runtime"


def status() -> None:
    path = runtime() / "status.json"
    if not path.exists():
        print("No native Mac build has run.")
        return
    state = json.loads(path.read_text(encoding="utf-8"))
    if state.get("state") in {"running", "paused"}:
        try:
            os.kill(int(state["pid"]), 0)
        except ProcessLookupError:
            state.update(state="stale", message="process stopped; run resume to continue checkpoints")
    progress = float(state.get("progress", 0))
    filled = round(progress * 0.4)
    print(f"[{'#' * filled}{'-' * (40 - filled)}] {progress:6.2f}%  {state['state']} / {state['stage']}")
    print(f"build: {state['buildId']}")
    print(f"stage: {state.get('stageCompleted', 0)}/{state.get('stageTotal', 1)}")
    if state.get("message"):
        print(f"message: {state['message']}")
    if state.get("webRoot"):
        print(f"web root: {state['webRoot']}")


def update_runtime(**fields: object) -> dict:
    path = runtime() / "status.json"
    state = json.loads(path.read_text(encoding="utf-8"))
    state.update(fields, updatedAt=now())
    atomic_json(path, state)
    return state


def pause() -> None:
    state = update_runtime(state="paused", pausedStage=json.loads((runtime() / "status.json").read_text())["stage"], message="paused by user")
    os.killpg(int(state["pid"]), signal.SIGSTOP)
    print("Paused native Mac build. Run scripts/map-assets-mac resume to continue.")


def resume() -> None:
    path = runtime() / "status.json"
    if path.exists():
        state = json.loads(path.read_text(encoding="utf-8"))
        try:
            os.kill(int(state["pid"]), 0)
            if state.get("state") == "paused":
                os.killpg(int(state["pid"]), signal.SIGCONT)
                update_runtime(state="running", message=None)
                print("Resumed native Mac build.")
                return
        except ProcessLookupError:
            pass
    start()


def execute() -> None:
    environment = install_dependencies()
    boundary = download_boundary()
    MacPipeline.create(environment, boundary).execute()


def start() -> None:
    runtime().mkdir(parents=True, exist_ok=True)
    if (runtime() / "status.json").exists():
        state = json.loads((runtime() / "status.json").read_text(encoding="utf-8"))
        try:
            os.kill(int(state["pid"]), 0)
            raise SystemExit("Native Mac build is already running or paused")
        except ProcessLookupError:
            pass
    log = (runtime() / "pipeline.log").open("a", encoding="utf-8")
    process = subprocess.Popen([sys.executable, __file__, "run"], cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    log.close()
    atomic_json(runtime() / "status.json", {
        "schemaVersion": 1, "buildId": "pending", "pid": process.pid, "state": "running",
        "stage": "bootstrap", "progress": 0, "stageCompleted": 0, "stageTotal": 1,
        "startedAt": now(), "updatedAt": now(),
    })
    print(f"Started native Mac build PID {process.pid}")
    print("Status: scripts/map-assets-mac status")
    print("Pause: scripts/map-assets-mac pause")
    print(f"Log: {runtime() / 'pipeline.log'}")


def verify(path: pathlib.Path | None) -> None:
    if path is None:
        state = json.loads((runtime() / "status.json").read_text(encoding="utf-8"))
        path = pathlib.Path(str(state["webRoot"]))
    checksums = path / "SHA256SUMS"
    for line in checksums.read_text(encoding="utf-8").splitlines():
        expected, relative = line.split("  ", 1)
        asset = path / relative
        if not asset.is_file() or sha256(asset) != expected:
            raise SystemExit(f"checksum mismatch: {relative}")
    print(f"Verified native Mac production web root {path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("run", "start", "pause", "resume", "status", "verify"), nargs="?", default="status")
    parser.add_argument("path", nargs="?", type=pathlib.Path)
    args = parser.parse_args()
    if args.command == "run":
        execute()
    elif args.command == "start":
        start()
    elif args.command == "pause":
        pause()
    elif args.command == "resume":
        resume()
    elif args.command == "verify":
        verify(args.path)
    else:
        status()


if __name__ == "__main__":
    main()
