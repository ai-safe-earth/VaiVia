import type { Metadata, Viewport } from 'next';

// Brand tokens first: every application rule below is written against these
// variables, and nothing outside this import may hardcode a brand colour.
import './tokens.css';
import './globals.css';

export const metadata: Metadata = {
  title: 'VaiVia',
  description: 'Ask for a trail the way you would ask a local.',
};

/**
 * The composer is the bottom edge of the app, so the two things that move a
 * bottom edge are declared here rather than worked around in CSS.
 *
 * `interactiveWidget: 'resizes-content'` makes the soft keyboard SHRINK the
 * layout viewport instead of sliding it: 100dvh then follows the keyboard and
 * the composer stays above it. Chrome and Android honour it; iOS Safari
 * ignores it and slides the visual viewport instead, which needs a
 * visualViewport listener — deliberately not written until a real iOS device
 * says it is needed, because it is code that cannot be tested here.
 *
 * `viewportFit: 'cover'` lets the map reach the edges of a notched screen; the
 * shell pays the safe-area insets back as padding (globals.css), so nothing
 * interactive sits under a home indicator.
 */
export const viewport: Viewport = {
  width: 'device-width',
  initialScale: 1,
  viewportFit: 'cover',
  interactiveWidget: 'resizes-content',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
