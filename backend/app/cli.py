"""Owner account and maintenance: `uv run python -m app.cli <command>`.

There is no registration and no reset email; this CLI is the reset flow.
"""

import argparse
import getpass
import sys

from sqlalchemy import select

from app.access import hash_password
from app.db import _session_factory
from app.models import Owner, OwnerSession, Upload
from app.services import imports
from app.services.fit_import import FitParseError


def _password(given: str | None) -> str:
    if given:
        return given
    first = getpass.getpass("Password: ")
    if len(first) < 8:
        sys.exit("Use at least 8 characters.")
    if getpass.getpass("Again: ") != first:
        sys.exit("The passwords don't match.")
    return first


def create_owner(username: str, password: str | None) -> None:
    with _session_factory()() as db:
        if db.scalar(select(Owner)):
            sys.exit("An owner already exists; use set-password to change the password.")
        db.add(Owner(username=username, password_hash=hash_password(_password(password))))
        db.commit()
    print(f"Created owner {username}.")


def set_password(password: str | None) -> None:
    with _session_factory()() as db:
        owner = db.scalar(select(Owner))
        if owner is None:
            sys.exit("No owner yet; run create-owner first.")
        owner.password_hash = hash_password(_password(password))
        db.query(OwnerSession).delete()  # log out everywhere
        db.commit()
    print("Password changed; all sessions were logged out.")


def reimport() -> None:
    """Re-parse every stored original with the current parser."""
    with _session_factory()() as db:
        for upload in db.scalars(select(Upload).order_by(Upload.id)).all():
            try:
                imports.reimport(db, upload)
                print(f"upload {upload.id} ({upload.filename}): updated")
            except (FitParseError, imports.GroupingError) as exc:
                db.rollback()
                print(f"upload {upload.id} ({upload.filename}): failed: {exc}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create-owner")
    create.add_argument("--username", default="owner")
    create.add_argument("--password", help="omit to be prompted")
    change = commands.add_parser("set-password")
    change.add_argument("--password", help="omit to be prompted")
    commands.add_parser("reimport")
    args = parser.parse_args(argv)
    if args.command == "create-owner":
        create_owner(args.username, args.password)
    elif args.command == "set-password":
        set_password(args.password)
    else:
        reimport()


if __name__ == "__main__":
    main()
