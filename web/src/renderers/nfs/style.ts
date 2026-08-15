import type {FilterSpecification, StyleSpecification} from "maplibre-gl";
import type {ReleaseCatalog} from "../../map/catalog";
import {layers, poiName, poiType} from "../../map/normalizedSchema";

const majorRoads: FilterSpecification = ["match", ["get", "class"], ["motorway", "trunk", "primary", "secondary"], true, false];
const minorRoads: FilterSpecification = ["match", ["get", "class"], ["tertiary", "residential", "service", "unclassified"], true, false];

export function nfsStyle(catalog: ReleaseCatalog): StyleSpecification {
  return {
    version: 8,
    glyphs: "/fonts/{fontstack}/{range}.pbf",
    sources: {
      core: {type: "vector", url: `pmtiles://${catalog.products.coreVector}?release=${catalog.releaseId}`, attribution: "© OpenStreetMap contributors"},
      boundary: {type: "geojson", data: catalog.products.boundary, attribution: "Survey of India"},
      route: {type: "geojson", data: {type: "FeatureCollection", features: []}}
    },
    layers: [
      {id: "sea", type: "background", paint: {"background-color": "#060c10"}},
      {id: "land", type: "fill", source: "boundary", paint: {"fill-color": "#211515"}},
      {id: "open-ground", type: "fill", source: "core", "source-layer": layers.landcover, filter: ["match", ["get", "class"], ["grass", "farmland", "meadow"], true, false], paint: {"fill-color": "#725534", "fill-opacity": 0.92}},
      {id: "urban-ground", type: "fill", source: "core", "source-layer": layers.landuse, filter: ["==", ["get", "class"], "residential"], paint: {"fill-color": "#241d2d", "fill-opacity": 0.96}},
      {id: "grass", type: "fill", source: "core", "source-layer": layers.landcover, filter: ["match", ["get", "class"], ["grass", "meadow"], true, false], paint: {"fill-color": "#74422f", "fill-opacity": 0.86}},
      {id: "wood", type: "fill", source: "core", "source-layer": layers.landcover, filter: ["==", ["get", "class"], "forest"], paint: {"fill-color": "#263f3b", "fill-opacity": 0.98}},
      {id: "wetland", type: "fill", source: "core", "source-layer": layers.landcover, filter: ["match", ["get", "class"], ["wetland", "scrub"], true, false], paint: {"fill-color": "#214b50", "fill-opacity": 0.9}},
      {id: "water", type: "fill", source: "core", "source-layer": layers.water, paint: {"fill-color": "#073d42", "fill-outline-color": "#176d70"}},
      {id: "waterways", type: "line", source: "core", "source-layer": layers.waterways, paint: {"line-color": "#16868b", "line-width": ["interpolate", ["linear"], ["zoom"], 9, 0.7, 15, 3]}},
      {id: "coastline", type: "line", source: "boundary", paint: {"line-color": "#17d6c4", "line-opacity": 0.6, "line-width": ["interpolate", ["linear"], ["zoom"], 4, 0.8, 10, 1.8]}},
      {id: "minor-roads-casing", type: "line", source: "core", "source-layer": layers.roads, minzoom: 11, filter: minorRoads, layout: {"line-cap": "round", "line-join": "round"}, paint: {"line-color": "#080d12", "line-width": ["interpolate", ["exponential", 1.35], ["zoom"], 11, 1.2, 16, 7]}},
      {id: "minor-roads", type: "line", source: "core", "source-layer": layers.roads, minzoom: 11, filter: minorRoads, layout: {"line-cap": "round", "line-join": "round"}, paint: {"line-color": "#a8a8aa", "line-opacity": 0.78, "line-width": ["interpolate", ["exponential", 1.35], ["zoom"], 11, 0.45, 16, 4]}},
      {id: "major-roads-casing", type: "line", source: "core", "source-layer": layers.roads, filter: majorRoads, layout: {"line-cap": "round", "line-join": "round"}, paint: {"line-color": "#070a0d", "line-width": ["interpolate", ["exponential", 1.3], ["zoom"], 8, 2.8, 12, 7, 16, 17]}},
      {id: "major-roads", type: "line", source: "core", "source-layer": layers.roads, filter: majorRoads, layout: {"line-cap": "round", "line-join": "round"}, paint: {"line-color": "#f0eee9", "line-width": ["interpolate", ["exponential", 1.3], ["zoom"], 8, 1.25, 12, 3.75, 16, 10.5]}},
      {id: "railways", type: "line", source: "core", "source-layer": layers.roads, minzoom: 9, filter: ["==", ["get", "class"], "rail"], paint: {"line-color": "#7f465a", "line-width": ["interpolate", ["linear"], ["zoom"], 9, 0.8, 16, 3], "line-dasharray": [1, 2]}},
      {id: "road-labels", type: "symbol", source: "core", "source-layer": layers.roads, minzoom: 13, filter: ["all", ["has", "name"], ["!=", ["get", "class"], "rail"]], layout: {"symbol-placement": "line", "symbol-spacing": 350, "text-field": ["get", "name"], "text-font": ["Noto Sans Regular"], "text-size": ["interpolate", ["linear"], ["zoom"], 13, 9, 17, 12], "text-letter-spacing": 0.04}, paint: {"text-color": "#d7e7e5", "text-halo-color": "rgba(8,15,20,0.95)", "text-halo-width": 1.5}},
      {id: "route-casing", type: "line", source: "route", layout: {"line-cap": "round", "line-join": "round"}, paint: {"line-color": "#38172b", "line-width": ["interpolate", ["linear"], ["zoom"], 9, 7, 16, 13]}},
      {id: "route-line", type: "line", source: "route", layout: {"line-cap": "round", "line-join": "round"}, paint: {"line-color": "#f12f61", "line-width": ["interpolate", ["linear"], ["zoom"], 9, 4, 16, 8]}},
      {id: "poi-fuel", type: "circle", source: "core", "source-layer": layers.pois, minzoom: 13, filter: ["==", ["get", "subcategory"], "fuel"], paint: {"circle-radius": ["interpolate", ["linear"], ["zoom"], 13, 4, 17, 8], "circle-color": "#17d6c4", "circle-stroke-color": "#071114", "circle-stroke-width": 2}},
      {id: "poi-shops", type: "circle", source: "core", "source-layer": layers.pois, minzoom: 14, filter: ["==", ["get", "category"], "shop"], paint: {"circle-radius": ["interpolate", ["linear"], ["zoom"], 14, 3, 17, 6], "circle-color": "#071114", "circle-stroke-color": "#17d6c4", "circle-stroke-width": 1.5}},
      {id: "poi-other", type: "circle", source: "core", "source-layer": layers.pois, minzoom: 12, filter: ["all", ["!=", ["get", "category"], "shop"], ["!=", ["get", "subcategory"], "fuel"]], paint: {"circle-radius": ["interpolate", ["linear"], ["zoom"], 12, 3, 17, 7], "circle-color": "#17d6c4", "circle-stroke-color": "#071114", "circle-stroke-width": 1.5}},
      {id: "poi-labels", type: "symbol", source: "core", "source-layer": layers.pois, minzoom: 12, filter: ["any", ["has", "name"], ["has", "name:en"], ["has", "brand"]], layout: {"text-field": ["format", poiName, {"font-scale": 1}, "\n", {}, poiType, {"font-scale": 0.78}], "text-font": ["Noto Sans Regular"], "text-size": ["interpolate", ["linear"], ["zoom"], 12, 10, 16, 12], "text-offset": [0, 1.25], "text-anchor": "top", "text-max-width": 13, "text-padding": 3}, paint: {"text-color": "#f0faf7", "text-halo-color": "rgba(8,15,20,0.96)", "text-halo-width": 2}},
      {id: "places", type: "symbol", source: "core", "source-layer": layers.places, minzoom: 5, maxzoom: 15, layout: {"text-field": ["coalesce", ["get", "name:en"], ["get", "name"]], "text-font": ["Noto Sans Bold"], "text-letter-spacing": 0.12, "text-size": ["interpolate", ["linear"], ["zoom"], 5, 10, 8, 12, 14, 17], "text-transform": "uppercase"}, paint: {"text-color": "#e5efec", "text-halo-color": "rgba(10,16,22,0.94)", "text-halo-width": 2}}
    ]
  };
}
