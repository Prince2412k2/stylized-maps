import * as maplibregl from "maplibre-gl";

type State = "idle" | "locating" | "tracking" | "following";

const ACCURACY_SOURCE = "gps-accuracy";
const EARTH_RADIUS = 6378137;

export type LocateController = {
  stop: () => void;
};

/**
 * Live position tracking. The button cycles idle → tracking → following (camera
 * locks to heading) → idle. Geolocation needs a secure context, which is the most
 * common reason this fails on a LAN address, so that case gets its own message.
 */
export function setupLocate(map: maplibregl.Map): LocateController {
  const button = document.querySelector<HTMLButtonElement>("#locate")!;
  const readout = document.querySelector<HTMLElement>("#gps-readout")!;
  let state: State = "idle";
  let watch: number | undefined;
  let marker: maplibregl.Marker | undefined;
  let centred = false;

  const setState = (next: State) => {
    state = next;
    button.dataset.state = next;
    button.setAttribute("aria-pressed", next === "idle" ? "false" : "true");
    button.setAttribute("aria-label", next === "following" ? "Stop following my location" : "Show my location");
  };

  const say = (message: string | undefined) => {
    readout.textContent = message ?? "";
    readout.hidden = !message;
  };

  const accuracyRing = (centre: [number, number], radius: number) => {
    const points: [number, number][] = [];
    const latitude = (centre[1] * Math.PI) / 180;
    for (let step = 0; step <= 64; step += 1) {
      const angle = (step / 64) * Math.PI * 2;
      const dx = (radius * Math.cos(angle)) / (EARTH_RADIUS * Math.cos(latitude)) * (180 / Math.PI);
      const dy = (radius * Math.sin(angle)) / EARTH_RADIUS * (180 / Math.PI);
      points.push([centre[0] + dx, centre[1] + dy]);
    }
    return {type: "Feature" as const, properties: {}, geometry: {type: "Polygon" as const, coordinates: [points]}};
  };

  const ensureAccuracyLayers = () => {
    if (map.getSource(ACCURACY_SOURCE)) return;
    map.addSource(ACCURACY_SOURCE, {type: "geojson", data: {type: "FeatureCollection", features: []}});
    map.addLayer({
      id: "gps-accuracy-fill",
      type: "fill",
      source: ACCURACY_SOURCE,
      paint: {"fill-color": "#4a9eff", "fill-opacity": 0.12}
    });
    map.addLayer({
      id: "gps-accuracy-edge",
      type: "line",
      source: ACCURACY_SOURCE,
      paint: {"line-color": "#4a9eff", "line-opacity": 0.4, "line-width": 1}
    });
  };

  const ensureMarker = () => {
    if (marker) return marker;
    const element = document.createElement("div");
    element.className = "gps-marker";
    element.innerHTML = '<span class="gps-heading"></span><span class="gps-dot"></span>';
    marker = new maplibregl.Marker({element, pitchAlignment: "map", rotationAlignment: "map"});
    return marker;
  };

  const onPosition = (position: GeolocationPosition) => {
    const {longitude, latitude, accuracy, heading, speed} = position.coords;
    const centre: [number, number] = [longitude, latitude];

    ensureAccuracyLayers();
    const source = map.getSource(ACCURACY_SOURCE) as maplibregl.GeoJSONSource | undefined;
    source?.setData({type: "FeatureCollection", features: [accuracyRing(centre, Math.max(accuracy, 5))]});

    const active = ensureMarker();
    active.setLngLat(centre).addTo(map);
    const element = active.getElement();
    element.classList.toggle("has-heading", typeof heading === "number" && !Number.isNaN(heading));
    if (typeof heading === "number" && !Number.isNaN(heading)) {
      element.style.setProperty("--heading", `${heading}deg`);
    }

    if (state === "locating") setState("tracking");

    if (!centred) {
      centred = true;
      map.easeTo({center: centre, zoom: Math.max(map.getZoom(), 15), duration: 900});
    } else if (state === "following") {
      map.easeTo({
        center: centre,
        bearing: typeof heading === "number" && !Number.isNaN(heading) ? heading : map.getBearing(),
        pitch: 50,
        duration: 700
      });
    }

    const parts = [`±${Math.round(accuracy)} m`];
    if (typeof speed === "number" && !Number.isNaN(speed)) parts.push(`${Math.round(speed * 3.6)} km/h`);
    parts.push(`${latitude.toFixed(5)}, ${longitude.toFixed(5)}`);
    say(parts.join("  ·  "));
  };

  const onError = (cause: GeolocationPositionError) => {
    stop();
    if (cause.code === cause.PERMISSION_DENIED) {
      say("Location permission denied. Allow it in your browser settings.");
      return;
    }
    if (cause.code === cause.POSITION_UNAVAILABLE) {
      say("No position fix. Try again outdoors.");
      return;
    }
    say("Locating timed out.");
  };

  const start = () => {
    if (!window.isSecureContext) {
      say("Location needs HTTPS. Open this over https or on localhost.");
      return;
    }
    if (!("geolocation" in navigator)) {
      say("This browser has no location support.");
      return;
    }
    setState("locating");
    say("Acquiring signal…");
    centred = false;
    watch = navigator.geolocation.watchPosition(onPosition, onError, {
      enableHighAccuracy: true,
      maximumAge: 2000,
      timeout: 20000
    });
  };

  const stop = () => {
    if (watch !== undefined) navigator.geolocation.clearWatch(watch);
    watch = undefined;
    marker?.remove();
    const source = map.getSource(ACCURACY_SOURCE) as maplibregl.GeoJSONSource | undefined;
    source?.setData({type: "FeatureCollection", features: []});
    setState("idle");
    say(undefined);
  };

  button.addEventListener("click", () => {
    if (state === "idle") return start();
    if (state === "locating" || state === "tracking") {
      setState("following");
      return;
    }
    map.easeTo({pitch: 0, bearing: 0, duration: 500});
    stop();
  });

  // Dragging the map by hand means the user wants to look elsewhere; drop the camera lock
  // but keep the fix live.
  map.on("dragstart", () => {
    if (state === "following") setState("tracking");
  });

  return {stop};
}
