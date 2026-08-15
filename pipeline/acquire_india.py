from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import math
import pathlib
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request


def coordinate(value: int, latitude: bool) -> str:
    prefix = ("N" if value >= 0 else "S") if latitude else ("E" if value >= 0 else "W")
    return f"{prefix}{abs(value):02d}" if latitude else f"{prefix}{abs(value):03d}"


def geometry_rings(geometry: dict) -> list[list[list[float]]]:
    if geometry["type"] == "Polygon":
        return geometry["coordinates"]
    if geometry["type"] == "MultiPolygon":
        return [ring for polygon in geometry["coordinates"] for ring in polygon]
    raise ValueError("India boundary must contain Polygon or MultiPolygon geometry")


def point_in_ring(x: float, y: float, ring: list[list[float]]) -> bool:
    inside = False
    previous_x, previous_y = ring[-1][:2]
    for current_x, current_y, *_ in ring:
        if (current_y > y) != (previous_y > y):
            crossing_x = (previous_x - current_x) * (y - current_y) / (previous_y - current_y) + current_x
            if x < crossing_x:
                inside = not inside
        previous_x, previous_y = current_x, current_y
    return inside


def orientation(a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def segments_intersect(a: tuple[float, float], b: tuple[float, float], c: tuple[float, float], d: tuple[float, float]) -> bool:
    return orientation(a, b, c) * orientation(a, b, d) <= 0 and orientation(c, d, a) * orientation(c, d, b) <= 0


def intersects(rings: list[list[list[float]]], west: float, south: float, east: float, north: float) -> bool:
    corners = ((west, south), (east, south), (east, north), (west, north))
    edges = tuple(zip(corners, corners[1:] + corners[:1]))
    for ring in rings:
        if any(west <= x <= east and south <= y <= north for x, y, *_ in ring):
            return True
        if any(point_in_ring(x, y, ring) for x, y in corners):
            return True
        points = [(point[0], point[1]) for point in ring]
        if any(segments_intersect(a, b, c, d) for a, b in zip(points, points[1:]) for c, d in edges):
            return True
    return False


def checksum(path: pathlib.Path, algorithm: str) -> str:
    digest = hashlib.new(algorithm)
    with path.open("rb") as source:
        while block := source.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def download(url: str, path: pathlib.Path, expected: tuple[str, str] | None = None, optional: bool = False) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and expected is None:
        return True
    if path.exists() and expected:
        algorithm, digest = expected
        actual = checksum(path, algorithm)
        if actual == digest:
            return True
        path.unlink()
    part = path.with_suffix(path.suffix + ".part")
    for attempt in range(1, 6):
        headers = {"User-Agent": "india-illustrated-map-builder/1"}
        if part.exists():
            headers["Range"] = f"bytes={part.stat().st_size}-"
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=120) as response:
                mode = "ab" if response.status == 206 else "wb"
                with part.open(mode) as output:
                    while block := response.read(1024 * 1024):
                        output.write(block)
            if expected:
                algorithm, digest = expected
                actual = checksum(part, algorithm)
                if actual != digest:
                    raise RuntimeError(f"checksum mismatch for {url}: expected {digest}, received {actual}")
            part.replace(path)
            return True
        except urllib.error.HTTPError as cause:
            if cause.code == 404 and optional:
                part.unlink(missing_ok=True)
                return False
            if attempt == 5:
                raise
        except (TimeoutError, urllib.error.URLError):
            if attempt == 5:
                raise
        time.sleep(2**attempt)
    return False


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=pathlib.Path, required=True)
    parser.add_argument("--lock", type=pathlib.Path, required=True)
    parser.add_argument("--bounds", nargs=4, type=float, required=True)
    parser.add_argument("--boundary", type=pathlib.Path, required=True)
    parser.add_argument("--min-free-gib", type=float, default=5)
    parser.add_argument("--workers", type=int, default=1)
    args = parser.parse_args()
    lock = json.loads(args.lock.read_text(encoding="utf-8"))["sources"]
    west, south, east, north = args.bounds
    boundary = json.loads(args.boundary.read_text(encoding="utf-8"))
    features = boundary["features"] if boundary["type"] == "FeatureCollection" else [boundary]
    rings = [ring for feature in features for ring in geometry_rings(feature["geometry"])]

    fixed = (
        (lock["osm"]["url"], args.root / lock["osm"]["path"], ("md5", lock["osm"]["md5"])),
        (lock["planetiler"]["url"], args.root / lock["planetiler"]["path"], ("sha256", lock["planetiler"]["sha256"])),
    )
    tasks: list[tuple[str, pathlib.Path, tuple[str, str] | None, bool]] = [(url, path, expected, False) for url, path, expected in fixed]

    dem_template = lock["indiaTerrain"]["demUrlTemplate"]
    for latitude in range(math.floor(south), math.ceil(north)):
        for longitude in range(math.floor(west), math.ceil(east)):
            if not intersects(rings, longitude, latitude, longitude + 1, latitude + 1):
                continue
            lat = coordinate(latitude, True)
            lon = coordinate(longitude, False)
            name = f"Copernicus_DSM_COG_10_{lat}_00_{lon}_00_DEM.tif"
            tasks.append((dem_template.format(lat=lat, lon=lon), args.root / "sources/terrain" / name, None, True))

    worldcover_template = lock["indiaTerrain"]["worldCoverUrlTemplate"]
    first_latitude = math.floor(south / 3) * 3
    first_longitude = math.floor(west / 3) * 3
    for latitude in range(first_latitude, math.ceil(north), 3):
        for longitude in range(first_longitude, math.ceil(east), 3):
            if not intersects(rings, longitude, latitude, longitude + 3, latitude + 3):
                continue
            lat = coordinate(latitude, True)
            lon = coordinate(longitude, False)
            name = f"ESA_WorldCover_10m_2021_v200_{lat}{lon}_Map.tif"
            tasks.append((worldcover_template.format(lat=lat, lon=lon), args.root / "sources/landcover" / name, None, True))

    glyphs = lock["glyphs"]
    for font in glyphs["fonts"]:
        encoded_font = urllib.parse.quote(font)
        for start in range(0, 65536, 256):
            glyph_range = f"{start}-{start + 255}"
            url = glyphs["urlTemplate"].format(font=encoded_font, range=glyph_range)
            tasks.append((url, args.root / "sources/glyphs" / font / f"{glyph_range}.pbf", None, False))

    def acquire(task: tuple[str, pathlib.Path, tuple[str, str] | None, bool]) -> dict[str, str | bool]:
        url, path, expected, optional = task
        if shutil.disk_usage(args.root).free < args.min_free_gib * 1024**3:
            raise RuntimeError(f"download stopped before exhausting disk: less than {args.min_free_gib:g} GiB free")
        present = download(url, path, expected, optional)
        return {"url": url, "path": str(path.relative_to(args.root)), "present": present}

    inventory: list[dict[str, str | bool] | None] = [None] * len(tasks)
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(acquire, task): index for index, task in enumerate(tasks)}
        for completed, future in enumerate(concurrent.futures.as_completed(futures), 1):
            inventory[futures[future]] = future.result()
            print(f"PROGRESS {completed} {len(tasks)}", flush=True)
    inventory_path = args.root / "sources/inventory.json"
    inventory_path.parent.mkdir(parents=True, exist_ok=True)
    inventory_path.write_text(json.dumps([entry for entry in inventory if entry is not None], indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
