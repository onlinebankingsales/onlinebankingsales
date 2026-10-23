
# Field Data Capture

Streamlit portal for field employees to enter customer data and view only their
own submissions. Administrators can create and delete employee accounts, view
all records, and download the complete data set as an Excel workbook.

## Run locally

Install `uv`, then create the environment:

```sh
uv sync
```

For local development with SQLite, explicitly allow the local database and set
credentials for the super administrator before starting the app. The
super-admin username is fixed to `352247`; the password must contain at least 8
characters.

```sh
export INITIAL_ADMIN_USERNAME="352247"
export INITIAL_ADMIN_PASSWORD="use-a-strong-password"
export REQUIRE_DATABASE_URL="false"
uv run streamlit run streamlit_app.py
```

The first launch stores a salted password hash in a persistent local database
at `~/.field_operations/records.db` by default. For an existing database, the
account named `352247` is treated as the super administrator and the existing
accounts and records are migrated in place. The environment variables are only
used to create the first super-admin account; changing them later does not
change the saved password. Set `DATA_DB_PATH` to use a different location,
such as a persistent volume when deploying.

## Accounts and data

- Super administrator `352247` can create administrators and employees, manage
   every account, and view or download all records.
- Administrators can create and delete only the employees they created, and can
   view or download only those employees' records. They can also reset the
   passwords of those employees.
- Employees can view only their own records and cannot download records.
- Every account can change its own password after confirming its current
  password. From the Accounts tab, the super administrator can set a new
  password for any administrator or employee; administrators can reset
  passwords for employees they created.
- The dashboard's Interested customers count includes only entries marked
  Interested. The employee submission summary shows all-time entries,
  interested entries, and entries submitted today for each employee.
- Each customer entry records whether the customer is Interested or Not
  interested in the Remarks field. Any legacy or missing remark value is
  normalized to Not interested.
- Account creation requires a username, employee name, and initial password.
- Employee names are captured on each record from the registered account, not
   entered freely with each submission.
- Only administrators can delete accounts: administrators can delete employees
   they created, and the super administrator can delete any account except their
   own.
- Deleting an employee account prevents future sign-ins but does not delete
   their submitted records. Those records remain available to the responsible
   administrator and the super administrator.
- Deleting an administrator transfers their managed accounts and records to
   the super administrator.
- Account removal is intentionally non-destructive: the app marks the user as
   inactive instead of deleting their row or stored records. Removal requires an
   administrator to confirm it in the UI.
- Passwords are stored as salted PBKDF2 hashes, not plaintext.
- Changing or resetting a password updates only that account's password hash;
  it does not delete accounts or submitted records.

Use HTTPS when exposing the app beyond a trusted local environment. Before
SQLite account, password, or record changes, the app saves and verifies a
separate backup beside the database. These local backups are not automatically
pruned; copy them to protected durable storage. SQLite and its backups only
survive a reboot if the configured directory is on persistent storage.

The app requires `DATABASE_URL` by default and stops rather than silently
starting with an empty local database. For Streamlit Community Cloud or another
host with an ephemeral filesystem, configure `DATABASE_URL` to a managed
PostgreSQL database. Do not disable this check for hosted deployments: the host
may discard its filesystem on reboot or redeploy. Use the database provider's
backup/point-in-time recovery feature as additional protection.

To restore a SQLite backup, stop the app and copy the chosen
`records.db.backup-*.sqlite` file over the configured database path. To restore
a PostgreSQL backup, set `DATABASE_URL` to an empty PostgreSQL database (or one
containing only its initial bootstrap super-administrator account), then run
`uv run python migrate_to_postgres.py path/to/your-backup.sqlite`.

If you previously used the app's older `records.db` file beside
`streamlit_app.py`, first startup copies that database to
`~/.field_operations/records.db` and leaves the original file intact. If both
locations already contain data, startup stops rather than choosing one or
overwriting either; set `DATA_DB_PATH` to the database you intend to use.

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
   password. The app will stop at startup if the hosted database URL is
   missing.

After deployment, the Streamlit Cloud app URL remains available independently
of this Codespace. Keep the managed database active and retain its backups.
