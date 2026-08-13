import com.onthegomap.planetiler.FeatureCollector;
import com.onthegomap.planetiler.Planetiler;
import com.onthegomap.planetiler.Profile;
import com.onthegomap.planetiler.config.Arguments;
import com.onthegomap.planetiler.reader.SourceFeature;
import java.nio.file.Path;
import java.util.Set;

public class IndiaPoc implements Profile {
  private static final Set<String> MAJOR_ROADS = Set.of("motorway", "trunk", "primary", "secondary");
  private static final Set<String> MINOR_ROADS = Set.of("tertiary", "residential", "unclassified", "service");

  public static void main(String[] args) {
    String source = System.getenv().getOrDefault("MAP_OSM_SOURCE", "data/sources/india.osm.pbf");
    String output = System.getenv().getOrDefault("MAP_VECTOR_OUTPUT", "data/india.pmtiles");
    var arguments = Arguments.fromArgsOrConfigFile(args)
      .withDefault("minzoom", 4)
      .withDefault("maxzoom", 14);

    Planetiler.create(arguments)
      .setProfile(new IndiaPoc())
      .addOsmSource("osm", Path.of(source), null)
      .overwriteOutput(Path.of(output))
      .run();
  }

  @Override
  public void processFeature(SourceFeature source, FeatureCollector features) {
    if (source.canBePolygon() && source.hasTag("natural", "water")) {
      features.polygon(NormalizedSchema.WATER).setMinZoom(5).setMinPixelSize(0.5);
    }

    if (source.canBeLine() && source.hasTag("waterway")) {
      features.line(NormalizedSchema.WATERWAYS)
        .setMinZoom(source.hasTag("waterway", "river") ? 6 : 11)
        .setAttr("class", source.getTag("waterway"))
        .setMinPixelSize(0);
    }

    if (source.canBePolygon() && (source.hasTag("natural", "wood") || source.hasTag("landuse", "forest"))) {
      features.polygon(NormalizedSchema.LANDCOVER).setMinZoom(6).setAttr("class", "forest").setMinPixelSize(1);
    }

    if (source.canBePolygon() && source.hasTag("landuse", "grass", "meadow", "farmland")) {
      features.polygon(NormalizedSchema.LANDCOVER).setMinZoom(7).setAttr("class", source.getTag("landuse")).setMinPixelSize(1);
    }

    if (source.canBePolygon() && source.hasTag("landuse", "residential")) {
      features.polygon(NormalizedSchema.LANDUSE).setMinZoom(8).setAttr("class", "residential").setMinPixelSize(1);
    }

    if (source.canBeLine() && source.hasTag("railway", "rail", "light_rail", "subway")) {
      features.line(NormalizedSchema.ROADS).setMinZoom(6).setAttr("class", "rail").setMinPixelSize(0);
    }

    if (source.canBeLine() && source.hasTag("highway")) {
      String roadClass = source.getString("highway").replace("_link", "");
      int minZoom = switch (roadClass) {
        case "motorway", "trunk" -> 5;
        case "primary" -> 6;
        case "secondary" -> 8;
        default -> MINOR_ROADS.contains(roadClass) ? 11 : 13;
      };
      features.line(NormalizedSchema.ROADS)
        .setMinZoom(minZoom)
        .setAttr("class", roadClass)
        .setAttr("name", source.getTag("name"))
        .setMinPixelSize(0);
    }

    if (source.isPoint() && source.hasTag("place", "city", "town", "village", "suburb")) {
      String placeClass = source.getString("place");
      int minZoom = switch (placeClass) {
        case "city" -> 5;
        case "town" -> 7;
        default -> 10;
      };
      features.point(NormalizedSchema.PLACES)
        .setMinZoom(minZoom)
        .setAttr("class", placeClass)
        .setAttr("name", source.getTag("name"))
        .setAttr("name:en", source.getTag("name:en"))
        .setPointLabelGridSizeAndLimit(12, 64, 2);
    }

    addPoi(source, features);
  }

  private static void addPoi(SourceFeature source, FeatureCollector features) {
    String category = null;
    String subcategory = null;
    int minZoom = 14;

    String amenity = source.getString("amenity");
    String tourism = source.getString("tourism");
    String historic = source.getString("historic");
    if (amenity != null && PoiTaxonomy.AMENITIES.containsKey(amenity)) {
      category = PoiTaxonomy.AMENITIES.get(amenity);
      subcategory = amenity;
      minZoom = Set.of("fuel", "hospital", "bus_station", "ferry_terminal", "university").contains(amenity) ? 13 : 14;
    } else if (source.hasTag("railway", "station", "halt")) {
      category = "transit";
      subcategory = source.getString("railway");
      minZoom = 12;
    } else if (source.hasTag("aeroway", "aerodrome", "terminal")) {
      category = "transit";
      subcategory = source.getString("aeroway");
      minZoom = 11;
    } else if (tourism != null && PoiTaxonomy.LANDMARK_TOURISM.contains(tourism)) {
      category = "landmark";
      subcategory = tourism;
      minZoom = 12;
    } else if (source.hasTag("tourism", "hotel", "hostel", "guest_house")) {
      category = "lodging";
      subcategory = source.getString("tourism");
      minZoom = 14;
    } else if (historic != null && PoiTaxonomy.LANDMARK_HISTORIC.contains(historic)) {
      category = "landmark";
      subcategory = historic;
      minZoom = 12;
    } else if (source.hasTag("natural", "peak", "beach")) {
      category = "landmark";
      subcategory = source.getString("natural");
      minZoom = 12;
    } else if (source.hasTag("man_made", "lighthouse", "tower", "water_tower")) {
      category = "landmark";
      subcategory = source.getString("man_made");
      minZoom = 13;
    } else if (source.hasTag("leisure", "park", "stadium", "sports_centre", "garden")) {
      category = "recreation";
      subcategory = source.getString("leisure");
      minZoom = source.hasTag("leisure", "stadium") ? 12 : 13;
    } else if (source.hasTag("shop")) {
      category = "shop";
      subcategory = source.getString("shop");
    }

    if (category == null) return;
    var poi = source.isPoint() ? features.point(NormalizedSchema.POIS) : source.canBePolygon() ? features.pointOnSurface(NormalizedSchema.POIS) : null;
    if (poi == null) return;

    int limit = category.equals("landmark") || category.equals("transit") ? 6 : category.equals("service") ? 4 : 3;
    poi.setMinZoom(minZoom)
      .setAttr("category", category)
      .setAttr("subcategory", subcategory)
      .setAttr("name", source.getTag("name"))
      .setAttr("name:en", source.getTag("name:en"))
      .setAttr("brand", source.getTag("brand"))
      .setAttr("opening_hours", source.getTag("opening_hours"))
      .setAttr("operator", source.getTag("operator"))
      .setAttr("phone", source.getTag("phone"))
      .setAttr("website", source.getTag("website"))
      .setAttr("wheelchair", source.getTag("wheelchair"))
      .setPointLabelGridSizeAndLimit(12, 32, limit);
  }

  @Override
  public String name() {
    return "India illustrated map POC";
  }

  @Override
  public String attribution() {
    return OSM_ATTRIBUTION;
  }
}
