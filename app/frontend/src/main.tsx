import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { LanguageProvider } from './i18n/LanguageContext'
import AuthGate from './auth/AuthGate'
import { initializeMsal } from './auth/msal'

// MSAL must finish initialize() and handleRedirectPromise() BEFORE React
// renders. Two reasons, both of which produce baffling symptoms:
//   * MSAL v3+ throws on any call made before initialize();
//   * we may be mid-redirect back from Microsoft, and the auth code in the URL
//     has to be consumed before a component asks "am I signed in?" — otherwise
//     the answer is no and it bounces to Microsoft again, in a loop that looks
//     exactly like a broken Entra registration.
// A no-op when SSO is not configured, so the pre-SSO startup path is unchanged.
// .finally, not .then: if initialisation fails the app must still render and
// say so through AuthGate's error component, rather than leaving a blank page.
initializeMsal().finally(() => {
  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      <AuthGate>
        <LanguageProvider>
          <App />
        </LanguageProvider>
      </AuthGate>
    </StrictMode>,
  )
})
