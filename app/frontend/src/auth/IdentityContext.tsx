/**
 * One place that knows who is signed in, and what every author email means.
 *
 * Three sources, deliberately kept distinct:
 *   * `me`        — what the SERVER sees (`GET /me`). The authority on whether
 *                   writes are attributed to a person or landing anonymously.
 *   * Graph       — the browser's own directory record, forwarded to the
 *                   backend so colleagues can resolve this email to a name.
 *   * `directory` — email → name for everyone, so a list of adjustments reads
 *                   as people rather than mail aliases.
 *
 * Loaded once per session rather than per view: every page that shows an
 * `author` needs the same map, and re-fetching it per component would put a
 * request behind every table render.
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { api } from "../api/client";
import type { DirectoryUser, Me } from "../api/types";
import { fetchGraphProfile } from "./graph";
import { activeAccount, ssoEnabled } from "./msal";

interface IdentityState {
  me: Me | null;
  directory: Record<string, DirectoryUser>;
  loading: boolean;
  /**
   * How to render an author string. Falls back to the raw value, never to
   * "unknown": an email is worse to read than a name but far better than
   * losing which colleague it was.
   */
  displayName: (author?: string | null) => string;
}

const IdentityContext = createContext<IdentityState>({
  me: null,
  directory: {},
  loading: true,
  displayName: (a) => a ?? "",
});

/**
 * Turn a mail alias into something readable when the directory has no entry —
 * a colleague who has never signed in, or pre-SSO history written when the
 * author was a hand-typed string.
 *
 * `m_balamurukan@twave.co.jp` → `m_balamurukan`. Deliberately conservative: it
 * only strips the domain. Guessing at capitalisation or splitting on
 * underscores would invent a name, and an invented name in an audit trail is
 * worse than a plain alias.
 */
export function prettifyAuthor(author: string): string {
  const at = author.indexOf("@");
  return at > 0 ? author.slice(0, at) : author;
}

export function IdentityProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [directory, setDirectory] = useState<Record<string, DirectoryUser>>({});
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      // /me first: it also upserts this person server-side, so the directory
      // fetched immediately after already contains them. The reverse order
      // would leave a first-time user missing from their own colleague list
      // until a reload.
      const identity = await api.me().catch(() => null);
      setMe(identity);

      // Forward the Graph record only when there is a verified identity to
      // attach it to — with the shared key the backend has no email to key on
      // and would (correctly) refuse.
      if (identity?.authenticated && ssoEnabled) {
        const graph = await fetchGraphProfile();
        if (graph) {
          const saved = await api.updateMyProfile(graph).catch(() => null);
          if (saved) setMe((prev) => (prev ? { ...prev, profile: saved } : prev));
        }
      }

      const users = await api.usersDirectory().catch(() => [] as DirectoryUser[]);
      setDirectory(Object.fromEntries(users.map((u) => [u.email.toLowerCase(), u])));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const displayName = useCallback(
    (author?: string | null) => {
      const raw = (author ?? "").trim();
      if (!raw) return "";
      const hit = directory[raw.toLowerCase()];
      // The directory row is refreshed at that person's next sign-in, so a
      // rename in Azure reaches every historical record here without any of
      // the stored author keys changing.
      if (hit?.display_name) return hit.display_name;
      return prettifyAuthor(raw);
    },
    [directory],
  );

  const value = useMemo(
    () => ({ me, directory, loading, displayName }),
    [me, directory, loading, displayName],
  );

  return <IdentityContext.Provider value={value}>{children}</IdentityContext.Provider>;
}

export function useIdentity(): IdentityState {
  return useContext(IdentityContext);
}

/**
 * The best name we have for the signed-in user, in preference order:
 * the directory record, then the server's token claim, then MSAL's local
 * account, then the email. Used by the header and the profile page so they
 * can never disagree with each other.
 */
export function useMyName(): string {
  const { me } = useIdentity();
  const account = ssoEnabled ? activeAccount() : null;
  return (
    me?.profile?.display_name ||
    me?.name ||
    account?.name ||
    (me?.email ? prettifyAuthor(me.email) : "") ||
    (account?.username ? prettifyAuthor(account.username) : "")
  );
}
