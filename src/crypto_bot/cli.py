import argparse
import json
import os
from pathlib import Path

from crypto_bot.config import ConfigurationError, load_settings


def main() -> None:
    parser = argparse.ArgumentParser(prog="cbot", description="Private Futures research bot")
    commands = parser.add_subparsers(dest="command", required=True)
    config = commands.add_parser("config").add_subparsers(dest="action", required=True)
    validate = config.add_parser("validate")
    validate.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    try:
        settings = load_settings(args.config, os.environ)
        print(json.dumps(settings.safe_dict(), indent=2))
    except ConfigurationError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
