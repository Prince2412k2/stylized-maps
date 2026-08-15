import * as maplibregl from "maplibre-gl";
import {searchPlaces, type SearchResult} from "./api";
import type {NavigationController} from "./navigation";

export function setupSearch(map: maplibregl.Map, navigation: NavigationController): void {
  const input = document.querySelector<HTMLInputElement>("#place-search")!;
  const clear = document.querySelector<HTMLButtonElement>("#search-clear")!;
  const status = document.querySelector<HTMLElement>("#search-status")!;
  const list = document.querySelector<HTMLUListElement>("#search-results")!;
  let request: AbortController | undefined;
  let timer: number | undefined;
  let marker: maplibregl.Marker | undefined;

  const close = () => {
    list.replaceChildren();
    list.hidden = true;
    input.setAttribute("aria-expanded", "false");
  };

  const select = (place: SearchResult) => {
    input.value = place.label;
    close();
    marker?.remove();
    marker = new maplibregl.Marker({className: "search-marker"}).setLngLat(place.coordinate).addTo(map);
    map.flyTo({center: place.coordinate, zoom: Math.max(map.getZoom(), 13), essential: true});

    const card = document.createElement("div");
    const kind = document.createElement("small");
    const heading = document.createElement("strong");
    const context = document.createElement("span");
    const actions = document.createElement("div");
    const from = document.createElement("button");
    const to = document.createElement("button");
    card.className = "poi-card";
    kind.textContent = place.kind;
    heading.textContent = place.label;
    context.textContent = place.context;
    from.textContent = "Start here";
    to.textContent = "Go here";
    actions.append(from, to);
    card.append(kind, heading, context, actions);
    const popup = new maplibregl.Popup({offset: 14}).setLngLat(place.coordinate).setDOMContent(card).addTo(map);
    from.addEventListener("click", () => { navigation.setStart(place.coordinate); popup.remove(); });
    to.addEventListener("click", () => { navigation.setDestination(place.coordinate); popup.remove(); });
  };

  const render = (results: SearchResult[]) => {
    list.replaceChildren(...results.map((place) => {
      const item = document.createElement("li");
      const button = document.createElement("button");
      const label = document.createElement("strong");
      const context = document.createElement("span");
      button.type = "button";
      button.setAttribute("role", "option");
      label.textContent = place.label;
      context.textContent = place.context;
      button.append(label, context);
      button.addEventListener("click", () => select(place));
      item.append(button);
      return item;
    }));
    list.hidden = results.length === 0;
    input.setAttribute("aria-expanded", String(results.length > 0));
    status.textContent = results.length ? `${results.length} places` : "No places found";
  };

  input.addEventListener("input", () => {
    window.clearTimeout(timer);
    request?.abort();
    const query = input.value.trim();
    clear.hidden = query.length === 0;
    if (query.length < 2) {
      status.textContent = "Search cities, streets, and landmarks";
      close();
      return;
    }
    timer = window.setTimeout(async () => {
      request = new AbortController();
      status.textContent = "Searching India...";
      try {
        render(await searchPlaces(query, [map.getCenter().lng, map.getCenter().lat], request.signal));
      } catch (cause) {
        if (cause instanceof DOMException && cause.name === "AbortError") return;
        status.textContent = cause instanceof Error ? cause.message : "Search is unavailable";
        close();
      }
    }, 280);
  });

  input.addEventListener("keydown", (event) => {
    if (event.key === "Escape") close();
    if (event.key === "ArrowDown") {
      event.preventDefault();
      list.querySelector<HTMLButtonElement>("button")?.focus();
    }
  });
  list.addEventListener("keydown", (event) => {
    if (!(event.target instanceof HTMLButtonElement)) return;
    const buttons = [...list.querySelectorAll<HTMLButtonElement>("button")];
    const index = buttons.indexOf(event.target);
    if (event.key === "ArrowDown") buttons[Math.min(index + 1, buttons.length - 1)]?.focus();
    if (event.key === "ArrowUp") index === 0 ? input.focus() : buttons[index - 1]?.focus();
  });
  clear.addEventListener("click", () => {
    input.value = "";
    clear.hidden = true;
    marker?.remove();
    status.textContent = "Search cities, streets, and landmarks";
    close();
    input.focus();
  });
}
