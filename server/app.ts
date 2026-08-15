import rateLimit from "@fastify/rate-limit";
import Fastify, {type FastifyInstance, type FastifyReply, type FastifyRequest, type FastifyServerOptions} from "fastify";

type Dependencies = {
  apiKey: string;
  fetch: typeof globalThis.fetch;
  logger?: FastifyServerOptions["logger"];
};

type Coordinate = [number, number];
type JsonObject = Record<string, unknown>;

const INDIA_BOUNDS = {west: 68, south: 6, east: 98, north: 38};

export async function buildApp(dependencies: Dependencies): Promise<FastifyInstance> {
  const app = Fastify({logger: dependencies.logger ?? false, trustProxy: true});
  await app.register(rateLimit, {
    global: false,
    errorResponseBuilder: (request) => ({error: {code: "rate_limited", message: "Too many requests", requestId: request.id}})
  });
  app.setNotFoundHandler((request, reply) => reply.code(404).send({error: {code: "not_found", message: "Endpoint not found", requestId: request.id}}));
  app.setErrorHandler((cause, request, reply) => {
    request.log.error(cause);
    const statusCode = isObject(cause) && typeof cause.statusCode === "number" && cause.statusCode < 500 ? cause.statusCode : 500;
    reply.code(statusCode).send({
      error: {code: "internal_error", message: "The request could not be completed", requestId: request.id}
    });
  });

  app.get("/api/v1/health", async () => ({status: "ok"}));

  app.get("/api/v1/search", {
    config: {rateLimit: {max: 60, timeWindow: "1 minute"}}
  }, async (request, reply) => {
    if (!dependencies.apiKey) return serviceUnavailable(reply, request.id);
    const query = request.query as Record<string, string | undefined>;
    const text = query.q?.trim();
    if (!text || text.length < 2 || text.length > 120) {
      return invalid(reply, request.id, "q must contain between 2 and 120 characters");
    }
    const limit = query.limit ? Number(query.limit) : 6;
    if (!Number.isInteger(limit) || limit < 1 || limit > 8) {
      return invalid(reply, request.id, "limit must be an integer between 1 and 8");
    }

    const url = new URL("https://api.openrouteservice.org/geocode/autocomplete");
    url.searchParams.set("text", text);
    url.searchParams.set("boundary.country", "IND");
    url.searchParams.set("size", String(limit));
    url.searchParams.set("api_key", dependencies.apiKey);
    const proximity = parseCoordinate(query.proximity);
    if (query.proximity && !proximity) return invalid(reply, request.id, "proximity must be lng,lat within India");
    if (proximity) {
      url.searchParams.set("focus.point.lon", String(proximity[0]));
      url.searchParams.set("focus.point.lat", String(proximity[1]));
    }

    const provider = await providerJson(dependencies.fetch, url, 8_000, reply, request.id);
    if (!provider) return;
    const results = Array.isArray(provider.features) ? provider.features : [];
    return {
      results: results.flatMap((entry): Array<Record<string, unknown>> => {
        if (!isObject(entry) || !isObject(entry.properties) || !isObject(entry.geometry) || !Array.isArray(entry.geometry.coordinates)) return [];
        const properties = entry.properties;
        const location = coordinates([entry.geometry.coordinates])[0];
        if (!location || typeof properties.label !== "string") return [];
        const label = typeof properties.name === "string" ? properties.name : properties.label.split(",")[0];
        const context = [properties.locality, properties.region, properties.country].filter((part): part is string => typeof part === "string" && part !== label).join(", ");
        return [{
          id: typeof properties.id === "string" ? properties.id : location.join(","),
          label,
          context: context || "India",
          coordinate: location,
          kind: typeof properties.layer === "string" ? properties.layer : "place"
        }];
      })
    };
  });

  app.get("/api/v1/routes/driving", {
    config: {rateLimit: {max: 20, timeWindow: "1 minute"}}
  }, async (request, reply) => {
    if (!dependencies.apiKey) return serviceUnavailable(reply, request.id);
    const query = request.query as Record<string, string | undefined>;
    const start = parseCoordinate(query.start);
    const end = parseCoordinate(query.end);
    if (!start || !end) return invalid(reply, request.id, "start and end must be lng,lat coordinates within India");

    const url = new URL("https://api.openrouteservice.org/v2/directions/driving-car/geojson");

    const provider = await providerJson(dependencies.fetch, url, 15_000, reply, request.id, {
      method: "POST",
      // The /geojson endpoint answers with application/geo+json and enforces negotiation,
      // so asking for plain application/json here comes back 406.
      headers: {accept: "application/geo+json", authorization: dependencies.apiKey, "content-type": "application/json"},
      body: JSON.stringify({coordinates: [start, end], instructions: true, language: "en"})
    });
    if (!provider) return;
    const feature = Array.isArray(provider.features) && isObject(provider.features[0]) ? provider.features[0] : undefined;
    const properties = feature && isObject(feature.properties) ? feature.properties : undefined;
    const geometry = feature && isObject(feature.geometry) ? feature.geometry : undefined;
    if (!properties || !geometry) return notFound(reply, request.id, "No driving route was found");
    const coordinates = routeCoordinates(geometry);
    const summary = isObject(properties.summary) ? properties.summary : undefined;
    if (!coordinates.length || !summary || typeof summary.distance !== "number" || typeof summary.duration !== "number") {
      return providerFailure(reply, request.id, "Routing provider returned an incomplete route");
    }

    return {
      route: {
        distanceMeters: summary.distance,
        durationSeconds: summary.duration,
        geometry: {type: "LineString", coordinates},
        steps: routeSteps(properties.segments)
      }
    };
  });

  return app;
}

async function providerJson(fetcher: typeof fetch, url: URL, timeout: number, reply: FastifyReply, requestId: string, init: RequestInit = {}): Promise<JsonObject | undefined> {
  try {
    const response = await fetcher(url, {headers: {accept: "application/json"}, ...init, signal: AbortSignal.timeout(timeout)});
    if (!response.ok) {
      providerFailure(reply, requestId, `Location provider returned HTTP ${response.status}`);
      return;
    }
    const body: unknown = await response.json();
    if (!isObject(body)) {
      providerFailure(reply, requestId, "Location provider returned invalid JSON");
      return;
    }
    return body;
  } catch (cause) {
    const message = cause instanceof DOMException && cause.name === "TimeoutError" ? "Location provider timed out" : "Location provider could not be reached";
    reply.code(503).header("Retry-After", "5").send({error: {code: "provider_unavailable", message, requestId}});
  }
}

function parseCoordinate(value: string | undefined): Coordinate | undefined {
  if (!value) return;
  const parts = value.split(",").map(Number);
  if (parts.length !== 2 || !parts.every(Number.isFinite)) return;
  const [longitude, latitude] = parts;
  if (longitude < INDIA_BOUNDS.west || longitude > INDIA_BOUNDS.east || latitude < INDIA_BOUNDS.south || latitude > INDIA_BOUNDS.north) return;
  return [longitude, latitude];
}

function routeCoordinates(geometry: JsonObject): Coordinate[] {
  if (geometry.type === "LineString" && Array.isArray(geometry.coordinates)) return coordinates(geometry.coordinates);
  if (geometry.type === "MultiLineString" && Array.isArray(geometry.coordinates)) return geometry.coordinates.flatMap((line) => Array.isArray(line) ? coordinates(line) : []);
  return [];
}

function coordinates(values: unknown[]): Coordinate[] {
  return values.flatMap((coordinate) => Array.isArray(coordinate) && coordinate.length >= 2 && typeof coordinate[0] === "number" && typeof coordinate[1] === "number" ? [[coordinate[0], coordinate[1]] as Coordinate] : []);
}

function routeSteps(value: unknown): Array<Record<string, unknown>> {
  if (!Array.isArray(value)) return [];
  return value.flatMap((segment) => isObject(segment) && Array.isArray(segment.steps) ? segment.steps : []).flatMap((step) => {
    if (!isObject(step) || typeof step.instruction !== "string") return [];
    const wayPoints = Array.isArray(step.way_points) ? step.way_points : [];
    return [{
      instruction: step.instruction,
      type: typeof step.type === "number" ? String(step.type) : "0",
      distanceMeters: typeof step.distance === "number" ? step.distance : 0,
      fromIndex: typeof wayPoints[0] === "number" ? wayPoints[0] : 0
    }];
  });
}

function isObject(value: unknown): value is JsonObject {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function invalid(reply: FastifyReply, requestId: string, message: string) {
  return reply.code(400).send({error: {code: "invalid_request", message, requestId}});
}

function notFound(reply: FastifyReply, requestId: string, message: string) {
  return reply.code(404).send({error: {code: "route_not_found", message, requestId}});
}

function providerFailure(reply: FastifyReply, requestId: string, message: string) {
  return reply.code(502).send({error: {code: "provider_error", message, requestId}});
}

function serviceUnavailable(reply: FastifyReply, requestId: string) {
  return reply.code(503).send({error: {code: "service_not_configured", message: "Location services are not configured", requestId}});
}
