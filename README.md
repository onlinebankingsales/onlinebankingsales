# Field Data Capture

Streamlit portal for field employees to enter customer data and view only their
own submissions. Administrators can create and delete employee accounts, view
all records, and download the complete data set as an Excel workbook.

## Run locally

Install `uv`, then create the environment:

```sh
uv sync
```

Set credentials for the super administrator before starting the app. The
super-admin username is fixed to `352247`; the password must contain at least 8
characters.

```sh
export INITIAL_ADMIN_USERNAME="352247"
export INITIAL_ADMIN_PASSWORD="use-a-strong-password"
uv run streamlit run streamlit_app.py
```

The first launch stores a salted password hash in `records.db`. For an existing
database, the account named `352247` is treated as the super administrator and
the existing accounts and records are migrated in place. The environment
variables are only used to create the first super-admin account; changing them
later does not change the saved password. The database is kept beside the app
by default. Set `DATA_DB_PATH` to use a different location, such as a persistent
volume when deploying.

## Accounts and data

- Super administrator `352247` can create administrators and employees, manage
   every account, and view or download all records.
- Administrators can create and delete only the employees they created, and can
   view or download only those employees' records.
- Employees can view only their own records and cannot download records.
- Account creation requires a username, employee name, and initial password.
- Employee names are captured on each record from the registered account, not
   entered freely with each submission.
- Deleting an employee account prevents future sign-ins but retains their
   Deleting an administrator transfers their managed accounts and records to
   the super administrator.
- Passwords are stored as salted PBKDF2 hashes, not plaintext.

Use HTTPS when exposing the app beyond a trusted local environment, and keep a
protected backup of `records.db` because it contains the accounts and records.

## Deploy persistently

1. Create a managed PostgreSQL database (for example, in Supabase or Neon) and
   copy its PostgreSQL connection URL. Require SSL for the connection.
2. Before closing this Codespace, migrate its local accounts and records. Set
   `DATABASE_URL` in the terminal environment to the hosted database URL, then
   run:

   ```sh
   uv sync
   uv run python migrate_to_postgres.py
   ```

   The migration preserves account IDs, password hashes, ownership, and saved
   records. It refuses to overwrite a populated PostgreSQL database; it can
   replace a single bootstrap super-admin account if the hosted app was opened
   once before migration.
3. Push this project to a GitHub repository and create a Streamlit Community
   Cloud app using `streamlit_app.py` as the entrypoint. Select Python 3.12 in
   the app's advanced settings.
4. In the app's **Settings > Secrets**, set the same PostgreSQL URL and require
   hosted storage:

   ```toml
   DATABASE_URL = "postgresql://..."
   REQUIRE_DATABASE_URL = true
   INITIAL_ADMIN_USERNAME = "352247"
   INITIAL_ADMIN_PASSWORD = "set-the-existing-or-a-new-strong-password"
   ```

   `INITIAL_ADMIN_PASSWORD` is only used when the database does not already
   contain the super-admin account. Never commit the actual connection URL or
   password. The app will stop at startup if `REQUIRE_DATABASE_URL` is enabled
   but the hosted database URL is missing.

After deployment, the Streamlit Cloud app URL remains available independently
of this Codespace. Keep the managed database active and retain its backups.
