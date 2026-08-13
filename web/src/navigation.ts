import * as maplibregl from "maplibre-gl";
import type {GeoJSONSource, MapGeoJSONFeature} from "maplibre-gl";

type Coordinate = [number, number];

type RouteStep = {
  distance: number;
  name: string;
  maneuver: {
    type: string;
    modifier?: string;
    exit?: number;
  };
};

type RouteResponse = {
  code: string;
  message?: string;
  routes: Array<{
    distance: number;
    duration: number;
    geometry: GeoJSON.LineString;
    legs: Array<{steps: RouteStep[]}>;
  }>;
};

export function setupNavigation(map: maplibregl.Map) {
  const startButton = document.querySelector<HTMLButtonElement>("#route-start")!;
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

  function setPoint(point: Coordinate, index: number) {
    points[index] = point;
    markers[index]?.remove();
    markers[index] = new maplibregl.Marker({className: index === 0 ? "route-marker start" : "route-marker destination"})
      .setLngLat(point)
      .addTo(map);

    if (points.length === 1) {
      status.textContent = "Now place the destination.";
      startButton.textContent = "Cancel";
      return;
    }

    isPlacing = false;
    map.getCanvas().classList.remove("placing-route");
    startButton.textContent = "Calculating...";
    startButton.disabled = true;
    void requestRoute(points[0], points[1]);
  }

  async function requestRoute(start: Coordinate, destination: Coordinate) {
    const coordinates = `${start.join(",")};${destination.join(",")}`;
    const request = new AbortController();
    routeRequest = request;

    try {
      const response = await fetch(`https://router.project-osrm.org/route/v1/driving/${coordinates}?overview=full&geometries=geojson&steps=true`, {signal: request.signal});
      if (!response.ok) {
        showRouteError(`Routing service returned HTTP ${response.status}.`);
        return;
      }

      const routeResponse = await response.json() as RouteResponse;
      const route = routeResponse.routes[0];
      if (routeResponse.code !== "Ok" || !route) {
        showRouteError(routeResponse.message ?? "No driving route was found.");
        return;
      }

      (map.getSource("route") as GeoJSONSource).setData({
        type: "Feature",
        properties: {},
        geometry: route.geometry
      });

      time.textContent = formatDuration(route.duration);
      distance.textContent = formatDistance(route.distance);
      steps.replaceChildren(...route.legs.flatMap((leg) => leg.steps.slice(0, 8).map((step) => {
        const item = document.createElement("li");
        const instruction = document.createElement("span");
        const stepDistance = document.createElement("small");
        instruction.textContent = formatInstruction(step);
        stepDistance.textContent = formatDistance(step.distance);
        item.append(instruction, stepDistance);
        return item;
      })));
      status.textContent = "Driving route";
      summary.hidden = false;
      resetButton.hidden = false;
      startButton.hidden = true;

      const bounds = route.geometry.coordinates.reduce(
        (routeBounds, coordinate) => routeBounds.extend(coordinate as Coordinate),
        new maplibregl.LngLatBounds(route.geometry.coordinates[0] as Coordinate, route.geometry.coordinates[0] as Coordinate)
      );
      const isMobile = window.matchMedia("(max-width: 640px)").matches;
      map.fitBounds(bounds, {padding: isMobile ? 40 : {top: 90, right: 90, bottom: 120, left: 360}, maxZoom: 14});
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") return;
      showRouteError("The routing service could not be reached.");
    } finally {
      if (routeRequest === request) routeRequest = undefined;
    }
  }

  function showRouteError(message: string) {
    status.textContent = message;
    startButton.textContent = "Try again";
    startButton.disabled = false;
    resetButton.hidden = false;
  }

  function resetRoute() {
    routeRequest?.abort();
    routeRequest = undefined;
    isPlacing = false;
    points = [];
    pendingDestination = undefined;
    markers.splice(0).forEach((marker) => marker.remove());
    (map.getSource("route") as GeoJSONSource).setData({type: "FeatureCollection", features: []});
    map.getCanvas().classList.remove("placing-route");
    status.textContent = "Choose two points on the map.";
    startButton.textContent = "Place start";
    startButton.hidden = false;
    startButton.disabled = false;
    resetButton.hidden = true;
    summary.hidden = true;
  }

  startButton.addEventListener("click", () => {
    if (isPlacing) {
      resetRoute();
      return;
    }
    resetRoute();
    isPlacing = true;
    status.textContent = "Click the map to place the start.";
    startButton.textContent = "Cancel";
    map.getCanvas().classList.add("placing-route");
  });

  resetButton.addEventListener("click", resetRoute);

  map.on("click", (event) => {
    if (!isPlacing) return;
    setPoint([event.lngLat.lng, event.lngLat.lat], points.length);
    if (pendingDestination) {
      const destination = pendingDestination;
      pendingDestination = undefined;
      setPoint(destination, 1);
    }
  });

  const poiLayers = ["poi-labels", "poi-fuel", "poi-shops", "poi-other"];
  map.on("mouseenter", poiLayers, () => { map.getCanvas().style.cursor = "pointer"; });
  map.on("mouseleave", poiLayers, () => { map.getCanvas().style.cursor = ""; });
  map.on("click", poiLayers, (event) => {
    if (isPlacing) return;
    const feature = event.features?.[0];
    if (!feature || feature.geometry.type !== "Point") return;
    showPoi(feature, feature.geometry.coordinates as Coordinate);
  });

  function showPoi(feature: MapGeoJSONFeature, coordinate: Coordinate) {
    const properties = feature.properties;
    const card = document.createElement("div");
    const title = properties["name:en"] || properties.name || properties.brand || formatPoiType(properties.subcategory || properties.category);
    card.className = "poi-card";
    const category = document.createElement("small");
    const heading = document.createElement("strong");
    category.textContent = formatPoiType(properties.subcategory || properties.category);
    heading.textContent = title;
    card.append(category, heading);
    if (properties.opening_hours) {
      const openingHours = document.createElement("span");
      openingHours.textContent = properties.opening_hours;
      card.append(openingHours);
    }
    const details = [properties.operator && `Operated by ${properties.operator}`, properties.phone, properties.wheelchair === "yes" && "Wheelchair accessible"].filter(Boolean);
    if (details.length) {
      const metadata = document.createElement("small");
      metadata.className = "poi-meta";
      metadata.textContent = details.join(" · ");
      card.append(metadata);
    }
    const actions = document.createElement("div");
    const from = document.createElement("button");
    const to = document.createElement("button");
    from.textContent = "Start here";
    to.textContent = "Go here";
    actions.append(from, to);
    card.append(actions);

    const popup = new maplibregl.Popup({offset: 12, closeButton: true}).setLngLat(coordinate).setDOMContent(card).addTo(map);
    from.addEventListener("click", () => {
      resetRoute();
      isPlacing = true;
      setPoint(coordinate, 0);
      popup.remove();
    });
    to.addEventListener("click", () => {
      if (points.length !== 1) {
        resetRoute();
        isPlacing = true;
        pendingDestination = coordinate;
        status.textContent = "Place your starting point.";
        map.getCanvas().classList.add("placing-route");
        return;
      }
      setPoint(coordinate, 1);
      popup.remove();
    });
  }
}

function formatInstruction(step: RouteStep) {
  const {type, modifier, exit} = step.maneuver;
  const road = step.name ? ` onto ${step.name}` : "";

  if (type === "depart") return `Start${road}`;
  if (type === "arrive") return "Arrive at your destination";
  if (type === "turn") return `Turn${modifier ? ` ${modifier}` : ""}${road}`;
  if (type === "fork") return `Keep${modifier ? ` ${modifier}` : ""}${road}`;
  if (type === "merge") return `Merge${modifier ? ` ${modifier}` : ""}${road}`;
  if (type === "roundabout" || type === "rotary") return exit ? `Take exit ${exit}${road}` : `Enter the roundabout${road}`;
  if (type === "new name" || type === "continue") return `Continue${modifier ? ` ${modifier}` : ""}${road}`;

  const action = type.replaceAll("_", " ");
  return `${action.charAt(0).toUpperCase()}${action.slice(1)}${modifier ? ` ${modifier}` : ""}${road}`;
}

function formatPoiType(value: string | undefined) {
  if (!value) return "Place";
  const labels: Record<string, string> = {
    fuel: "Petrol pump",
    charging_station: "Charging station",
    bus_station: "Bus station",
    ferry_terminal: "Ferry terminal",
    place_of_worship: "Place of worship",
    fast_food: "Fast food",
    sports_centre: "Sports centre",
    fire_station: "Fire station",
    post_office: "Post office",
    railway: "Rail station",
    station: "Rail station",
    halt: "Rail stop"
  };
  return labels[value] ?? value.replaceAll("_", " ").replace(/^./, (letter) => letter.toUpperCase());
}

function formatDistance(meters: number) {
  return meters < 1000 ? `${Math.round(meters)} m` : `${(meters / 1000).toFixed(1)} km`;
}

function formatDuration(seconds: number) {
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min`;
  return `${Math.floor(minutes / 60)} hr ${minutes % 60} min`;
}
