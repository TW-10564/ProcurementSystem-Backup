"""Load the permissions roster from a CSV the departments actually send.

    cd app/backend
    python3 -m onefcst.access_roster --file ../../deploy/roster.csv            # dry run
    python3 -m onefcst.access_roster --file ../../deploy/roster.csv --commit

WHY A LOADER AND NOT THE ADMIN SCREEN
    The roster arrives as a table pasted from a mail — 19 people from 企画部 in
    one go, purchasing's 7–10 to follow. Typing that into a UI twice, once on
    this box and again on the DGX, is how a wrong address gets in and how the
    two environments drift. A file that is re-runnable makes the roster the
    artefact and the database a projection of it.

IDEMPOTENT, AND WRITES ONLY WHAT CHANGED
    Re-running with the same file is a no-op that reports "unchanged", so the
    same command is safe to run on the DGX after each departmental update.
    Rows already correct are left alone rather than deleted and reinserted,
    which keeps `created_at`/`granted_by` meaning what they say.

NEVER DELETES
    A name vanishing from the file does NOT revoke access — removal is
    deliberate and goes through `DELETE /access/{email}` or the admin screen.
    A truncated paste or a half-sent list would otherwise silently lock out a
    department, and "the file is shorter this time" is not evidence of intent.

CSV FORMAT (header required; 担当パーツ optional, purchasing only)
    氏名,メールアドレス,部署,権限,担当パーツ
    田中 太郎,t_tanaka@twave.co.jp,購買本部,購買,MEM;SSD
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from .api.permissions import ADMIN, PLANNING, PURCHASING, ROLES
from .db import get_session
from .models import AuditLog, UserAccess, UserScope

# The departments write the role in Japanese; accept both spellings so nobody
# has to be told to re-send a file over a word.
ROLE_ALIASES = {
    "管理者": ADMIN, "admin": ADMIN, "アドミン": ADMIN,
    "購買": PURCHASING, "purchasing": PURCHASING, "購買部": PURCHASING,
    "購買本部": PURCHASING,
    "企画": PLANNING, "planning": PLANNING, "企画部": PLANNING,
}

COLUMNS = {
    "name": ("氏名", "name", "Name"),
    "email": ("メールアドレス", "email", "Email", "mail"),
    "dept": ("部署", "department", "Department"),
    "role": ("権限", "role", "Role"),
    "scopes": ("担当パーツ", "scopes", "Scopes", "担当"),
}


def _pick(row: dict, key: str) -> str:
    for header in COLUMNS[key]:
        if header in row and row[header] is not None:
            return str(row[header]).strip()
    return ""

def _scope_keys() -> list[str]:
    from .ingestion.subfile_config import FILES
    return list(FILES.keys())


def parse(path: Path) -> tuple[list[dict], list[str]]:
    """Rows and problems. Every problem is reported, not just the first —
    a roster with three bad rows should take one round trip to fix, not three."""
    valid_scopes = set(_scope_keys())
    rows: list[dict] = []
    problems: list[str] = []
    seen: dict[str, int] = {}

    with path.open(encoding="utf-8-sig", newline="") as fh:
        for n, raw in enumerate(csv.DictReader(fh), start=2):   # 2 = after header
            email = _pick(raw, "email").lower()
            name = _pick(raw, "name")
            role_raw = _pick(raw, "role")
            if not email and not role_raw:
                continue                                        # blank filler row

            if "@" not in email:
                problems.append(f"line {n}: not an email address: {email!r}")
                continue
            if email in seen:
                problems.append(
                    f"line {n}: {email} already listed on line {seen[email]}")
                continue
            seen[email] = n

            role = ROLE_ALIASES.get(role_raw.lower(), ROLE_ALIASES.get(role_raw))
            if role not in ROLES:
                problems.append(
                    f"line {n}: {email} — unknown role {role_raw!r} "
                    f"(expected 管理者 / 購買 / 企画)")
                continue

            # Anything past the last named column. csv.DictReader collects
            # those under the None key, and they matter: a department that
            # writes `MEM,SSD` unquoted in a COMMA-separated file splits the
            # parts across extra columns, and reading only the named one would
            # silently grant MEM and drop SSD — the buyer then cannot touch
            # half their parts and it looks like a permissions bug. Folding
            # them back in is safe because every key is validated below.
            extras = [str(x) for x in (raw.get(None) or []) if x]
            scopes = [s.strip() for s in
                      ";".join([_pick(raw, "scopes"), *extras])
                      .replace("、", ";").replace(",", ";").replace("/", ";")
                      .split(";") if s.strip()]
            bad = [s for s in scopes if s not in valid_scopes]
            if bad:
                # A typo'd sub-file owns nothing, which looks exactly like a
                # permissions bug when the buyer is refused.
                problems.append(f"line {n}: {email} — unknown sub-file(s) {bad}")
                continue
            if role == PURCHASING and not scopes:
                problems.append(
                    f"line {n}: {email} is 購買 but has no 担当パーツ — they would "
                    f"be able to change nothing. Ask which sub-files they own.")
            if role != PURCHASING and scopes:
                problems.append(
                    f"line {n}: {email} is {role_raw} — 担当パーツ is ignored for "
                    f"this role (admin sees everything; planning is by category)")
                scopes = []

            rows.append({"email": email, "name": name, "role": role,
                         "dept": _pick(raw, "dept"), "scopes": scopes})
    return rows, problems


def apply(rows: list[dict], commit: bool, granted_by: str) -> dict:
    session = get_session()
    created = updated = unchanged = 0
    try:
        for r in rows:
            row = session.query(UserAccess).filter(
                UserAccess.email == r["email"]).first()
            have = {s.scope_key for s in session.query(UserScope).filter(
                UserScope.email == r["email"])}
            want = set(r["scopes"])
            note = " ".join(x for x in (r["dept"], r["name"]) if x) or None

            if row is None:
                created += 1
                verb = "create"
            elif row.role != r["role"] or have != want or row.note != note:
                updated += 1
                verb = "update"
            else:
                unchanged += 1
                continue

            print(f"  {verb:7} {r['email']:32} {r['role']:11}"
                  f" {'/'.join(sorted(want)) if want else ''}  {note or ''}")
            if not commit:
                continue

            if row is None:
                row = UserAccess(email=r["email"], granted_by=granted_by)
                session.add(row)
            row.role, row.note = r["role"], note
            session.query(UserScope).filter(
                UserScope.email == r["email"]).delete()
            for key in want:
                session.add(UserScope(email=r["email"], scope_key=key,
                                      granted_by=granted_by))
            session.add(AuditLog(who=granted_by,
                                 what=f"access {verb}d from roster: "
                                      f"{r['email']} -> {r['role']}"
                                      + (f" scopes={sorted(want)}" if want else "")))
        if commit:
            session.commit()
    finally:
        session.close()
    return {"created": created, "updated": updated, "unchanged": unchanged}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--file", required=True, type=Path)
    ap.add_argument("--commit", action="store_true",
                    help="actually write (default is a dry run)")
    ap.add_argument("--by", default="roster-import",
                    help="who to record as having granted the access")
    args = ap.parse_args()

    if not args.file.exists():
        raise SystemExit(f"no such file: {args.file}")

    rows, problems = parse(args.file)
    if problems:
        print("PROBLEMS — fix these first:")
        for p in problems:
            print(f"  ! {p}")
        print()

    by_role: dict[str, int] = {}
    for r in rows:
        by_role[r["role"]] = by_role.get(r["role"], 0) + 1
    print(f"{len(rows)} usable row(s): "
          + ", ".join(f"{k}={v}" for k, v in sorted(by_role.items())) + "\n")

    counts = apply(rows, commit=args.commit, granted_by=args.by)
    print(f"\ncreated={counts['created']} updated={counts['updated']} "
          f"unchanged={counts['unchanged']}")
    if not args.commit:
        print("DRY RUN — nothing written. Re-run with --commit to apply.")

    # A blocking problem is worth a non-zero exit so a scripted deploy stops.
    fatal = [p for p in problems if "unknown role" in p or "not an email" in p]
    return 1 if fatal else 0


if __name__ == "__main__":
    sys.exit(main())
