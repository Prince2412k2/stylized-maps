from __future__ import annotations

import argparse
import io
import math
import os
import pathlib
import shutil
import sqlite3
import sys
from contextlib import redirect_stdout

from osgeo import gdal, ogr, osr

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from render_tracer import render_elden, render_rdr

gdal.UseExceptions()
ORIGIN = 20037508.342789244
TILE_SIZE = 256


def tile_x(longitude: float, zoom: int) -> int:
    return math.floor((longitude + 180) / 360 * (1 << zoom))


def tile_y(latitude: float, zoom: int) -> int:
    value = math.asinh(math.tan(math.radians(latitude)))
    return math.floor((1 - value / math.pi) / 2 * (1 << zoom))


def tile_bounds(x: int, y: int, zoom: int) -> tuple[float, float, float, float]:
    resolution = 2 * ORIGIN / (TILE_SIZE * (1 << zoom))
    west = -ORIGIN + x * TILE_SIZE * resolution
    east = west + TILE_SIZE * resolution
    north = ORIGIN - y * TILE_SIZE * resolution
    south = north - TILE_SIZE * resolution
    return west, south, east, north


def create_mbtiles(path: pathlib.Path, name: str, bounds: tuple[float, float, float, float], zoom: int) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.executescript("""
        PRAGMA journal_mode=WAL;
        PRAGMA synchronous=NORMAL;
        CREATE TABLE IF NOT EXISTS metadata (name TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS tiles (
            zoom_level INTEGER,
            tile_column INTEGER,
            tile_row INTEGER,
            tile_data BLOB,
            PRIMARY KEY (zoom_level, tile_column, tile_row)
        );
        CREATE TABLE IF NOT EXISTS completed_jobs (job TEXT PRIMARY KEY);
    """)
    metadata = {
        "name": name,
        "type": "overlay",
        "version": "1.3",
        "format": "png",
        "bounds": ",".join(str(value) for value in bounds),
        "minzoom": str(zoom),
        "maxzoom": str(zoom),
    }
    connection.executemany("INSERT OR REPLACE INTO metadata(name, value) VALUES(?, ?)", metadata.items())
    connection.commit()
    return connection


def encode_tiles(dataset: gdal.Dataset, zoom: int, start_x: int, start_y: int, width: int, height: int, offset: int) -> list[tuple[int, int, int, bytes]]:
    tiles = []
    for offset_y in range(height):
        for offset_x in range(width):
            alpha = dataset.GetRasterBand(4).ReadAsArray(
                offset + offset_x * TILE_SIZE, offset + offset_y * TILE_SIZE, TILE_SIZE, TILE_SIZE
            )
            if alpha is not None and not alpha.any():
                continue
            memory_path = f"/vsimem/{os.getpid()}-{offset_x}-{offset_y}.png"
            gdal.Translate(
                memory_path,
                dataset,
                format="PNG",
                srcWin=[offset + offset_x * TILE_SIZE, offset + offset_y * TILE_SIZE, TILE_SIZE, TILE_SIZE],
                creationOptions=["ZLEVEL=6"],
            )
            contents = gdal.VSIGetMemFileBuffer_unsafe(memory_path)
            tiles.append((zoom, start_x + offset_x, (1 << zoom) - 1 - (start_y + offset_y), bytes(contents)))
            gdal.Unlink(memory_path)
    return tiles


def boundary_geometry(path: str) -> ogr.Geometry:
    source = ogr.Open(path)
    layer = source.GetLayer(0)
    source_srs = layer.GetSpatialRef()
    target_srs = osr.SpatialReference()
    target_srs.ImportFromEPSG(3857)
    transform = osr.CoordinateTransformation(source_srs, target_srs)
    geometry = ogr.Geometry(ogr.wkbMultiPolygon)
    for feature in layer:
        part = feature.GetGeometryRef().Clone()
        part.Transform(transform)
        geometry = geometry.Union(part)
    return geometry


def intersects_boundary(boundary: ogr.Geometry, x: int, y: int, width: int, height: int, zoom: int) -> bool:
    west, south, _, north = tile_bounds(x, y + height - 1, zoom)
    _, _, east, _ = tile_bounds(x + width - 1, y, zoom)
    ring = ogr.Geometry(ogr.wkbLinearRing)
    for point in ((west, south), (east, south), (east, north), (west, north), (west, south)):
        ring.AddPoint(*point)
    polygon = ogr.Geometry(ogr.wkbPolygon)
    polygon.AddGeometry(ring)
    return boundary.Intersects(polygon)


def render_metatile(
    renderer: str,
    dem_sources: list[str],
    landcover_source: str,
    boundary: str,
    zoom: int,
    start_x: int,
    start_y: int,
    width: int,
    height: int,
    work: pathlib.Path,
) -> list[tuple[int, int, int, bytes]]:
    west, south, _, north = tile_bounds(start_x, start_y + height - 1, zoom)
    _, _, east, _ = tile_bounds(start_x + width - 1, start_y, zoom)
    scale = 2 if renderer == "elden" else 1
    halo = 32
    resolution = 2 * ORIGIN / (TILE_SIZE * (1 << zoom))
    west -= halo * resolution
    south -= halo * resolution
    east += halo * resolution
    north += halo * resolution
    pixel_width = (width * TILE_SIZE + halo * 2) * scale
    pixel_height = (height * TILE_SIZE + halo * 2) * scale
    dem = work / f"{renderer}-dem.tif"
    landcover = work / f"{renderer}-landcover.tif"
    painted = work / f"{renderer}-painted.tif"
    output = work / f"{renderer}-output.tif"
    warp_options = {"format": "GTiff", "dstSRS": "EPSG:3857", "outputBounds": [west, south, east, north], "width": pixel_width, "height": pixel_height}
    gdal.Warp(str(dem), dem_sources, resampleAlg="bilinear", dstNodata=0, **warp_options)
    gdal.Warp(str(landcover), landcover_source, resampleAlg="near", **warp_options)
    with redirect_stdout(io.StringIO()):
        if renderer == "elden":
            render_elden(str(dem), str(landcover), str(painted))
        else:
            render_rdr(str(dem), str(landcover), str(painted))
    source = gdal.Open(str(painted))
    if scale == 2:
        gdal.Translate(str(output), source, width=width * TILE_SIZE + halo * 2, height=height * TILE_SIZE + halo * 2, resampleAlg="lanczos", creationOptions=["TILED=YES", "COMPRESS=DEFLATE"])
        source = gdal.Open(str(output))
    clipped = work / f"{renderer}-clipped.tif"
    gdal.Warp(
        str(clipped), source, format="GTiff", cutlineDSName=boundary, dstAlpha=True,
        outputBounds=[west, south, east, north], width=width * TILE_SIZE + halo * 2,
        height=height * TILE_SIZE + halo * 2, dstSRS="EPSG:3857",
        creationOptions=["TILED=YES", "COMPRESS=DEFLATE"],
    )
    source = gdal.Open(str(clipped))
    tiles = encode_tiles(source, zoom, start_x, start_y, width, height, halo)
    source = None
    for path in work.glob(f"{renderer}-*.tif"):
        path.unlink()
    return tiles


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--renderer", choices=("rdr", "elden"), required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    parser.add_argument("--work", type=pathlib.Path, required=True)
    parser.add_argument("--bounds", nargs=4, type=float, required=True)
    parser.add_argument("--zoom", type=int, required=True)
    parser.add_argument("--metatile", type=int, default=4)
    parser.add_argument("--dem", action="append", required=True)
    parser.add_argument("--landcover", required=True)
    parser.add_argument("--boundary", required=True)
    parser.add_argument("--min-free-gib", type=float, default=5)
    args = parser.parse_args()

    west, south, east, north = args.bounds
    min_x, max_x = tile_x(west, args.zoom), tile_x(east, args.zoom)
    min_y, max_y = tile_y(north, args.zoom), tile_y(south, args.zoom)
    boundary = boundary_geometry(args.boundary)
    jobs = [
        (x, y, min(args.metatile, max_x - x + 1), min(args.metatile, max_y - y + 1))
        for y in range(min_y, max_y + 1, args.metatile)
        for x in range(min_x, max_x + 1, args.metatile)
        if intersects_boundary(boundary, x, y, min(args.metatile, max_x - x + 1), min(args.metatile, max_y - y + 1), args.zoom)
    ]
    connection = create_mbtiles(args.output, f"{args.renderer} India", tuple(args.bounds), args.zoom)
    args.work.mkdir(parents=True, exist_ok=True)
    completed = 0
    for start_x, start_y, width, height in jobs:
        if shutil.disk_usage(args.output.parent).free < args.min_free_gib * 1024**3:
            raise RuntimeError(f"render stopped before exhausting disk: less than {args.min_free_gib:g} GiB free")
        job = f"{args.zoom}/{start_x}/{start_y}/{width}/{height}"
        existing = connection.execute("SELECT 1 FROM completed_jobs WHERE job=?", (job,)).fetchone()
        if existing is None:
            tiles = render_metatile(args.renderer, args.dem, args.landcover, args.boundary, args.zoom, start_x, start_y, width, height, args.work)
            connection.executemany("INSERT OR REPLACE INTO tiles VALUES(?, ?, ?, ?)", tiles)
            connection.execute("INSERT INTO completed_jobs(job) VALUES(?)", (job,))
            connection.commit()
        completed += 1
        print(f"PROGRESS {completed} {len(jobs)}", flush=True)
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    connection.execute("VACUUM")
    connection.close()


if __name__ == "__main__":
    main()
