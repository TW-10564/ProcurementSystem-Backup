/**
 * An author/who/confirmed_by string, rendered as the person's Azure name.
 *
 * The database stores the EMAIL, because that is the only identifier that
 * survives a rename (see advisory/profiles.py). This component is the read
 * side of that decision: it resolves the email through the directory cache so
 * a buyer reads 「山田 太郎」 rather than `m_yamada@twave.co.jp`, and keeps the
 * email in the tooltip because that is the value actually recorded.
 *
 * Because the directory row refreshes at each person's next sign-in, renaming
 * someone in Azure updates their name on every historical record here — no
 * stored data changes, and nothing needs backfilling.
 *
 * Unresolvable authors (a colleague who has never signed in, or pre-SSO rows
 * written when the author was hand-typed) fall back to the raw string with the
 * domain stripped. Never to "unknown": knowing it was `m_yamada` is worth far
 * more in an audit trail than knowing nothing.
 */
import { useIdentity, useMyName } from "../auth/IdentityContext";

export default function AuthorName({
  author,
  fallback = "system",
}: {
  author?: string | null;
  fallback?: string;
}) {
  const { displayName, directory } = useIdentity();
  const raw = (author ?? "").trim();
  if (!raw) return <>{fallback}</>;

  const name = displayName(raw);
  const known = directory[raw.toLowerCase()];
  const title = known?.department ? `${raw} — ${known.department}` : raw;

  // No dotted underline when the name IS the raw string — a tooltip repeating
  // what is already on screen is just noise.
  return name === raw ? (
    <>{name}</>
  ) : (
    <span className="author-name" title={title}>
      {name}
    </span>
  );
}

/**
 * Initials for an avatar, from the resolved name rather than the email —
 * `m_yamada@twave.co.jp` yields "M_", which tells nobody anything.
 */
export function useAuthorInitials(): (author?: string | null) => string {
  const { displayName } = useIdentity();
  return (author) => {
    const name = displayName(author).trim();
    if (!name) return "?";
    const parts = name.split(/[\s　]+/).filter(Boolean);
    if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
    return name.slice(0, 2).toUpperCase();
  };
}

/**
 * The "who is doing this" chip on the compat-review and discontinuation
 * toolbars — avatar plus either the signed-in person's NAME (read-only) or,
 * pre-SSO, a box to type one.
 *
 * Under SSO it must not be editable. The server takes the author from the
 * verified token and discards whatever the client sends
 * (api/auth.py:effective_author), so an editable field would let someone type
 * a colleague's name and still have the record say themselves — a control that
 * lies about what it does is worse than no control.
 *
 * The NAME is displayed, but the email is what gets stored, so the email stays
 * in the tooltip: a buyer reads the name, and anyone auditing can see the key.
 */
export function AuthorChip({
  author,
  setAuthor,
  editable,
  label,
  placeholder,
}: {
  author: string;
  setAuthor: (v: string) => void;
  editable: boolean;
  label: string;
  placeholder: string;
}) {
  const initials = useAuthorInitials();
  const name = useMyName();

  return (
    <>
      <span className="avatar" title={author || label}>
        {editable ? (author ? author.slice(0, 2).toUpperCase() : "？") : initials(author)}
      </span>
      {editable ? (
        <input
          value={author}
          onChange={(e) => setAuthor(e.target.value)}
          placeholder={placeholder}
          aria-label={label}
          style={{ width: 180 }}
        />
      ) : (
        <span
          title={`${label}: ${author}`}
          style={{
            fontSize: "0.8rem",
            color: "#475569",
            maxWidth: 200,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {name || author}
        </span>
      )}
    </>
  );
}
