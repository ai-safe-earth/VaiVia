import { createElement } from 'react';
import { vi } from 'vitest';

import type { LineEntry } from '@/lib/mapTurn';

// MapLibre needs WebGL, which jsdom has not got. Component tests pin WHICH
// line an open card's map is handed; the map itself is the browser's (e2e).
vi.mock('@/components/CardMap', () => ({
  CardMap: ({ line }: { routeId: string; line?: LineEntry }) =>
    createElement('div', {
      'data-testid': 'card-map',
      'data-line':
        line === undefined
          ? 'loading'
          : line.status === 'ok'
            ? `ok:${line.feature.properties?.route_id}`
            : line.status,
    }),
}));
