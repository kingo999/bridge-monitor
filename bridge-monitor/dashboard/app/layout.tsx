import type { Metadata } from 'next';
import '@xyflow/react/dist/style.css';
import './globals.css';

export const metadata: Metadata = {
  title: 'Bridge Monitor',
  description: 'Live GPT, Claude and Skynet exchange monitor',
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}
