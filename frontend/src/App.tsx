import { Route, Routes } from 'react-router-dom'
import { RequireAuth } from '@/components/RequireAuth'
import { AppLayout } from '@/components/AppLayout'
import Login from '@/pages/Login'
import Timeline from '@/pages/Timeline'
import RaceDetail from '@/pages/RaceDetail'
import RaceEdit from '@/pages/RaceEdit'
import Import from '@/pages/Import'
import GearPage from '@/pages/Gear'

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route
        element={
          <RequireAuth>
            <AppLayout />
          </RequireAuth>
        }
      >
        <Route path="/" element={<Timeline />} />
        <Route path="/races/:id" element={<RaceDetail />} />
        <Route path="/races/:id/edit" element={<RaceEdit />} />
        <Route path="/import" element={<Import />} />
        <Route path="/gear" element={<GearPage />} />
      </Route>
    </Routes>
  )
}
