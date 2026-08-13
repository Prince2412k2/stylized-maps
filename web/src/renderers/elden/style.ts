import type {LayerSpecification, StyleSpecification} from "maplibre-gl";
import type {ReleaseCatalog} from "../../map/catalog";
import {nfsStyle} from "../nfs/style";

export function eldenStyle(catalog: ReleaseCatalog): StyleSpecification {
  if (!catalog.products.eldenBase) {
    throw new Error(catalog.renderers.elden.reason ?? "Relic illustrated raster products are unavailable.");
  }

  const base = nfsStyle(catalog);
  const layers = base.layers
    .filter((layer) => !["open-ground", "urban-ground", "grass", "wood", "wetland"].includes(layer.id))
    .map(relicLayer);
  layers.splice(1, 0, {
    id: "elden-base",
    type: "raster",
    source: "elden-base",
    paint: {"raster-opacity": 1, "raster-fade-duration": 0, "raster-resampling": "nearest"}
  });

  return {
    ...base,
    sources: {
      ...base.sources,
      "elden-base": {type: "raster", url: `pmtiles://${catalog.products.eldenBase}?release=${catalog.releaseId}`, tileSize: 256, attribution: "© Copernicus DEM 2021 · © ESA WorldCover project 2021"}
    },
    layers
  };
}

function relicLayer(layer: LayerSpecification): LayerSpecification {
  if (layer.id === "land" && layer.type === "background") return {...layer, paint: {"background-color": "#8a7a5c"}};
  if (layer.id === "water" && layer.type === "fill") return {...layer, paint: {"fill-color": "rgba(82,110,108,0.34)", "fill-outline-color": "#40504d"}};
  if (layer.id === "waterways" && layer.type === "line") return {...layer, paint: {...layer.paint, "line-color": "#536f69", "line-opacity": 0.75}};
  if (layer.id === "minor-roads-casing" && layer.type === "line") return {...layer, minzoom: 13, paint: {...layer.paint, "line-color": "rgba(45,34,24,0.68)", "line-opacity": 0.84, "line-width": ["interpolate", ["exponential", 1.35], ["zoom"], 13, 1.1, 16, 5.4]}};
  if (layer.id === "minor-roads" && layer.type === "line") return {...layer, minzoom: 13, paint: {...layer.paint, "line-color": "#bfa977", "line-opacity": 0.82, "line-width": ["interpolate", ["exponential", 1.35], ["zoom"], 13, 0.45, 16, 3.1]}};
  if (layer.id === "major-roads-casing" && layer.type === "line") return {...layer, paint: {...layer.paint, "line-color": "#3f3021", "line-width": ["interpolate", ["exponential", 1.3], ["zoom"], 10, 2.2, 16, 10]}};
  if (layer.id === "major-roads" && layer.type === "line") return {...layer, paint: {...layer.paint, "line-color": "#c7ab70", "line-width": ["interpolate", ["exponential", 1.3], ["zoom"], 10, 0.9, 16, 6]}};
  if (layer.id === "railways" && layer.type === "line") return {...layer, paint: {...layer.paint, "line-color": "#46382b", "line-dasharray": [4, 2]}};
  if (layer.id === "route-casing" && layer.type === "line") return {...layer, paint: {...layer.paint, "line-color": "#4b3523"}};
  if (layer.id === "route-line" && layer.type === "line") return {...layer, paint: {...layer.paint, "line-color": "#d4a32d"}};
  if (["poi-fuel", "poi-shops", "poi-other"].includes(layer.id) && layer.type === "circle") return {...layer, minzoom: 15, paint: {...layer.paint, "circle-color": "#5f3428", "circle-stroke-color": "#d5bb78"}};
  if (layer.id === "poi-labels" && layer.type === "symbol") return {...layer, minzoom: 15, paint: {...layer.paint, "text-color": "#3f3020", "text-halo-color": "rgba(202,178,125,0.96)"}};
  if (["road-labels", "places"].includes(layer.id) && layer.type === "symbol") return {...layer, paint: {...layer.paint, "text-color": "#3f3020", "text-halo-color": "rgba(202,178,125,0.96)"}};
  return layer;
}
