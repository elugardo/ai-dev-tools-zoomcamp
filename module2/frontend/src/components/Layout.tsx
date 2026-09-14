import { Link, NavLink, Outlet, useNavigate } from 'react-router-dom'
import { homePathFor, useAuth } from '../auth/AuthContext'
import { resetMockData, serviceMode } from '../services'

export function Layout() {
  const { user, logout } = useAuth()
  const navigate = useNavigate()

  function handleLogout() {
    logout()
    navigate('/')
  }

  function handleReset() {
    resetMockData()
    window.location.assign('/')
  }

  return (
    <div className="app">
      <header className="topbar">
        <div className="topbar__inner">
          <Link to="/" className="logo" aria-label="WaitWise home">
            <span className="logo__mark" aria-hidden="true">
              W
            </span>
            WaitWise
          </Link>
          <nav className="topbar__nav">
            {user ? (
              <>
                <NavLink to={homePathFor(user)} className="topbar__link">
                  {user.role === 'ADMIN' ? 'Admin' : 'Dashboard'}
                </NavLink>
                <button type="button" className="button button--ghost button--small" onClick={handleLogout}>
                  Log out
                </button>
              </>
            ) : (
              <NavLink to="/login" className="topbar__link">
                Staff login
              </NavLink>
            )}
          </nav>
        </div>
      </header>
      <main className="page">
        <Outlet />
      </main>
      {serviceMode === 'mock' ? (
        <footer className="footer">
          <span>Demo mode — data is simulated in this browser.</span>
          <button type="button" className="link-button" onClick={handleReset}>
            Reset demo data
          </button>
        </footer>
      ) : null}
    </div>
  )
}
