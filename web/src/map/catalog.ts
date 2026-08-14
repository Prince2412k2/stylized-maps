export type RendererId = "sanandreas" | "nfs" | "rdr" | "elden";

type RendererStatus = {
  label: string;
  status: "ready" | "preview" | "unavailable";
  model: "vector-first" | "vector-plus-terrain" | "raster-hybrid";
  reason?: string;
};

export type ReleaseCatalog = {
  releaseId: string;
  region: {
    id: string;
    label: string;
    bounds: [number, number, number, number];
    center: [number, number];
    zoom: {initial: number; min: number; max: number};
  };
  products: {
    coreVector: string;
    rdrTerrain?: string;
    eldenBase?: string;
  };
  renderers: Record<RendererId, RendererStatus>;
};

export async function loadCatalog() {
  const response = await fetch("/maps/current.json", {cache: "no-store"});
  if (!response.ok) throw new Error(`Release catalog returned HTTP ${response.status}.`);
  return await response.json() as ReleaseCatalog;
}

export function isRendererId(value: string | null): value is RendererId {
  return value === "sanandreas" || value === "nfs" || value === "rdr" || value === "elden";
}
