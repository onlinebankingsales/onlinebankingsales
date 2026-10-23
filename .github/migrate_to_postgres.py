import argparse

from data_store import migrate_sqlite_to_postgres


def main():
    parser = argparse.ArgumentParser(
        description="Copy users and records from a SQLite backup into PostgreSQL."
    )
    parser.add_argument(
        "source",
        help="Path to the SQLite database backup downloaded from the app.",
    )
    args = parser.parse_args()

    result = migrate_sqlite_to_postgres(args.source)
    print(
        f"Migration complete: {result['users']} users and "
        f"{result['records']} records copied."
    )


if __name__ == "__main__":
    main()

