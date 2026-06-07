'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import {
  LayoutDashboard,
  Network,
  AlertTriangle,
  BookOpen,
  Settings,
} from 'lucide-react';

const navItems = [
  { href: '/', label: 'Dashboard', icon: LayoutDashboard },
  { href: '/architecture', label: 'Architecture', icon: Network },
  { href: '/drift', label: 'Drift Reports', icon: AlertTriangle },
  { href: '/rules', label: 'Rules', icon: BookOpen },
  { href: '/settings', label: 'Settings', icon: Settings },
];

export default function Sidebar() {
  const pathname = usePathname();

  return (
    <aside
      className="w-56 flex flex-col flex-shrink-0 border-r"
      style={{
        backgroundColor: 'var(--sidebar-bg)',
        borderColor: 'rgba(255,255,255,0.06)',
      }}
    >
      {/* Logo area */}
      <div
        className="h-14 flex items-center gap-2 px-4 border-b"
        style={{ borderColor: 'rgba(255,255,255,0.06)' }}
      >
        <CompassIcon />
        <span
          className="font-semibold text-sm tracking-wide"
          style={{ color: 'var(--sidebar-fg)' }}
        >
          GyroCompass
        </span>
      </div>

      {/* Navigation */}
      <nav className="flex-1 px-2 py-4 space-y-0.5">
        {navItems.map(({ href, label, icon: Icon }) => {
          const isActive = pathname === href;
          return (
            <Link
              key={href}
              href={href}
              className="flex items-center gap-3 px-3 py-2 rounded-md text-sm font-medium transition-colors"
              style={{
                color: isActive
                  ? 'var(--sidebar-active)'
                  : 'var(--sidebar-muted)',
                backgroundColor: isActive
                  ? 'var(--sidebar-active-bg)'
                  : 'transparent',
              }}
            >
              <Icon size={16} strokeWidth={isActive ? 2.5 : 1.8} />
              {label}
            </Link>
          );
        })}
      </nav>

      {/* Footer */}
      <div
        className="px-4 py-3 border-t text-xs"
        style={{
          borderColor: 'rgba(255,255,255,0.06)',
          color: 'var(--sidebar-muted)',
        }}
      >
        v0.1.0-alpha
      </div>
    </aside>
  );
}

function CompassIcon() {
  return (
    <svg
      width="22"
      height="22"
      viewBox="0 0 24 24"
      fill="none"
      stroke="var(--sidebar-active)"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <circle cx="12" cy="12" r="10" />
      <polygon points="16.24 7.76 14.12 14.12 7.76 16.24 9.88 9.88 16.24 7.76" />
    </svg>
  );
}
