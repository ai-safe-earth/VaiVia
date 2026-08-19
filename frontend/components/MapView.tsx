'use client';

import 'maplibre-gl/dist/maplibre-gl.css';

import maplibregl, { type Map as MapLibreMap } from 'maplibre-gl';
import { useEffect, useRef } from 'react';

const LECCO: [number, number] = [9.39, 45.86];

/**
 * Raster style using OSM tiles directly — no API key. Swap for a vector style
 * when the beta needs one.
 *
 * The attribution covers BOTH the tiles and the data: every line we draw is
 * OSM-derived geometry, not just the basemap under it. ODbL asks for
 * attribution on produced works, so it is rendered expanded rather than behind
 * the collapsed ⓘ toggle. The app-level credit in the chat column covers the
 * answers, which carry the same data without a map in view.
 */
const OSM_ATTRIBUTION =
  'Map data and trails © <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">OpenStreetMap</a> contributors, under <a href="https://opendatacommons.org/licenses/odbl/" target="_blank" rel="noreferrer">ODbL</a>';

const STYLE: maplibregl.StyleSpecification = {
  version: 8,
  sources: {
    osm: {
      type: 'raster',
      tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],
      tileSize: 256,
      attribution: OSM_ATTRIBUTION,
    },
  },
  layers: [{ id: 'osm', type: 'raster', source: 'osm' }],
};

interface Props {
  geometry: GeoJSON.Feature | GeoJSON.FeatureCollection | GeoJSON.Geometry | null;
}

export function MapView({ geometry }: Props) {
  const container = useRef<HTMLDivElement>(null);
  const map = useRef<MapLibreMap | null>(null);

  useEffect(() => {
    if (!container.current || map.current) return;
    map.current = new maplibregl.Map({
      container: container.current,
      style: STYLE,
      center: LECCO,
      zoom: 11,
      attributionControl: { compact: false },
    });
    map.current.addControl(new maplibregl.NavigationControl(), 'top-right');

    return () => {
      map.current?.remove();
      map.current = null;
    };
  }, []);

  useEffect(() => {
    const instance = map.current;
    if (!instance) return;

    const draw = () => {
      const data: GeoJSON.Feature | GeoJSON.FeatureCollection =
        geometry && 'type' in geometry && (geometry.type === 'Feature' || geometry.type === 'FeatureCollection')
          ? (geometry as GeoJSON.Feature | GeoJSON.FeatureCollection)
          : { type: 'Feature', properties: {}, geometry: (geometry ?? null) as GeoJSON.Geometry };

      const source = instance.getSource('selection') as maplibregl.GeoJSONSource | undefined;
      if (!geometry) {
        source?.setData({ type: 'FeatureCollection', features: [] });
        return;
      }

      if (source) {
        source.setData(data);
      } else {
        instance.addSource('selection', { type: 'geojson', data });
        // Styling is data-driven on `properties.selected` so several routes can
        // be shown at once with one of them picked out. A feature without that
        // property — a trail, or a single route — reads as unselected, and the
        // widths below deliberately make that case look the way it always has.
        // Typed explicitly: TypeScript otherwise widens the tuple to
        // string[] and MapLibre's paint props reject it.
        const selected: maplibregl.ExpressionSpecification = [
          'boolean',
          ['get', 'selected'],
          false,
        ];
        // Casing under the line keeps it legible over both forest and water.
        instance.addLayer({
          id: 'selection-casing',
          type: 'line',
          source: 'selection',
          paint: {
            'line-color': '#0b3a24',
            'line-width': ['case', selected, 7, 4],
            'line-opacity': ['case', selected, 0.8, 0.35],
          },
          layout: { 'line-cap': 'round', 'line-join': 'round' },
        });
        instance.addLayer({
          id: 'selection-line',
          type: 'line',
          source: 'selection',
          paint: {
            'line-color': '#6fcf97',
            'line-width': ['case', selected, 3.5, 2],
            'line-opacity': ['case', selected, 1, 0.55],
          },
          layout: { 'line-cap': 'round', 'line-join': 'round' },
        });
      }

      const bounds = boundsOf(data);
      if (bounds) instance.fitBounds(bounds, { padding: 64, maxZoom: 15, duration: 600 });
    };

    if (instance.isStyleLoaded()) draw();
    else instance.once('load', draw);
  }, [geometry]);

  return <div ref={container} style={{ position: 'absolute', inset: 0 }} />;
}

/** Bounds over every coordinate in the line features of a Feature or collection. */
function boundsOf(
  data: GeoJSON.Feature | GeoJSON.FeatureCollection,
): maplibregl.LngLatBoundsLike | null {
  const all = data.type === 'FeatureCollection' ? data.features : [data];
  // Zoom to the picked route when there is one, otherwise frame them all. That
  // is what makes clicking a card feel like choosing rather than reloading.
  const picked = all.filter((feature) => feature.properties?.selected);
  const features = picked.length > 0 ? picked : all;
  const lines: GeoJSON.Position[][] = features.flatMap((feature) => {
    const geometry = feature.geometry;
    if (!geometry) return [];
    return geometry.type === 'MultiLineString'
      ? geometry.coordinates
      : geometry.type === 'LineString'
        ? [geometry.coordinates]
        : [];
  });

  const points = lines.flat();
  if (points.length === 0) return null;

  const bounds = new maplibregl.LngLatBounds(
    points[0] as [number, number],
    points[0] as [number, number],
  );
  for (const point of points) bounds.extend(point as [number, number]);
  return bounds;
}
