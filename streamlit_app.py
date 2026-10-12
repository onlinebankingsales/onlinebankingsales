import os
from datetime import date, datetime
from io import BytesIO

import pandas as pd
import streamlit as st
from streamlit.errors import StreamlitSecretNotFoundError

try:
    deployment_secrets = st.secrets
    for secret_name in (
        "DATABASE_URL",
        "INITIAL_ADMIN_USERNAME",
        "INITIAL_ADMIN_PASSWORD",
        "REQUIRE_DATABASE_URL",
    ):
        if secret_name not in os.environ and secret_name in deployment_secrets:
            os.environ[secret_name] = str(deployment_secrets[secret_name])
except StreamlitSecretNotFoundError:
    deployment_secrets = {}

from data_store import (
    DATABASE_URL,
    SUPER_ADMIN_USERNAME,
    add_record,
    authenticate,
    create_account,
    delete_user,
    get_admin_records,
    get_user,
    get_user_records,
    initialize_database,
    list_users,
)


def sign_in():
    user = authenticate(
        st.session_state["login_username"],
        st.session_state["login_password"],
    )
    if user:
        st.session_state["authenticated_user_id"] = user["id"]
        st.session_state.pop("login_error", None)
        st.session_state["login_password"] = ""
    else:
        st.session_state["login_error"] = "Invalid username or password."


def sign_out():
    st.session_state.pop("authenticated_user_id", None)
    st.session_state.pop("login_error", None)


st.set_page_config(
    page_title="Field Operations",
    page_icon="📋",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=Manrope:wght@500;600;700;800&display=swap');
    :root {
        --ink: #18322e;
        --muted: #637570;
        --green: #176c59;
        --green-dark: #124c40;
        --line: #dce6e1;
        --canvas: #f4f7f5;
        --coral: #bd5142;
    }
    html, body, [class*="css"] { font-family: 'DM Sans', sans-serif; }
    .stApp { background: var(--canvas); color: var(--ink); }
    [data-testid="stHeader"] { background: transparent; }
    [data-testid="stMainBlockContainer"] { padding-top: 2rem; padding-bottom: 3rem; }
    h1, h2, h3 { color: var(--ink); font-family: 'Manrope', sans-serif; }
    h1 { font-size: 2rem; font-weight: 750; }
    h2 { font-size: 1.45rem; font-weight: 700; }
    h3 { font-size: 1.12rem; font-weight: 700; }
    p, label, [data-testid="stCaptionContainer"] { color: var(--muted); }
    [data-testid="stSidebar"] { background: #183b34; }
    [data-testid="stSidebar"] * { color: #eef5f1; }
    [data-testid="stSidebar"] [data-testid="stButton"] button {
        color: #ffffff; background: var(--coral); border: 0;
    }
    [data-testid="stSidebar"] [data-testid="stButton"] button:hover {
        color: #ffffff; background: #963e33; border: 0;
    }
    [data-testid="stForm"] {
        background: #ffffff; border: 1px solid var(--line); border-radius: 9px;
        padding: 1.1rem 1.2rem;
    }
    div[data-testid="stMetric"] {
        background: #ffffff; border: 1px solid var(--line); border-radius: 8px;
        padding: 1rem 1.1rem;
    }
    div[data-testid="stMetricLabel"] { color: var(--muted); }
    div[data-testid="stMetricValue"] { color: var(--ink); font-family: 'Manrope', sans-serif; }
    div.stButton > button, div[data-testid="stFormSubmitButton"] button,
    div[data-testid="stDownloadButton"] button {
        border-radius: 6px; font-weight: 650; min-height: 2.7rem;
    }
    div.stButton > button[kind="primary"],
    div[data-testid="stFormSubmitButton"] button[kind="primary"] {
        background: var(--green); border-color: var(--green); color: white;
    }
    div.stButton > button:hover, div[data-testid="stFormSubmitButton"] button:hover {
        border-color: var(--green-dark); color: var(--green-dark);
    }
    [data-testid="stTabs"] [role="tab"] { font-weight: 650; }
    [data-testid="stDataFrame"] { border: 1px solid var(--line); border-radius: 7px; }
    .eyebrow {
        color: var(--green); font-size: .75rem; font-weight: 700;
        text-transform: uppercase;
    }
    @media (max-width: 640px) {
        [data-testid="stMainBlockContainer"] { padding: 1rem .9rem 2rem; }
        h1 { font-size: 1.65rem; }
        [data-testid="stMetric"] { padding: .75rem; }
    }
    </style>
    """,
    unsafe_allow_html=True,
)

if (
    os.environ.get("REQUIRE_DATABASE_URL", "").casefold() == "true"
    and not DATABASE_URL
):
    st.title("Database configuration required")
    st.error("Set DATABASE_URL in the app secrets before starting the hosted app.")
    st.stop()

if not initialize_database():
    st.title("Administrator setup required")
    st.error(
        f"Configure INITIAL_ADMIN_USERNAME as {SUPER_ADMIN_USERNAME} and set "
        "INITIAL_ADMIN_PASSWORD, then restart the app."
    )
    st.stop()

current_user_id = st.session_state.get("authenticated_user_id")
current_user = get_user(current_user_id) if current_user_id else None

if current_user is None:
    login_copy, login_form = st.columns([1.15, 0.85], gap="large")
    with login_copy:
        st.markdown('<p class="eyebrow">FIELD OPERATIONS</p>', unsafe_allow_html=True)
        st.title("Field data, in good hands.")
        st.caption("A secure workspace for daily field records.")
    with login_form:
        st.markdown("### Sign in")
        with st.form("login_form"):
            st.text_input("Username", key="login_username")
            st.text_input("Password", type="password", key="login_password")
            st.form_submit_button("Sign in", on_click=sign_in, width="stretch")
        if error := st.session_state.get("login_error"):
            st.error(error)
    st.stop()

is_super_admin = current_user["role"] == "super_admin"
is_admin = current_user["role"] in ("admin", "super_admin")
role_label = "Super administrator" if is_super_admin else "Administrator" if is_admin else "Employee"

st.sidebar.markdown('<p class="eyebrow">FIELD OPERATIONS</p>', unsafe_allow_html=True)
st.sidebar.markdown(f"### {current_user['employee_name']}")
st.sidebar.caption(role_label)
st.sidebar.button("Sign out", on_click=sign_out, width="stretch")

if is_admin:
    managed_users = list_users(current_user["id"])
    managed_records = get_admin_records(current_user["id"])
    employee_count = sum(user["role"] == "user" for user in managed_users)
    administrator_count = sum(user["role"] == "admin" for user in managed_users)
    records_df = pd.DataFrame(managed_records)
    acreage_total = records_df["Acres"].sum() if not records_df.empty else 0

    st.markdown(
        '<p class="eyebrow">ADMINISTRATION</p>', unsafe_allow_html=True
    )
    st.title("Operations overview" if is_super_admin else "Team overview")
    st.caption(
        "System-wide access" if is_super_admin else "Your accounts and their submitted records"
    )

    if notice := st.session_state.pop("admin_notice", None):
        st.success(notice)

    overview_tab, accounts_tab, records_tab = st.tabs(
        ["Overview", "Accounts", "Records"]
    )
    with overview_tab:
        metric_columns = st.columns(4 if is_super_admin else 3)
        metric_columns[0].metric("Employees", employee_count)
        if is_super_admin:
            metric_columns[1].metric("Administrators", administrator_count)
            metric_columns[2].metric("All records", len(managed_records))
            metric_columns[3].metric("Total acres", f"{acreage_total:,.2f}")
        else:
            metric_columns[1].metric("Submitted records", len(managed_records))
            metric_columns[2].metric("Total acres", f"{acreage_total:,.2f}")

        st.subheader("Latest submissions")
        if managed_records:
            st.dataframe(records_df.head(8), width="stretch", hide_index=True)
        else:
            st.info("No records have been submitted to this account yet.")

    with accounts_tab:
        create_column, accounts_column = st.columns([0.85, 1.15], gap="large")
        with create_column:
            st.subheader("Create account")
            with st.form("add_account_form", clear_on_submit=True):
                new_username = st.text_input("Username")
                new_employee_name = st.text_input("Employee name")
                if is_super_admin:
                    account_type = st.selectbox(
                        "Account type",
                        options=["Employee", "Administrator"],
                    )
                    account_role = {
                        "Employee": "user",
                        "Administrator": "admin",
                    }[account_type]
                else:
                    st.text_input("Account type", value="Employee", disabled=True)
                    account_role = "user"
                new_password = st.text_input("Initial password", type="password")
                add_submitted = st.form_submit_button(
                    "Create account", width="stretch"
                )

            if add_submitted:
                try:
                    create_account(
                        current_user["id"],
                        new_username,
                        new_employee_name,
                        new_password,
                        account_role,
                    )
                except ValueError as error:
                    st.error(str(error))
                else:
                    st.session_state["admin_notice"] = (
                        f"{account_type if is_super_admin else 'Employee'} account created."
                        if is_super_admin
                        else "Employee account created."
                    )
                    st.rerun()

        with accounts_column:
            st.subheader("Managed accounts")
            if managed_users:
                users_df = pd.DataFrame(managed_users)
                display_columns = ["username", "employee_name", "role", "created_at"]
                if is_super_admin:
                    display_columns.insert(3, "created_by_username")
                users_df = users_df[display_columns].rename(
                    columns={
                        "username": "Username",
                        "employee_name": "Employee name",
                        "created_by_username": "Created by",
                        "created_at": "Created at",
                        "role": "Access level",
                    }
                )
                users_df["Access level"] = users_df["Access level"].map(
                    {"user": "Employee", "admin": "Administrator", "super_admin": "Super administrator"}
                )
                st.dataframe(users_df, width="stretch", hide_index=True)
            else:
                st.info("No managed accounts yet.")

        deletable_users = [
            user for user in managed_users if user["id"] != current_user["id"]
        ]
        st.divider()
        st.subheader("Remove account")
        if deletable_users:
            users_by_id = {user["id"]: user for user in deletable_users}
            with st.form("delete_account_form", clear_on_submit=True):
                selected_user_id = st.selectbox(
                    "Account to remove",
                    options=list(users_by_id),
                    format_func=lambda user_id: (
                        f"{users_by_id[user_id]['employee_name']} · "
                        f"{users_by_id[user_id]['username']} · "
                        f"{users_by_id[user_id]['role'].replace('_', ' ').title()}"
                    ),
                )
                confirm_delete = st.checkbox("Confirm account removal")
                delete_submitted = st.form_submit_button(
                    "Delete account", width="stretch"
                )
            if delete_submitted:
                if not confirm_delete:
                    st.error("Confirm the removal before continuing.")
                else:
                    try:
                        delete_user(selected_user_id, current_user["id"])
                    except ValueError as error:
                        st.error(str(error))
                    else:
                        st.session_state["admin_notice"] = (
                            "Account removed. Its saved records were retained."
                        )
                        st.rerun()
        else:
            st.info("There are no other accounts in your scope.")

    with records_tab:
        st.subheader("All system records" if is_super_admin else "Team records")
        if managed_records:
            download_buffer = BytesIO()
            with pd.ExcelWriter(download_buffer, engine="openpyxl") as writer:
                records_df.to_excel(writer, sheet_name="Field Records", index=False)
            file_prefix = "all_field_records" if is_super_admin else "team_field_records"
            st.download_button(
                "Download Excel",
                data=download_buffer.getvalue(),
                file_name=f"{file_prefix}_{date.today().isoformat()}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                width="content",
            )
            st.dataframe(records_df, width="stretch", hide_index=True)
        else:
            st.info("No records are available in this scope.")
else:
    st.markdown('<p class="eyebrow">EMPLOYEE WORKSPACE</p>', unsafe_allow_html=True)
    st.title(f"Welcome, {current_user['employee_name']}")

    entry_tab, records_tab = st.tabs(["New entry", "My records"])
    with entry_tab:
        with st.form("data_entry_form", clear_on_submit=True):
            date_column, name_column = st.columns(2)
            with date_column:
                st.date_input("Entry date", value=date.today(), disabled=True)
            with name_column:
                st.text_input(
                    "Employee name",
                    value=current_user["employee_name"],
                    disabled=True,
                )

            customer_column, contact_column, acres_column = st.columns(3)
            with customer_column:
                customer_name = st.text_input("Customer name *")
            with contact_column:
                contact_number = st.text_input("Contact number *", max_chars=15)
            with acres_column:
                acres = st.number_input(
                    "Acres *",
                    min_value=0.0,
                    value=None,
                    step=0.01,
                    format="%.2f",
                )

            village_column, mandal_column, district_column = st.columns(3)
            with village_column:
                village = st.text_input("Village *")
            with mandal_column:
                mandal = st.text_input("Mandal *")
            with district_column:
                district = st.text_input("District *")
            submitted = st.form_submit_button("Save entry", width="stretch")

        if submitted:
            values = (
                customer_name,
                contact_number,
                village,
                mandal,
                district,
            )
            if not all(value.strip() for value in values) or acres is None:
                st.error("Complete all required fields before saving.")
            else:
                try:
                    add_record(
                        current_user["id"],
                        date.today().isoformat(),
                        datetime.now().strftime("%H:%M:%S"),
                        customer_name.strip(),
                        contact_number.strip(),
                        acres,
                        village.strip(),
                        mandal.strip(),
                        district.strip(),
                    )
                except ValueError as error:
                    st.error(str(error))
                else:
                    st.success("Entry saved.")

    with records_tab:
        own_records = get_user_records(current_user["id"])
        if own_records:
            st.dataframe(
                pd.DataFrame(own_records), width="stretch", hide_index=True
            )
        else:
            st.info("Your saved entries will appear here.")
