import { NavLink, Outlet } from 'react-router-dom'

const NAV = [
  { to: '/', label: 'Timeline', end: true },
  { to: '/import', label: 'Import', end: false },
  { to: '/gear', label: 'Gear', end: false },
]

export function AppLayout() {
  return (
    <div className="min-h-full">
      <header className="border-b border-hairline">
        <div className="mx-auto flex max-w-3xl items-baseline gap-6 px-6 py-4">
          <span className="text-sm font-semibold tracking-tight">Racing History</span>
          <nav className="flex gap-4 text-sm">
            {NAV.map(({ to, label, end }) => (
              <NavLink
                key={to}
                to={to}
                end={end}
                className={({ isActive }) =>
                  isActive ? 'text-ink' : 'text-ink/50 transition-colors hover:text-ink'
                }
              >
                {label}
              </NavLink>
            ))}
          </nav>
        </div>
      </header>
      <main className="mx-auto max-w-3xl px-6 py-8">
        <Outlet />
      </main>
    </div>
  )
}
