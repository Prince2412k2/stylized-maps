import * as maplibregl from "maplibre-gl";
import type {ErrorEvent, StyleSpecification} from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import {Protocol} from "pmtiles";
import {isRendererId, loadCatalog, type RendererId} from "./map/catalog";
import {nfsStyle} from "./renderers/nfs/style";
import {rdrStyle} from "./renderers/rdr/style";
import {eldenStyle} from "./renderers/elden/style";
import {sanAndreasStyle} from "./renderers/sanandreas/style";
import {setupNavigation} from "./navigation";
import {setupCompass, type CompassController} from "./compass";
import {setupSearch} from "./search";
import {setupLocate, type LocateController} from "./locate";
import "./styles.css";

const params = new URLSearchParams(window.location.search);
const requestedRenderer = params.get("renderer");
const rendererId: RendererId = isRendererId(requestedRenderer) ? requestedRenderer : "nfs";
const catalog = await loadCatalog();
const renderer = catalog.renderers[rendererId];
const protocol = new Protocol();
const error = document.querySelector<HTMLElement>("#error")!;
const routePanel = document.querySelector<HTMLElement>(".route-panel")!;

document.body.dataset.renderer = rendererId;
document.querySelector<HTMLElement>(".masthead h1")!.textContent = catalog.region.label;
document.querySelector<HTMLElement>("#source-label")!.textContent = `${catalog.releaseId} / ${renderer.model}`;
maplibregl.addProtocol("pmtiles", protocol.tile);

let style: StyleSpecification;
try {
  if (renderer.status === "unavailable") throw new Error(renderer.reason ?? `${renderer.label} is unavailable.`);
  style = rendererId === "sanandreas" ? sanAndreasStyle(catalog) : rendererId === "nfs" ? nfsStyle(catalog) : rendererId === "rdr" ? rdrStyle(catalog) : eldenStyle(catalog);
} catch (cause) {
  const message = cause instanceof Error ? cause.message : `${renderer.label} is unavailable.`;
  error.textContent = message;
  error.hidden = false;
  routePanel.hidden = true;
  style = {version: 8, sources: {}, layers: [{id: "unavailable", type: "background", paint: {"background-color": "#171a1c"}}]};
}

// MapLibre needs WebGL2. Phones in low-power or lockdown modes can refuse the context,
// and an unguarded constructor throw leaves a blank page with nothing to act on.
if (!document.createElement("canvas").getContext("webgl2")) {
  error.textContent = "This browser cannot start WebGL2, which the map needs. Try another browser, or turn off low-power or lockdown mode.";
  error.hidden = false;
}

const [west, south, east, north] = catalog.region.bounds;
// Sea margin around the country. Without it MapLibre refuses to zoom out past the
// point where the viewport is taller than the bounds, so the whole of India never fits.
const margin = 18;
const map = new maplibregl.Map({
  container: "map",
  style,
  center: catalog.region.center,
  zoom: catalog.region.zoom.initial,
  minZoom: catalog.region.zoom.min,
  maxZoom: catalog.region.zoom.max,
  maxBounds: [[west - margin, south - margin], [east + margin, north + margin]],
  hash: true,
  attributionControl: false,
  // The map is the whole page, so gestures belong to it. Cooperative gestures are for
  // maps embedded in scrollable documents; on a phone they swallow one-finger panning.
  cooperativeGestures: false
});
let compass: CompassController | undefined;
let locate: LocateController | undefined;

Object.assign(window, {map});

map.addControl(new maplibregl.ScaleControl({unit: "metric"}), "bottom-left");
map.addControl(new maplibregl.AttributionControl({compact: true}), "bottom-right");

document.querySelector<HTMLButtonElement>("#zoom-in")!.addEventListener("click", () => map.zoomIn());
document.querySelector<HTMLButtonElement>("#zoom-out")!.addEventListener("click", () => map.zoomOut());

const zoomLabel = document.querySelector<HTMLElement>("#zoom-label")!;
const rendererOptions = document.querySelector<HTMLElement>("#renderer-options")!;

for (const id of ["sanandreas", "nfs", "rdr", "elden"] as const) {
  const option = catalog.renderers[id];
  const label = document.createElement("label");
  const input = document.createElement("input");
  const copy = document.createElement("span");
  const name = document.createElement("strong");
  const status = document.createElement("small");

  label.className = "renderer-option";
  input.type = "radio";
  input.name = "renderer";
  input.value = id;
  input.checked = id === rendererId;
  input.disabled = option.status === "unavailable";
  name.textContent = option.label;
  status.textContent = option.status === "unavailable" ? `Unavailable · ${option.model}` : `Ready · ${option.model}`;
  copy.append(name, status);
  label.append(input, copy);
  if (option.reason) label.title = option.reason;
  rendererOptions.append(label);
}

rendererOptions.addEventListener("change", (event) => {
  const input = event.target as HTMLInputElement;
  if (!isRendererId(input.value) || catalog.renderers[input.value].status === "unavailable") return;
  params.set("renderer", input.value);
  params.delete("theme");
  params.delete("source");
  window.location.assign(`${window.location.pathname}?${params}${window.location.hash}`);
});

function showZoom() {
  zoomLabel.textContent = `z ${map.getZoom().toFixed(1)}`;
}

showZoom();
map.on("zoom", showZoom);

map.on("error", (event: ErrorEvent) => {
  const message = event.error?.message ?? "A map source failed to load.";
  error.textContent = message;
  error.hidden = false;
});

map.once("style.load", () => {
  document.body.classList.add("map-ready");
  compass = setupCompass(map, style);
  locate = setupLocate(map);
  if (renderer.status === "ready") {
    const navigation = setupNavigation(map, compass);
    setupSearch(map, navigation);
  }
});

window.addEventListener("beforeunload", () => {
  locate?.stop();
  compass?.remove();
  map.remove();
  maplibregl.removeProtocol("pmtiles");
});
