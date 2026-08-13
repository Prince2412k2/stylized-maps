import java.util.Map;
import java.util.Set;

final class PoiTaxonomy {
  static final Map<String, String> AMENITIES = Map.ofEntries(
    Map.entry("fuel", "service"), Map.entry("charging_station", "service"), Map.entry("parking", "service"),
    Map.entry("bus_station", "transit"), Map.entry("ferry_terminal", "transit"),
    Map.entry("hospital", "health"), Map.entry("clinic", "health"), Map.entry("pharmacy", "health"),
    Map.entry("school", "education"), Map.entry("college", "education"), Map.entry("university", "education"),
    Map.entry("place_of_worship", "worship"),
    Map.entry("restaurant", "food"), Map.entry("cafe", "food"), Map.entry("fast_food", "food"), Map.entry("bar", "food"),
    Map.entry("cinema", "culture"), Map.entry("theatre", "culture"), Map.entry("library", "culture"),
    Map.entry("police", "civic"), Map.entry("fire_station", "civic"), Map.entry("post_office", "civic"), Map.entry("townhall", "civic"),
    Map.entry("marketplace", "shop")
  );
  static final Set<String> LANDMARK_TOURISM = Set.of("attraction", "museum", "gallery", "viewpoint", "zoo", "aquarium", "theme_park");
  static final Set<String> LANDMARK_HISTORIC = Set.of("monument", "memorial", "fort", "castle", "ruins", "archaeological_site");

  private PoiTaxonomy() {}
}
