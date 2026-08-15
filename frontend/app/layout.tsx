import type { Metadata } from 'next';

import './globals.css';

export const metadata: Metadata = {
  title: 'get-out-door',
  description: 'Ask for a trail the way you would ask a local.',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
