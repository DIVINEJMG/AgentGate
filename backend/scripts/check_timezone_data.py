"""Report availability of required timezone data without changing the system."""

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def main() -> int:
    missing = False
    for name in ("UTC", "Africa/Lagos"):
        try:
            ZoneInfo(name)
        except ZoneInfoNotFoundError:
            print(f"{name}: unavailable")
            missing = True
        else:
            print(f"{name}: available")
    return int(missing)


if __name__ == "__main__":
    raise SystemExit(main())
