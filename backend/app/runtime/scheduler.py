"""F26 scheduler process boundary.

Scheduling behavior is migrated in F26.17. This module exists now so deployment topology does not
couple scheduled execution to the FastAPI process.
"""


def main() -> None:
    print("Aduoryn scheduler boundary ready; scheduler behavior is migrated in F26.17.")


if __name__ == "__main__":
    main()
