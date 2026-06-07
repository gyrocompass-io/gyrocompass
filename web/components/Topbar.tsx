'use client';

import { usePathname } from 'next/navigation';
import { Bell } from 'lucide-react';

const pageTitles: Record<string, string> = {
  '/': 'Dashboard',
  '/architecture': 'Architecture Explorer',
  '/drift': 'Drift Reports',
  '/rules': 'Rules',
  '/settings': 'Settings',
};

export default function Topbar() {
  const pathname = usePathname();
  const title = pageTitles[pathname] ?? 'GyroCompass';

  return (
    <header
      className="h-14 flex items-center justify-between px-6 border-b flex-shrink-0"
      style={{
        backgroundColor: 'var(--topbar-bg)',
        borderColor: 'rgba(255,255,255,0.06)',
        color: 'var(--topbar-fg)',
      }}
    >
      <h1 className="text-sm font-semibold tracking-wide">{title}</h1>

      <div className="flex items-center gap-3">
        <button
          className="p-1.5 rounded-md transition-colors hover:bg-white/10"
          aria-label="Notifications"
        >
          <Bell size={16} style={{ color: 'var(--sidebar-muted)' }} />
        </button>

        <div
          className="w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold"
          style={{ backgroundColor: 'var(--sidebar-active)', color: '#fff' }}
        >
          GC
        </div>
      </div>
    </header>
  );
}
