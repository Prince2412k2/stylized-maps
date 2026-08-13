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

const [west, south, east, north] = catalog.region.bounds;
const map = new maplibregl.Map({
  container: "map",
  style,
  center: catalog.region.center,
  zoom: catalog.region.zoom.initial,
  minZoom: catalog.region.zoom.min,
  maxZoom: catalog.region.zoom.max,
  maxBounds: [[west, south], [east, north]],
  hash: true,
  attributionControl: false,
  cooperativeGestures: true
});

Object.assign(window, {map});

map.addControl(new maplibregl.NavigationControl({showCompass: false}), "bottom-right");
map.addControl(new maplibregl.ScaleControl({unit: "metric"}), "bottom-left");
map.addControl(new maplibregl.AttributionControl({compact: true}), "bottom-right");

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

map.on("zoom", () => {
  zoomLabel.textContent = `z ${map.getZoom().toFixed(1)}`;
});

map.on("error", (event: ErrorEvent) => {
  const message = event.error?.message ?? "A map source failed to load.";
  error.textContent = message;
  error.hidden = false;
});

map.once("load", () => {
  document.body.classList.add("map-ready");
  if (renderer.status === "ready") setupNavigation(map);
});

window.addEventListener("beforeunload", () => {
  map.remove();
  maplibregl.removeProtocol("pmtiles");
});
