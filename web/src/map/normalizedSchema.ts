import type {ExpressionSpecification} from "maplibre-gl";

export const layers = {
  water: "water",
  waterways: "waterways",
  landcover: "landcover",
  landuse: "landuse",
  roads: "roads",
  places: "places",
  pois: "pois"
} as const;

export const poiName: ExpressionSpecification = ["coalesce", ["get", "name:en"], ["get", "name"], ["get", "brand"]];

export const poiType: ExpressionSpecification = ["match", ["get", "subcategory"],
  "fuel", "Petrol pump", "charging_station", "Charging station", "parking", "Parking",
  "station", "Rail station", "halt", "Rail stop", "bus_station", "Bus station", "ferry_terminal", "Ferry terminal",
  "aerodrome", "Airport", "terminal", "Airport terminal",
  "hospital", "Hospital", "clinic", "Clinic", "pharmacy", "Pharmacy",
  "school", "School", "college", "College", "university", "University",
  "restaurant", "Restaurant", "cafe", "Cafe", "fast_food", "Fast food", "bar", "Bar",
  "museum", "Museum", "gallery", "Gallery", "viewpoint", "Viewpoint", "attraction", "Landmark",
  "monument", "Monument", "memorial", "Memorial", "fort", "Fort", "castle", "Fort",
  "peak", "Peak", "beach", "Beach", "lighthouse", "Lighthouse", "tower", "Tower", "water_tower", "Water tower",
  "hotel", "Hotel", "hostel", "Hostel", "guest_house", "Guest house",
  "park", "Park", "garden", "Garden", "stadium", "Stadium", "sports_centre", "Sports centre",
  "place_of_worship", "Place of worship", "police", "Police", "fire_station", "Fire station", "post_office", "Post office",
  ["coalesce", ["get", "subcategory"], ["get", "category"], "Place"]];
