'use client';

import 'maplibre-gl/dist/maplibre-gl.css';

import maplibregl, { type Map as MapLibreMap } from 'maplibre-gl';
import { useEffect, useRef, useState } from 'react';

import { focusedRouteId, noteOf } from '@/lib/mapTurn';

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

/** Brand colours live in tokens.css; MapLibre paint properties cannot read a
 *  CSS variable, so this is the one place a component resolves one. The
 *  fallback is the token's own value, for the server pass where there is no
 *  computed style to read. */
function token(name: string, fallback: string): string {
  if (typeof window === 'undefined') return fallback;
  const value = getComputedStyle(document.documentElement).getPropertyValue(name);
  return value.trim() || fallback;
}

/**
 * The raster basemap is a light OSM style and the app is dark, so the tiles are
 * desaturated and darkened onto --vv-map at the layer level rather than with a
 * CSS filter over the canvas — a filter would take the route line down with it.
 * A real dark vector style is the proper fix when the beta needs one.
 */
const STYLE: maplibregl.StyleSpecification = {
  version: 8,
  sources: {
    osm: {
      type: 'raster',
      tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],
      tileSize: 256,
      attribution: OSM_ATTRIBUTION,
    },
    opentopo: {
      type: 'raster',
      tiles: ['https://tile.opentopomap.org/{z}/{x}/{y}.png'],
      tileSize: 256,
      attribution:
        '© OpenStreetMap contributors, SRTM | style © <a href="https://opentopomap.org">OpenTopoMap</a> (CC-BY-SA)',
    },
    satellite: {
      type: 'raster',
      // Esri tiles are {z}/{y}/{x}, not {z}/{x}/{y}.
      tiles: [
        'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
      ],
      tileSize: 256,
      attribution:
        'Imagery © Esri — Esri, Maxar, Earthstar Geographics, GIS User Community',
    },
  },
  // All three basemaps live in the style from construction and a switch only
  // flips visibility — map.setStyle would destroy the selection source and
  // its layers, which are added at runtime. Desaturation is the default
  // basemap's; terrain and satellite are shown as themselves.
  layers: [
    {
      id: 'osm',
      type: 'raster',
      source: 'osm',
      paint: {
        'raster-saturation': -0.8,
        'raster-brightness-max': 0.5,
        'raster-contrast': 0.15,
      },
    },
    {
      id: 'opentopo',
      type: 'raster',
      source: 'opentopo',
      layout: { visibility: 'none' },
    },
    {
      id: 'satellite',
      type: 'raster',
      source: 'satellite',
      layout: { visibility: 'none' },
    },
  ],
};

const BASEMAPS = [
  { id: 'osm', label: 'Map' },
  { id: 'opentopo', label: 'Terrain' },
  { id: 'satellite', label: 'Satellite' },
] as const;

type BasemapId = (typeof BASEMAPS)[number]['id'];

interface Props {
  geometry: GeoJSON.Feature | GeoJSON.FeatureCollection | GeoJSON.Geometry | null;
}

export function MapView({ geometry }: Props) {
  const container = useRef<HTMLDivElement>(null);
  const map = useRef<MapLibreMap | null>(null);
  const [basemap, setBasemap] = useState<BasemapId>('osm');

  const switchBasemap = (id: BasemapId) => {
    setBasemap(id);
    const instance = map.current;
    if (!instance) return;
    for (const layer of BASEMAPS) {
      instance.setLayoutProperty(
        layer.id,
        'visibility',
        layer.id === id ? 'visible' : 'none',
      );
    }
  };

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
        // Line colour encodes the difficulty band (BRAND-SPEC amendment
        // 2026-08-27): lime easy, amber moderate, flare hard, muted
        // ungraded. The fallback is lime — a composed A→B route carries no
        // band and stays the route colour it always was.
        const bandColor: maplibregl.ExpressionSpecification = [
          'match',
          ['get', 'difficulty_band'],
          'easy',
          token('--vv-lime', '#CCFF3B'),
          'moderate',
          token('--vv-amber', '#FFC53B'),
          'hard',
          token('--vv-flare', '#FF6B3D'),
          'ungraded',
          token('--vv-muted', '#A7ADA6'),
          // All mtb routes wear the palette's near-black (owner decision
          // 2026-08-27); the ramp above is hike-only.
          'mtb',
          token('--vv-ground', '#0D0F0E'),
          token('--vv-lime', '#CCFF3B'),
        ];
        // The picked route is wider and full-opacity; its siblings dim. No
        // casing (owner decision 2026-08-27). The layer is created once and
        // restyles through setData — the expressions are all data-driven.
        instance.addLayer({
          id: 'selection-line',
          type: 'line',
          source: 'selection',
          paint: {
            'line-color': bandColor,
            'line-width': ['case', selected, 4, 2],
            'line-opacity': ['case', selected, 1, 0.4],
          },
          layout: { 'line-cap': 'round', 'line-join': 'round' },
        });
      }

      const bounds = boundsOf(data);
      if (bounds) instance.fitBounds(bounds, { padding: 64, maxZoom: 15, duration: 600 });
    };

    if (instance.isStyleLoaded()) draw();
    // 'idle', not 'load': load fires exactly once per map, so an update
    // arriving while the style is transiently busy after the initial load
    // registered on an event that would never fire again — and was silently
    // dropped, forever. idle fires after every render settles.
    else instance.once('idle', draw);
  }, [geometry]);

  // The drawn line's caveat (a multi-piece route served as its longest piece
  // says so in properties.note) is shown ON the map: the card can say
  // "mapped in pieces", but the line is what a walker plans around. The
  // focused route id rides along as a data attribute so a browser test can
  // assert the drawn line IS the clicked card's.
  const note = noteOf(geometry);
  return (
    <div
      style={{ position: 'absolute', inset: 0 }}
      data-selected-route={focusedRouteId(geometry) ?? undefined}
    >
      <div ref={container} style={{ position: 'absolute', inset: 0 }} />
      <nav className="basemap" aria-label="Basemap">
        {BASEMAPS.map((layer) => (
          <button
            key={layer.id}
            type="button"
            className={basemap === layer.id ? 'active' : undefined}
            onClick={() => switchBasemap(layer.id)}
          >
            {layer.label}
          </button>
        ))}
      </nav>
      {note && <div className="map-note vv-body-sm">{note}</div>}
    </div>
  );
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
