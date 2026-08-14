import type {LayerSpecification, StyleSpecification} from "maplibre-gl";
import type {ReleaseCatalog} from "../../map/catalog";
import {nfsStyle} from "../nfs/style";

export function rdrStyle(catalog: ReleaseCatalog): StyleSpecification {
  if (!catalog.products.rdrTerrain) {
    throw new Error(catalog.renderers.rdr.reason ?? "Frontier terrain products are unavailable.");
  }

  const base = nfsStyle(catalog);
  const layers = base.layers
    .filter((layer) => !["open-ground", "urban-ground", "grass", "wood", "wetland"].includes(layer.id))
    .map(frontierLayer);
  layers.splice(1, 0, {
    id: "rdr-terrain",
    type: "raster",
    source: "rdr-terrain",
    paint: {"raster-opacity": 1, "raster-fade-duration": 0, "raster-resampling": "linear"}
  });

  return {
    ...base,
    sources: {
      ...base.sources,
      "rdr-terrain": {type: "raster", url: `pmtiles://${catalog.products.rdrTerrain}?release=${catalog.releaseId}`, tileSize: 256, attribution: "© Copernicus DEM 2021 · © ESA WorldCover project 2021"}
    },
    layers
  };
}

function frontierLayer(layer: LayerSpecification): LayerSpecification {
  if (layer.id === "land" && layer.type === "background") return {...layer, paint: {"background-color": "#cdb07b"}};
  if (layer.id === "water" && layer.type === "fill") return {...layer, paint: {"fill-color": "rgba(111, 117, 105, 0.52)", "fill-outline-color": "#666756"}};
  if (layer.id === "waterways" && layer.type === "line") return {...layer, paint: {...layer.paint, "line-color": "#626554"}};
  if (layer.id === "minor-roads-casing" && layer.type === "line") return {...layer, paint: {...layer.paint, "line-color": "rgba(205,176,123,0.7)", "line-width": ["interpolate", ["exponential", 1.35], ["zoom"], 11, 1.5, 16, 5]}};
  if (layer.id === "minor-roads" && layer.type === "line") return {...layer, paint: {...layer.paint, "line-color": "#51493b", "line-width": ["interpolate", ["exponential", 1.35], ["zoom"], 11, 0.55, 16, 2.8]}};
  if (layer.id === "major-roads-casing" && layer.type === "line") return {...layer, paint: {...layer.paint, "line-color": "#d8bd89", "line-width": ["interpolate", ["exponential", 1.3], ["zoom"], 10, 2.8, 16, 9]}};
  if (layer.id === "major-roads" && layer.type === "line") return {...layer, paint: {...layer.paint, "line-color": "#3d382f", "line-width": ["interpolate", ["exponential", 1.3], ["zoom"], 10, 1.2, 16, 5]}};
  if (layer.id === "railways" && layer.type === "line") return {...layer, paint: {...layer.paint, "line-color": "#272621", "line-dasharray": [5, 2]}};
  if (layer.id === "route-casing" && layer.type === "line") return {...layer, paint: {...layer.paint, "line-color": "#ead6a9"}};
  if (layer.id === "route-line" && layer.type === "line") return {...layer, paint: {...layer.paint, "line-color": "#9b2924"}};
  if (["poi-fuel", "poi-shops", "poi-other"].includes(layer.id) && layer.type === "circle") return {...layer, paint: {...layer.paint, "circle-color": "#4d5b3d", "circle-stroke-color": "#efe0b7"}};
  if (["road-labels", "poi-labels", "places"].includes(layer.id) && layer.type === "symbol") return {...layer, paint: {...layer.paint, "text-color": "#39342c", "text-halo-color": "rgba(215,188,137,0.94)"}};
  return layer;
}
