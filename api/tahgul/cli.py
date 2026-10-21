"""Admin commands: `tahgul create-owner`, `tahgul reset-totp`, `tahgul seed-demo`."""

import argparse
import getpass
import sys

from sqlalchemy import func, select

from tahgul.bootstrap import OwnerExists, create_owner
from tahgul.db import get_sessionmaker
from tahgul.models import User


def _create_owner(args: argparse.Namespace) -> int:
    password = args.password or getpass.getpass("Password (12+ characters): ")
    if not args.password and password != getpass.getpass("Repeat password: "):
        print("Passwords do not match.", file=sys.stderr)
        return 1
    with get_sessionmaker()() as db:
        try:
            user = create_owner(db, args.email, password)
        except (OwnerExists, ValueError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
    print(f"Owner account created for {user.email}. Turn on two-factor under Settings.")
    return 0


def _reset_totp(args: argparse.Namespace) -> int:
    """Recovery path if the authenticator is lost. Needs shell access to the host."""
    with get_sessionmaker()() as db:
        user = db.scalar(select(User).where(func.lower(User.email) == args.email.lower()))
        if user is None:
            print("No such user.", file=sys.stderr)
            return 1
        user.totp_enabled = False
        user.totp_secret = None
        user.totp_last_step = None
        db.commit()
    print("Two-factor turned off. Set it up again after logging in.")
    return 0


def _seed_demo(args: argparse.Namespace) -> int:
    from tahgul import demo

    try:
        made = demo.seed(force=args.force)
    except demo.DemoError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(
        "Demo data added: "
        + ", ".join(f"{v} {k.replace('_', ' ')}" for k, v in made.items() if k != "invoice")
    )
    if made.get("invoice"):
        print(f"Issued invoice {made['invoice']} for last month.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tahgul")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("create-owner", help="create the single owner account")
    p.add_argument("--email", required=True)
    p.add_argument("--password", help="omit to be prompted (recommended)")
    p.set_defaults(func=_create_owner)

    p = sub.add_parser("reset-totp", help="turn off two-factor for the owner")
    p.add_argument("--email", required=True)
    p.set_defaults(func=_reset_totp)

    p = sub.add_parser("seed-demo", help="fill an empty install with a few weeks of demo data")
    p.add_argument("--force", action="store_true", help="add it even if clients exist")
    p.set_defaults(func=_seed_demo)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
