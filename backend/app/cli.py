"""`python -m app.cli` — account management.

Registration is deliberately not open in v1 (§2): this CLI is the only path to
creating or resetting an account. Passwords are always prompted for
interactively (never taken as an argv value), so they never land in shell
history or a process listing.
"""

from __future__ import annotations

import argparse
import getpass
import sys

from sqlalchemy import select

from app.auth.password import hash_password
from app.db import SessionLocal
from app.models import User

_MIN_PASSWORD_LEN = 8


def _prompt_password(prompt: str = "Password: ") -> str:
    while True:
        password = getpass.getpass(prompt)
        if len(password) < _MIN_PASSWORD_LEN:
            print(f"Password must be at least {_MIN_PASSWORD_LEN} characters.", file=sys.stderr)
            continue
        confirm = getpass.getpass("Confirm password: ")
        if password != confirm:
            print("Passwords did not match. Try again.", file=sys.stderr)
            continue
        return password


def create_user(args: argparse.Namespace) -> None:
    password = _prompt_password()
    with SessionLocal() as db:
        existing = db.execute(select(User).where(User.email == args.email)).scalar_one_or_none()
        if existing is not None:
            print(f"A user with email {args.email!r} already exists.", file=sys.stderr)
            raise SystemExit(1)

        user = User(
            email=args.email,
            password_hash=hash_password(password),
            display_name=args.display_name,
        )
        db.add(user)
        db.commit()
        print(f"Created user {args.email!r} (id={user.id}).")


def reset_password(args: argparse.Namespace) -> None:
    password = _prompt_password("New password: ")
    with SessionLocal() as db:
        user = db.execute(select(User).where(User.email == args.email)).scalar_one_or_none()
        if user is None:
            print(f"No user with email {args.email!r}.", file=sys.stderr)
            raise SystemExit(1)

        user.password_hash = hash_password(password)
        db.commit()
        print(f"Password reset for {args.email!r}.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli",
        description=(
            "Account management. Registration is not open (§2) — this is the "
            "only way to create or reset an account."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser("create-user", help="Create a new user account.")
    create.add_argument("--email", required=True, help="Login email.")
    create.add_argument("--display-name", default=None, help="Optional display name.")
    create.set_defaults(func=create_user)

    reset = subparsers.add_parser("reset-password", help="Reset an existing user's password.")
    reset.add_argument("--email", required=True, help="Account email.")
    reset.set_defaults(func=reset_password)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
