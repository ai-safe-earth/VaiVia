import type { Metadata } from 'next';

// Brand tokens first: every application rule below is written against these
// variables, and nothing outside this import may hardcode a brand colour.
import './tokens.css';
import './globals.css';

export const metadata: Metadata = {
  title: 'VaiVia',
  description: 'Ask for a trail the way you would ask a local.',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
