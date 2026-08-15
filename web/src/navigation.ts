import * as maplibregl from "maplibre-gl";
import type {GeoJSONSource, MapGeoJSONFeature} from "maplibre-gl";
import {fetchDrivingRoute, type Coordinate, type DrivingRoute} from "./api";
import type {CompassController} from "./compass";

export type NavigationController = {
  setStart(point: Coordinate): void;
  setDestination(point: Coordinate): void;
  reset(): void;
};

export function setupNavigation(map: maplibregl.Map, compass: CompassController): NavigationController {
  const startButton = document.querySelector<HTMLButtonElement>("#route-start")!;
  const driveButton = document.querySelector<HTMLButtonElement>("#route-drive")!;
  const resetButton = document.querySelector<HTMLButtonElement>("#route-reset")!;
  const status = document.querySelector<HTMLElement>("#route-status")!;
  const summary = document.querySelector<HTMLElement>("#route-summary")!;
  const time = document.querySelector<HTMLElement>("#route-time")!;
  const distance = document.querySelector<HTMLElement>("#route-distance")!;
  const steps = document.querySelector<HTMLOListElement>("#route-steps")!;
  const markers: maplibregl.Marker[] = [];
  let isPlacing = false;
  let points: Coordinate[] = [];
  let pendingDestination: Coordinate | undefined;
  let routeRequest: AbortController | undefined;
  let route: DrivingRoute | undefined;
  let watchId: number | undefined;
  let positionMarker: maplibregl.Marker | undefined;
  let previousPosition: Coordinate | undefined;
  let lastReroute = 0;

  const routeMaps = [map, compass.map];
  const setRouteGeometry = (geometry: GeoJSON.Geometry) => {
    for (const routeMap of routeMaps) {
      const update = () => (routeMap.getSource("route") as GeoJSONSource | undefined)?.setData({type: "Feature", properties: {}, geometry});
      routeMap.loaded() ? update() : routeMap.once("load", update);
    }
  };

  function setPoint(point: Coordinate, index: number): void {
    points[index] = point;
    markers[index]?.remove();
    markers[index] = new maplibregl.Marker({className: index === 0 ? "route-marker start" : "route-marker destination"}).setLngLat(point).addTo(map);
    syncRouteParams();
    if (points.length === 1) {
      status.textContent = "Now place the destination.";
      startButton.textContent = "Cancel";
      return;
    }
    isPlacing = false;
    map.getCanvas().classList.remove("placing-route");
    void requestRoute(points[0], points[1]);
  }

  async function requestRoute(start: Coordinate, destination: Coordinate, isReroute = false): Promise<void> {
    routeRequest?.abort();
    const request = new AbortController();
    routeRequest = request;
    startButton.textContent = "Calculating...";
    startButton.disabled = true;
    try {
      route = await fetchDrivingRoute(start, destination, request.signal);
      setRouteGeometry(route.geometry);
      renderRoute(route);
      if (!isReroute) fitRoute(route.geometry.coordinates);
    } catch (cause) {
      if (cause instanceof DOMException && cause.name === "AbortError") return;
      showRouteError(cause instanceof Error ? cause.message : "Routing service could not be reached.");
    } finally {
      if (routeRequest === request) routeRequest = undefined;
    }
  }

  function renderRoute(nextRoute: DrivingRoute): void {
    time.textContent = formatDuration(nextRoute.durationSeconds);
    distance.textContent = formatDistance(nextRoute.distanceMeters);
    steps.replaceChildren(...nextRoute.steps.slice(0, 8).map((step) => {
      const item = document.createElement("li");
      const instruction = document.createElement("span");
      const stepDistance = document.createElement("small");
      instruction.textContent = step.instruction;
      stepDistance.textContent = formatDistance(step.distanceMeters);
      item.append(instruction, stepDistance);
      return item;
    }));
    status.textContent = watchId === undefined ? "Driving route" : "Drive mode active";
    summary.hidden = false;
    resetButton.hidden = false;
    startButton.hidden = true;
    driveButton.hidden = false;
  }

  function fitRoute(coordinates: Coordinate[]): void {
    const bounds = coordinates.reduce((box, coordinate) => box.extend(coordinate), new maplibregl.LngLatBounds(coordinates[0], coordinates[0]));
    const isMobile = window.matchMedia("(max-width: 640px)").matches;
    map.fitBounds(bounds, {padding: isMobile ? {top: 180, right: 24, bottom: 160, left: 24} : {top: 90, right: 210, bottom: 120, left: 360}, maxZoom: 14});
  }

  function showRouteError(message: string): void {
    status.textContent = message;
    startButton.textContent = "Try again";
    startButton.hidden = false;
    startButton.disabled = false;
    resetButton.hidden = false;
    driveButton.hidden = true;
  }

  function resetRoute(): void {
    stopDrive();
    routeRequest?.abort();
    routeRequest = undefined;
    isPlacing = false;
    points = [];
    route = undefined;
    pendingDestination = undefined;
    markers.splice(0).forEach((marker) => marker.remove());
    setRouteGeometry({type: "LineString", coordinates: []});
    map.getCanvas().classList.remove("placing-route");
    status.textContent = "Choose two points on the map.";
    startButton.textContent = "Place start";
    startButton.hidden = false;
    startButton.disabled = false;
    resetButton.hidden = true;
    driveButton.hidden = true;
    summary.hidden = true;
    syncRouteParams();
  }

  function startDrive(): void {
    if (!route || !points[1]) return;
    if (!navigator.geolocation) {
      showRouteError("This browser does not provide GPS location.");
      return;
    }
    document.body.classList.add("drive-mode");
    driveButton.textContent = "Stop drive";
    status.textContent = "Waiting for GPS...";
    watchId = navigator.geolocation.watchPosition(updateDrivePosition, (error) => {
      status.textContent = error.code === error.PERMISSION_DENIED ? "Location permission is required for drive mode." : "GPS position is unavailable.";
      stopDrive();
    }, {enableHighAccuracy: true, maximumAge: 1_000, timeout: 15_000});
  }

  function stopDrive(): void {
    if (watchId !== undefined) navigator.geolocation.clearWatch(watchId);
    watchId = undefined;
    previousPosition = undefined;
    positionMarker?.remove();
    positionMarker = undefined;
    document.body.classList.remove("drive-mode");
    driveButton.textContent = "Start drive";
    compass.release();
    map.easeTo({pitch: 0, bearing: 0, duration: 500});
  }

  function updateDrivePosition(position: GeolocationPosition): void {
    if (!route || !points[1]) return;
    const current: Coordinate = [position.coords.longitude, position.coords.latitude];
    const gpsHeading = position.coords.heading;
    const heading = gpsHeading !== null && Number.isFinite(gpsHeading) ? gpsHeading : previousPosition ? bearing(previousPosition, current) : map.getBearing();
    previousPosition = current;
    positionMarker?.remove();
    const marker = document.createElement("div");
    marker.className = "drive-marker";
    marker.style.setProperty("--heading", `${heading}deg`);
    positionMarker = new maplibregl.Marker({element: marker, rotationAlignment: "map"}).setLngLat(current).addTo(map);
    map.easeTo({center: current, bearing: heading, pitch: 55, zoom: Math.max(map.getZoom(), 16), offset: [0, map.getContainer().clientHeight * 0.18], duration: 700});
    compass.follow(current, heading);

    const nearest = nearestRoutePoint(current, route.geometry.coordinates);
    const remaining = route.geometry.coordinates.slice(nearest.index);
    setRouteGeometry({type: "LineString", coordinates: [current, ...remaining]});
    const instruction = [...route.steps].reverse().find((step) => step.fromIndex <= nearest.index) ?? route.steps[0];
    status.textContent = instruction?.instruction ?? "Continue on the route";
    if (distanceBetween(current, points[1]) < 35) {
      status.textContent = "You have arrived.";
      stopDrive();
      return;
    }
    if (nearest.distance > 120 && Date.now() - lastReroute > 30_000) {
      lastReroute = Date.now();
      void requestRoute(current, points[1], true);
    }
  }

  function syncRouteParams(): void {
    const url = new URL(window.location.href);
    points[0] ? url.searchParams.set("from", points[0].join(",")) : url.searchParams.delete("from");
    points[1] ? url.searchParams.set("to", points[1].join(",")) : url.searchParams.delete("to");
    window.history.replaceState(null, "", url);
  }

  startButton.addEventListener("click", () => {
    if (isPlacing) return resetRoute();
    resetRoute();
    isPlacing = true;
    status.textContent = "Click the map to place the start.";
    startButton.textContent = "Cancel";
    map.getCanvas().classList.add("placing-route");
  });
  resetButton.addEventListener("click", resetRoute);
  driveButton.addEventListener("click", () => watchId === undefined ? startDrive() : stopDrive());
  map.on("click", (event) => {
    if (!isPlacing) return;
    setPoint([event.lngLat.lng, event.lngLat.lat], points.length);
    if (pendingDestination) {
      const destination = pendingDestination;
      pendingDestination = undefined;
      setPoint(destination, 1);
    }
  });

  const controller: NavigationController = {
    setStart(point) { resetRoute(); isPlacing = true; setPoint(point, 0); },
    setDestination(point) {
      if (points.length === 1) return setPoint(point, 1);
      resetRoute();
      isPlacing = true;
      pendingDestination = point;
      status.textContent = "Place your starting point.";
      map.getCanvas().classList.add("placing-route");
    },
    reset: resetRoute
  };

  setupPoiInteractions(map, controller);
  const params = new URLSearchParams(window.location.search);
  const sharedStart = parseCoordinate(params.get("from"));
  const sharedEnd = parseCoordinate(params.get("to"));
  if (sharedStart && sharedEnd) {
    setPoint(sharedStart, 0);
    setPoint(sharedEnd, 1);
  }
  return controller;
}

function setupPoiInteractions(map: maplibregl.Map, navigation: NavigationController): void {
  const poiLayers = ["poi-labels", "poi-fuel", "poi-shops", "poi-other"];
  map.on("mouseenter", poiLayers, () => { map.getCanvas().style.cursor = "pointer"; });
  map.on("mouseleave", poiLayers, () => { map.getCanvas().style.cursor = ""; });
  map.on("click", poiLayers, (event) => {
    const feature = event.features?.[0];
    if (!feature || feature.geometry.type !== "Point") return;
    showPoi(map, navigation, feature, feature.geometry.coordinates as Coordinate);
  });
}

function showPoi(map: maplibregl.Map, navigation: NavigationController, feature: MapGeoJSONFeature, coordinate: Coordinate): void {
  const properties = feature.properties;
  const card = document.createElement("div");
  const category = document.createElement("small");
  const heading = document.createElement("strong");
  const actions = document.createElement("div");
  const from = document.createElement("button");
  const to = document.createElement("button");
  card.className = "poi-card";
  category.textContent = formatPoiType(properties.subcategory || properties.category);
  heading.textContent = properties["name:en"] || properties.name || properties.brand || category.textContent;
  from.textContent = "Start here";
  to.textContent = "Go here";
  actions.append(from, to);
  card.append(category, heading, actions);
  const popup = new maplibregl.Popup({offset: 12}).setLngLat(coordinate).setDOMContent(card).addTo(map);
  from.addEventListener("click", () => { navigation.setStart(coordinate); popup.remove(); });
  to.addEventListener("click", () => { navigation.setDestination(coordinate); popup.remove(); });
}

function nearestRoutePoint(point: Coordinate, route: Coordinate[]): {index: number; distance: number} {
  let nearest = {index: 0, distance: Number.POSITIVE_INFINITY};
  route.forEach((candidate, index) => {
    const distance = distanceBetween(point, candidate);
    if (distance < nearest.distance) nearest = {index, distance};
  });
  return nearest;
}

function distanceBetween(a: Coordinate, b: Coordinate): number {
  const latitude = (a[1] + b[1]) * Math.PI / 360;
  const x = (b[0] - a[0]) * Math.cos(latitude);
  const y = b[1] - a[1];
  return Math.hypot(x, y) * 111_320;
}

function bearing(a: Coordinate, b: Coordinate): number {
  const startLat = a[1] * Math.PI / 180;
  const endLat = b[1] * Math.PI / 180;
  const delta = (b[0] - a[0]) * Math.PI / 180;
  const y = Math.sin(delta) * Math.cos(endLat);
  const x = Math.cos(startLat) * Math.sin(endLat) - Math.sin(startLat) * Math.cos(endLat) * Math.cos(delta);
  return (Math.atan2(y, x) * 180 / Math.PI + 360) % 360;
}

function parseCoordinate(value: string | null): Coordinate | undefined {
  if (!value) return;
  const coordinate = value.split(",").map(Number);
  return coordinate.length === 2 && coordinate.every(Number.isFinite) ? coordinate as Coordinate : undefined;
}

function formatPoiType(value: string | undefined): string {
  return value ? value.replaceAll("_", " ").replace(/^./, (letter) => letter.toUpperCase()) : "Place";
}

function formatDistance(meters: number): string {
  return meters < 1000 ? `${Math.round(meters)} m` : `${(meters / 1000).toFixed(1)} km`;
}

function formatDuration(seconds: number): string {
  const minutes = Math.round(seconds / 60);
  return minutes < 60 ? `${minutes} min` : `${Math.floor(minutes / 60)} hr ${minutes % 60} min`;
}
