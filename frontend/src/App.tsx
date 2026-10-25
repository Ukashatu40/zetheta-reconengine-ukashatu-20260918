import { Route, Routes } from 'react-router-dom'
import { useSession } from '@/auth/session'
import { LockScreen } from '@/auth/LockScreen'
import { Shell } from '@/components/Shell'
import { AlertsPage } from '@/features/alerts/AlertsPage'
import { AuditPage } from '@/features/audit/AuditPage'
import { ExceptionsPage } from '@/features/exceptions/ExceptionsPage'
import { FilesPage } from '@/features/files/FilesPage'
import { OverviewPage } from '@/features/overview/OverviewPage'
import { ReviewsPage } from '@/features/reviews/ReviewsPage'
import { RunsPage } from '@/features/runs/RunsPage'

function NotFound() {
  return (
    <div className="page">
      <h1 className="page__title">No such page</h1>
      <p className="page__lede">Use the menu to find what you were looking for.</p>
    </div>
  )
}

export function App() {
  const { me, checking } = useSession()
  if (!me)
    return checking ? (
      <div className="lock" role="status">
        Checking your key…
      </div>
    ) : (
      <LockScreen />
    )
  return (
    <Routes>
      <Route element={<Shell />}>
        <Route index element={<OverviewPage />} />
        <Route path="exceptions" element={<ExceptionsPage />} />
        <Route path="reviews" element={<ReviewsPage />} />
        <Route path="alerts" element={<AlertsPage />} />
        <Route path="audit" element={<AuditPage />} />
        <Route path="files" element={<FilesPage />} />
        <Route path="runs" element={<RunsPage />} />
        <Route path="*" element={<NotFound />} />
      </Route>
    </Routes>
  )
}
