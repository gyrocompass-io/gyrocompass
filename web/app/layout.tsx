import type { Metadata } from 'next';
import './globals.css';
import Sidebar from '@/components/Sidebar';
import Topbar from '@/components/Topbar';

export const metadata: Metadata = {
  title: 'GyroCompass — Architecture Guardrails',
  description:
    'Open-source architecture guardrails platform for AI coding agents',
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className="dark">
      <body className="flex h-screen overflow-hidden" style={{ backgroundColor: 'var(--background)', color: 'var(--foreground)' }}>
        <Sidebar />
        <div className="flex flex-col flex-1 overflow-hidden">
          <Topbar />
          <main className="flex-1 overflow-y-auto p-6" style={{ backgroundColor: 'var(--background)' }}>
            {children}
          </main>
        </div>
      </body>
    </html>
  );
}
