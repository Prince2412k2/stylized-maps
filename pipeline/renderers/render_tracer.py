from osgeo import gdal
import math
import numpy as np
import sys


def read(path):
    dataset = gdal.Open(path)
    return dataset, dataset.GetRasterBand(1).ReadAsArray()


def hash_noise(x, y, seed):
    value = x.astype(np.uint64) * np.uint64(0x9E3779B185EBCA87)
    value ^= y.astype(np.uint64) * np.uint64(0xC2B2AE3D27D4EB4F)
    value ^= np.uint64(seed)
    value ^= value >> np.uint64(30)
    value *= np.uint64(0xBF58476D1CE4E5B9)
    value ^= value >> np.uint64(27)
    value *= np.uint64(0x94D049BB133111EB)
    value ^= value >> np.uint64(31)
    return (value & np.uint64(0xFFFF)).astype(np.float32) / 65535


def value_noise(world_x, world_y, scale, seed):
    grid_x = world_x / scale
    grid_y = world_y / scale
    x0 = np.floor(grid_x).astype(np.int64)
    y0 = np.floor(grid_y).astype(np.int64)
    tx = (grid_x - x0).astype(np.float32)
    ty = (grid_y - y0).astype(np.float32)
    tx = tx * tx * (3 - 2 * tx)
    ty = ty * ty * (3 - 2 * ty)
    top = hash_noise(x0, y0, seed) * (1 - tx) + hash_noise(x0 + 1, y0, seed) * tx
    bottom = hash_noise(x0, y0 + 1, seed) * (1 - tx) + hash_noise(x0 + 1, y0 + 1, seed) * tx
    return top * (1 - ty) + bottom * ty


def hillshade(dx, dy, azimuth, altitude=42):
    slope = np.arctan(np.hypot(dx, dy))
    aspect = np.arctan2(dy, -dx)
    azimuth = np.deg2rad(360 - azimuth + 90)
    altitude = np.deg2rad(altitude)
    return np.clip(np.sin(altitude) * np.cos(slope) + np.cos(altitude) * np.sin(slope) * np.cos(azimuth - aspect), 0, 1)


def paint_marks(image, mask, dx, dy, world_x, world_y, spacing, seed, kind):
    height, width = mask.shape
    grid_x = np.arange(0, width, spacing)
    grid_y = np.arange(0, height, spacing)
    candidates_x, candidates_y = np.meshgrid(grid_x, grid_y)
    global_x = np.floor(world_x[candidates_y, candidates_x] / spacing).astype(np.int64)
    global_y = np.floor(world_y[candidates_y, candidates_x] / spacing).astype(np.int64)
    noise = hash_noise(global_x, global_y, seed)
    jitter_x = ((noise * 997).astype(np.int32) % spacing)
    jitter_y = ((noise * 619).astype(np.int32) % spacing)
    candidates_x = np.clip(candidates_x + jitter_x, 0, width - 1)
    candidates_y = np.clip(candidates_y + jitter_y, 0, height - 1)

    for x, y, candidate_noise in zip(candidates_x.ravel(), candidates_y.ravel(), noise.ravel()):
        if not mask[y, x]:
            continue
        if kind == "hachure":
            slope = np.hypot(dx[y, x], dy[y, x])
            if slope < 0.06:
                continue
            length = min(22, 5 + int(slope * 15))
            angle = np.arctan2(dy[y, x], dx[y, x]) + (candidate_noise - 0.5) * 0.12
            x2 = int(round(x + np.cos(angle) * length))
            y2 = int(round(y + np.sin(angle) * length))
            count = max(abs(x2 - x), abs(y2 - y), 1) + 1
            xs = np.clip(np.linspace(x, x2, count).astype(int), 0, width - 1)
            ys = np.clip(np.linspace(y, y2, count).astype(int), 0, height - 1)
            image[ys, xs] = (image[ys, xs] * 0.52).astype(np.uint8)
        elif kind == "tree":
            for ox, oy in ((0, -4), (-3, 1), (3, 1), (-2, 4), (2, 4), (0, 5)):
                px, py = x + ox, y + oy
                if 0 <= px < width and 0 <= py < height:
                    image[py, px] = (image[py, px] * 0.55).astype(np.uint8)
        elif kind == "marsh":
            for offset, half_width in ((-3, 3), (1, 5), (5, 2)):
                py = y + offset
                if 0 <= py < height:
                    x0, x1 = max(0, x - half_width), min(width, x + half_width + 1)
                    image[py, x0:x1] = (image[py, x0:x1] * 0.62).astype(np.uint8)


def write_rgb(path, source, image):
    driver = gdal.GetDriverByName("GTiff")
    output = driver.Create(path, source.RasterXSize, source.RasterYSize, 3, gdal.GDT_Byte, options=["TILED=YES", "COMPRESS=DEFLATE"])
    output.SetGeoTransform(source.GetGeoTransform())
    output.SetProjection(source.GetProjection())
    for index in range(3):
        output.GetRasterBand(index + 1).WriteArray(image[:, :, index])
    output.FlushCache()


def raster_context(dem_path, landcover_path):
    dataset, dem = read(dem_path)
    _, landcover = read(landcover_path)
    dem = np.where((dem < -100) | ~np.isfinite(dem), 0, dem).astype(np.float32)
    transform = dataset.GetGeoTransform()
    resolution = abs(transform[1])
    dy, dx = np.gradient(dem, resolution, resolution)
    height, width = dem.shape
    columns, rows = np.meshgrid(np.arange(width), np.arange(height))
    world_x = transform[0] + (columns + 0.5) * transform[1]
    world_y = transform[3] + (rows + 0.5) * transform[5]
    return dataset, dem, landcover, dx, dy, world_x, world_y


def render_rdr(dem_path, landcover_path, output_path):
    dataset, _, landcover, dx, dy, world_x, world_y = raster_context(dem_path, landcover_path)
    shade = sum(hillshade(dx, dy, azimuth) for azimuth in (315, 45, 225)) / 3
    paper = value_noise(world_x, world_y, 70, 0x52445232)
    base = np.zeros((*landcover.shape, 3), dtype=np.float32)
    base[:] = (205, 175, 124)
    base[landcover == 80] = (133, 139, 124)
    base[landcover == 10] = (166, 157, 111)
    base[landcover == 50] = (190, 164, 116)
    base[landcover == 90] = (151, 148, 112)
    light = 0.62 + shade[:, :, None] * 0.42 + (paper[:, :, None] - 0.5) * 0.07
    write_rgb(output_path, dataset, np.clip(base * light, 0, 255).astype(np.uint8))


def render_elden(dem_path, landcover_path, output_path):
    dataset, _, landcover, dx, dy, world_x, world_y = raster_context(dem_path, landcover_path)
    shade = sum(hillshade(dx, dy, azimuth, 48) for azimuth in (315, 45, 225)) / 3
    palette = {
        10: (72, 82, 55), 20: (111, 105, 66), 30: (130, 116, 69), 40: (154, 127, 76),
        50: (139, 112, 78), 60: (130, 103, 69), 70: (199, 190, 164), 80: (91, 113, 112),
        90: (83, 105, 83), 95: (65, 91, 69), 100: (126, 117, 82)
    }
    image = np.empty((*landcover.shape, 3), dtype=np.float32)
    image[:] = (174, 146, 92)
    for category, color in palette.items():
        image[landcover == category] = color

    macro = value_noise(world_x, world_y, 1400, 0x454C4401)
    meso = value_noise(world_x, world_y, 190, 0x454C4402)
    micro = value_noise(world_x, world_y, 24, 0x454C4403)
    pigment = (macro - 0.5) * 0.22 + (meso - 0.5) * 0.10 + (micro - 0.5) * 0.035
    light = 0.72 + shade[:, :, None] * 0.30 + pigment[:, :, None]
    image = np.clip(image * light, 0, 255).astype(np.uint8)

    slope_mask = np.hypot(dx, dy) > 0.06
    paint_marks(image, slope_mask, dx, dy, world_x, world_y, 22, 0x48414348, "hachure")
    paint_marks(image, landcover == 10, dx, dy, world_x, world_y, 28, 0x54524545, "tree")
    paint_marks(image, np.isin(landcover, (90, 95)), dx, dy, world_x, world_y, 26, 0x4D415253, "marsh")
    write_rgb(output_path, dataset, image)


if __name__ == "__main__":
    rdr_dem, rdr_landcover, rdr_path, elden_dem, elden_landcover, elden_path = sys.argv[1:]
    render_rdr(rdr_dem, rdr_landcover, rdr_path)
    render_elden(elden_dem, elden_landcover, elden_path)
