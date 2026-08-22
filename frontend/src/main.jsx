import React from 'react'
import ReactDOM from 'react-dom/client'
import { createBrowserRouter, RouterProvider, Link, Outlet } from 'react-router-dom'
import { AuthProvider } from './AuthContext.jsx'
import ExplorePage from './pages/ExplorePage.jsx'
import ToolDetailPage from './pages/ToolDetailPage.jsx'
import ComparePage from './pages/ComparePage.jsx'
import BookmarksPage from './pages/BookmarksPage.jsx'
import RecommendationsPage from './pages/RecommendationsPage.jsx'
import LoginPage from './pages/LoginPage.jsx'
import RegisterPage from './pages/RegisterPage.jsx'
import ChatPage from './pages/ChatPage.jsx'
import Navbar from './components/Navbar.jsx'
import { CompareProvider } from './components/CompareContext.jsx'
import './styles.css'

/**
 * App shell — Navbar/footer live INSIDE the router as a layout route.
 * They previously rendered outside <RouterProvider>, and Navbar's
 * useNavigate() hook then crashed the entire tree on mount
 * ("useNavigate() may be used only in the context of a <Router>
 * component") → blank black page. The layout route fixes the context.
 */
function Shell() {
  return (
    <AuthProvider>
      <CompareProvider>
        <Navbar />
        <main className="container">
          <Outlet />
        </main>
        <footer className="footer">
          <span>AI Tool Discovery</span>
          <span>Django REST + pgvector · sentence embeddings · recommendations served from Postgres</span>
        </footer>
      </CompareProvider>
    </AuthProvider>
  )
}

const router = createBrowserRouter([
  {
    element: <Shell />,
    children: [
      { path: '/', element: <ExplorePage /> },
      { path: '/tools/:slug', element: <ToolDetailPage /> },
      { path: '/compare', element: <ComparePage /> },
      { path: '/chat', element: <ChatPage /> },
      { path: '/bookmarks', element: <BookmarksPage /> },
      { path: '/recommendations', element: <RecommendationsPage /> },
      { path: '/login', element: <LoginPage /> },
      { path: '/register', element: <RegisterPage /> },
      { path: '*', element: (
        <div className="empty-state">
          <h2>404</h2>
          <p>That page drifted out of the embedding space.</p>
          <Link className="btn btn-primary" to="/">Back to explore</Link>
        </div>
      ) },
    ],
  },
])

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <RouterProvider router={router} />
  </React.StrictMode>,
)
