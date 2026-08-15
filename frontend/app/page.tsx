'use client';

import dynamic from 'next/dynamic';
import { useState } from 'react';

import { ChatPanel } from '@/components/ChatPanel';

// MapLibre touches window at import time, so it must not be server-rendered.
const MapView = dynamic(() => import('@/components/MapView').then((m) => m.MapView), {
  ssr: false,
});

export default function Home() {
  const [geometry, setGeometry] = useState<GeoJSON.Feature | GeoJSON.Geometry | null>(null);

  return (
    <main className="shell">
      <ChatPanel onGeometry={setGeometry} />
      <div className="map">
        <MapView geometry={geometry} />
        {!geometry && (
          <p className="map-empty">Pick a trail to see it drawn here.</p>
        )}
      </div>
    </main>
  );
}
