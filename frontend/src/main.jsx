import React from 'react'
import ReactDOM from 'react-dom/client'
import { createBrowserRouter, RouterProvider, Link } from 'react-router-dom'
import { AuthProvider } from './AuthContext.jsx'
import ExplorePage from './pages/ExplorePage.jsx'
import ToolDetailPage from './pages/ToolDetailPage.jsx'
import ComparePage from './pages/ComparePage.jsx'
import BookmarksPage from './pages/BookmarksPage.jsx'
import RecommendationsPage from './pages/RecommendationsPage.jsx'
import LoginPage from './pages/LoginPage.jsx'
import RegisterPage from './pages/RegisterPage.jsx'
import Navbar from './components/Navbar.jsx'
import { CompareProvider } from './components/CompareContext.jsx'
import './styles.css'

const router = createBrowserRouter([
  { path: '/', element: <ExplorePage /> },
  { path: '/tools/:slug', element: <ToolDetailPage /> },
  { path: '/compare', element: <ComparePage /> },
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
])

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <AuthProvider>
      <CompareProvider>
        <Navbar />
        <main className="container">
          <RouterProvider router={router} />
        </main>
        <footer className="footer">
          AI Tool Discovery — Django REST + pgvector · sentence embeddings · recommendations served from Postgres
        </footer>
      </CompareProvider>
    </AuthProvider>
  </React.StrictMode>,
)
