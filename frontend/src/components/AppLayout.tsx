import { NavLink, Outlet, useNavigate } from 'react-router-dom'
import { logout, useInvalidateCurrentUser } from '@/api/auth'

const NAV = [
  { to: '/', label: 'Timeline', end: true },
  { to: '/import', label: 'Import', end: false },
  { to: '/gear', label: 'Gear', end: false },
]

export function AppLayout() {
  const navigate = useNavigate()
  const invalidateCurrentUser = useInvalidateCurrentUser()

  const handleLogout = async () => {
    await logout()
    await invalidateCurrentUser()
    navigate('/login', { replace: true })
  }

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
          <button
            type="button"
            onClick={handleLogout}
            className="ml-auto text-sm text-ink/50 transition-colors hover:text-ink"
          >
            Log out
          </button>
        </div>
      </header>
      <main className="mx-auto max-w-3xl px-6 py-8">
        <Outlet />
      </main>
    </div>
  )
}
