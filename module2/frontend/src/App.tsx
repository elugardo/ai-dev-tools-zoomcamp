import { Route, Routes } from 'react-router-dom'
import { AuthProvider } from './auth/AuthContext'
import { RequireRole } from './auth/RequireRole'
import { Layout } from './components/Layout'
import { AdminDashboardPage } from './pages/AdminDashboardPage'
import { HomePage } from './pages/HomePage'
import { LoginPage } from './pages/LoginPage'
import { NotFoundPage } from './pages/NotFoundPage'
import { RestaurantDashboardPage } from './pages/RestaurantDashboardPage'
import { RestaurantFormPage } from './pages/RestaurantFormPage'
import { RestaurantPage } from './pages/RestaurantPage'
import { WaitStatusPage } from './pages/WaitStatusPage'
import type { WaitWiseService } from './services/WaitWiseService'
import { ServicesProvider } from './services/ServicesContext'

/** The route table from spec §23. The router itself is supplied by the caller. */
export function App({ services }: { services: WaitWiseService }) {
  return (
    <ServicesProvider services={services}>
      <AuthProvider>
        <Routes>
          <Route element={<Layout />}>
            <Route path="/" element={<HomePage />} />
            <Route path="/restaurants/:restaurantId" element={<RestaurantPage />} />
            <Route path="/wait/:token" element={<WaitStatusPage />} />
            <Route path="/login" element={<LoginPage />} />
            <Route
              path="/admin"
              element={
                <RequireRole role="ADMIN">
                  <AdminDashboardPage />
                </RequireRole>
              }
            />
            <Route
              path="/admin/restaurants/new"
              element={
                <RequireRole role="ADMIN">
                  <RestaurantFormPage />
                </RequireRole>
              }
            />
            <Route
              path="/admin/restaurants/:id/edit"
              element={
                <RequireRole role="ADMIN">
                  <RestaurantFormPage />
                </RequireRole>
              }
            />
            <Route
              path="/restaurant/dashboard"
              element={
                <RequireRole role="RESTAURANT">
                  <RestaurantDashboardPage />
                </RequireRole>
              }
            />
            <Route path="*" element={<NotFoundPage />} />
          </Route>
        </Routes>
      </AuthProvider>
    </ServicesProvider>
  )
}
