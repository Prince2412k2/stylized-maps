export type Coordinate = [number, number];

export type SearchResult = {
  id: string;
  label: string;
  context: string;
  coordinate: Coordinate;
  kind: string;
};

export type DrivingRoute = {
  distanceMeters: number;
  durationSeconds: number;
  geometry: {type: "LineString"; coordinates: Coordinate[]};
  steps: Array<{
    instruction: string;
    type: string;
    distanceMeters: number;
    fromIndex: number;
  }>;
};

export async function searchPlaces(query: string, proximity: Coordinate, signal: AbortSignal): Promise<SearchResult[]> {
  const params = new URLSearchParams({q: query, limit: "6", proximity: proximity.join(",")});
  const response = await fetch(`/api/v1/search?${params}`, {signal});
  if (!response.ok) throw new Error(await responseMessage(response, "Search is temporarily unavailable."));
  return (await response.json() as {results: SearchResult[]}).results;
}

export async function fetchDrivingRoute(start: Coordinate, end: Coordinate, signal: AbortSignal): Promise<DrivingRoute> {
  const params = new URLSearchParams({start: start.join(","), end: end.join(",")});
  const response = await fetch(`/api/v1/routes/driving?${params}`, {signal});
  if (!response.ok) throw new Error(await responseMessage(response, "No driving route was found."));
  return (await response.json() as {route: DrivingRoute}).route;
}

async function responseMessage(response: Response, fallback: string): Promise<string> {
  const body = await response.json().catch(() => undefined) as {error?: {message?: string}} | undefined;
  return body?.error?.message ?? fallback;
}
