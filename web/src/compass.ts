import * as maplibregl from "maplibre-gl";
import type {StyleSpecification} from "maplibre-gl";
import type {Coordinate} from "./api";

export type CompassController = {
  map: maplibregl.Map;
  follow(position: Coordinate, heading: number): void;
  release(): void;
  remove(): void;
};

export function setupCompass(mainMap: maplibregl.Map, style: StyleSpecification): CompassController {
  const arrow = document.querySelector<HTMLElement>("#direction-arrow")!;
  const compass = new maplibregl.Map({
    container: "compass-map",
    style: structuredClone(style),
    center: mainMap.getCenter(),
    zoom: Math.max(mainMap.getZoom() - 2, 4),
    interactive: false,
    attributionControl: false,
    fadeDuration: 0
  });
  let isFollowing = false;

  const mirrorMainMap = () => {
    if (isFollowing || !compass.loaded()) return;
    compass.jumpTo({center: mainMap.getCenter(), zoom: Math.max(mainMap.getZoom() - 2, 4), bearing: 0, pitch: 0});
    arrow.style.setProperty("--heading", `${mainMap.getBearing()}deg`);
  };
  mainMap.on("move", mirrorMainMap);

  return {
    map: compass,
    follow(position, heading) {
      isFollowing = true;
      arrow.style.setProperty("--heading", "0deg");
      compass.easeTo({center: position, bearing: heading, zoom: 15, duration: 500});
    },
    release() {
      isFollowing = false;
      mirrorMainMap();
    },
    remove() {
      mainMap.off("move", mirrorMainMap);
      compass.remove();
    }
  };
}
