import type {StyleSpecification} from "maplibre-gl";
import type {ReleaseCatalog} from "../map/catalog";

export type RendererFactory = (catalog: ReleaseCatalog) => StyleSpecification;
