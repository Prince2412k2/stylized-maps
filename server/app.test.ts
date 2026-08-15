import assert from "node:assert/strict";
import test from "node:test";
import {buildApp} from "./app.js";

test("search normalizes OpenRouteService results", async () => {
  const app = await buildApp({apiKey: "test", fetch: async () => new Response(JSON.stringify({features: [{properties: {id: "delhi", name: "New Delhi", label: "New Delhi, Delhi, India", region: "Delhi", country: "India", layer: "locality"}, geometry: {type: "Point", coordinates: [77.209, 28.6139]}}]}))});
  const response = await app.inject({method: "GET", url: "/api/v1/search?q=Delhi"});
  assert.equal(response.statusCode, 200);
  assert.deepEqual(response.json().results[0], {id: "delhi", label: "New Delhi", context: "Delhi, India", coordinate: [77.209, 28.6139], kind: "locality"});
  await app.close();
});

test("route normalizes geometry and instructions", async () => {
  const provider = {features: [{geometry: {type: "LineString", coordinates: [[77.2, 28.6], [77.3, 28.7]]}, properties: {summary: {distance: 1200, duration: 300}, segments: [{steps: [{distance: 1200, type: 6, way_points: [0, 1], instruction: "Drive north"}]}]}}]};
  const app = await buildApp({apiKey: "test", fetch: async (input, init) => {
    assert.equal(String(input), "https://api.openrouteservice.org/v2/directions/driving-car/geojson");
    assert.equal(init?.method, "POST");
    assert.equal((init?.headers as Record<string, string>).authorization, "test");
    // The /geojson endpoint enforces content negotiation and answers 406 for application/json.
    assert.equal((init?.headers as Record<string, string>).accept, "application/geo+json");
    return new Response(JSON.stringify(provider));
  }});
  const response = await app.inject({method: "GET", url: "/api/v1/routes/driving?start=77.2,28.6&end=77.3,28.7"});
  assert.equal(response.statusCode, 200);
  assert.equal(response.json().route.steps[0].instruction, "Drive north");
  assert.deepEqual(response.json().route.geometry.coordinates, [[77.2, 28.6], [77.3, 28.7]]);
  await app.close();
});

test("route rejects coordinates outside India", async () => {
  const app = await buildApp({apiKey: "test", fetch});
  const response = await app.inject({method: "GET", url: "/api/v1/routes/driving?start=0,0&end=77.3,28.7"});
  assert.equal(response.statusCode, 400);
  assert.equal(response.json().error.code, "invalid_request");
  await app.close();
});
