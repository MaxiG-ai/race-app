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


def strava_unlink() -> None:
    """Turn the password login back on: the way back when Strava login is unavailable.
    Deletes the Strava data too, like disconnecting does (docs/features/strava.md#login)."""
    from app.services import strava

    with _session_factory()() as db:
        owner = db.scalar(select(Owner))
        if owner is None or owner.strava_athlete_id is None:
            sys.exit("No Strava athlete is linked.")
        if not strava.disconnect(db):
            print("Strava didn't confirm the revocation: remove this site under My Apps in")
            print("the athlete's Strava settings.")
        owner = db.scalar(select(Owner))
        owner.strava_athlete_id = None  # type: ignore[union-attr]
        db.query(OwnerSession).delete()
        db.commit()
    print("Strava is unlinked and its data deleted. Log in with the password; run set-password")
    print("first if this owner never had one.")


def strava_subscribe(callback_url: str) -> None:
    """Create Strava's one webhook subscription for this app (docs/features/strava.md#webhook).
    The site must be reachable at `callback_url` and STRAVA_WEBHOOK_VERIFY_TOKEN set: Strava
    calls it back to check, before this returns."""
    from app.config import get_settings
    from app.services import strava_api

    settings = get_settings()
    if not settings.strava_enabled or not settings.strava_webhook_verify_token:
        sys.exit("Set STRAVA_CLIENT_ID, STRAVA_CLIENT_SECRET and STRAVA_WEBHOOK_VERIFY_TOKEN.")
    response = strava_api.http(
        "POST",
        f"{settings.strava_api_base.rstrip('/')}/push_subscriptions",
        data={
            "client_id": settings.strava_client_id,
            "client_secret": settings.strava_client_secret,
            "callback_url": callback_url,
            "verify_token": settings.strava_webhook_verify_token,
        },
    )
    if response.status >= 400:
        sys.exit(f"Strava refused the subscription ({response.status}): {response.body}")
    print(f"Subscribed: {response.body}")


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
    commands.add_parser("strava-unlink")
    subscribe = commands.add_parser("strava-subscribe")
    subscribe.add_argument("callback_url", help="e.g. https://race.example.com/api/strava/webhook")
    args = parser.parse_args(argv)
    if args.command == "create-owner":
        create_owner(args.username, args.password)
    elif args.command == "set-password":
        set_password(args.password)
    elif args.command == "strava-unlink":
        strava_unlink()
    elif args.command == "strava-subscribe":
        strava_subscribe(args.callback_url)
    else:
        reimport()


if __name__ == "__main__":
    main()
