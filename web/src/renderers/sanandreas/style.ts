import type {LayerSpecification, StyleSpecification} from "maplibre-gl";
import type {ReleaseCatalog} from "../../map/catalog";
import {nfsStyle} from "../nfs/style";

export function sanAndreasStyle(catalog: ReleaseCatalog): StyleSpecification {
  const base = nfsStyle(catalog);
  return {...base, layers: base.layers.map(sanAndreasLayer)};
}

function sanAndreasLayer(layer: LayerSpecification): LayerSpecification {
  if (layer.id === "land" && layer.type === "background") return {...layer, paint: {"background-color": "#7f963d"}};
  if (layer.id === "open-ground" && layer.type === "fill") return {...layer, paint: {...layer.paint, "fill-color": "#80983e", "fill-opacity": 0.95}};
  if (layer.id === "urban-ground" && layer.type === "fill") return {...layer, paint: {...layer.paint, "fill-color": "#aaa9a8", "fill-opacity": 1}};
  if (layer.id === "grass" && layer.type === "fill") return {...layer, paint: {...layer.paint, "fill-color": "#769036", "fill-opacity": 1}};
  if (layer.id === "wood" && layer.type === "fill") return {...layer, paint: {...layer.paint, "fill-color": "#356b2d", "fill-opacity": 1}};
  if (layer.id === "wetland" && layer.type === "fill") return {...layer, paint: {...layer.paint, "fill-color": "#667c46", "fill-opacity": 1}};
  if (layer.id === "water" && layer.type === "fill") return {...layer, paint: {"fill-color": "#7d96bb", "fill-outline-color": "#5b7397"}};
  if (layer.id === "waterways" && layer.type === "line") return {...layer, paint: {...layer.paint, "line-color": "#617da5"}};
  if (layer.id === "minor-roads-casing" && layer.type === "line") return {...layer, paint: {...layer.paint, "line-color": "#090a08", "line-width": ["interpolate", ["exponential", 1.35], ["zoom"], 11, 1.4, 16, 9]}};
  if (layer.id === "minor-roads" && layer.type === "line") return {...layer, paint: {...layer.paint, "line-color": "#f0efea", "line-width": ["interpolate", ["exponential", 1.35], ["zoom"], 11, 0.5, 16, 5.4]}};
  if (layer.id === "major-roads-casing" && layer.type === "line") return {...layer, paint: {...layer.paint, "line-color": "#050604", "line-width": ["interpolate", ["exponential", 1.3], ["zoom"], 8, 2.2, 12, 5.5, 16, 15]}};
  if (layer.id === "major-roads" && layer.type === "line") return {...layer, paint: {...layer.paint, "line-color": "#d2d0cb", "line-width": ["interpolate", ["exponential", 1.3], ["zoom"], 8, 0.9, 12, 2.7, 16, 10.5]}};
  if (layer.id === "railways" && layer.type === "line") return {...layer, paint: {...layer.paint, "line-color": "#641d12", "line-dasharray": [3, 2]}};
  if (layer.id === "route-casing" && layer.type === "line") return {...layer, paint: {...layer.paint, "line-color": "#f4eee0"}};
  if (layer.id === "route-line" && layer.type === "line") return {...layer, paint: {...layer.paint, "line-color": "#c63224"}};
  if (["poi-fuel", "poi-shops", "poi-other"].includes(layer.id) && layer.type === "circle") return {...layer, paint: {...layer.paint, "circle-color": "#d79a32", "circle-stroke-color": "#171812"}};
  if (["road-labels", "poi-labels", "places"].includes(layer.id) && layer.type === "symbol") return {...layer, paint: {...layer.paint, "text-color": "#11120f", "text-halo-color": "rgba(235,234,225,0.96)"}};
  return layer;
}
