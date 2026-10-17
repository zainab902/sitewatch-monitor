
import json
import os
import shutil
import subprocess
import tempfile
from datetime import datetime

import pandas as pd
import streamlit as st
from streamlit_autorefresh import st_autorefresh
from monitor import check_all_websites


# =========================================================
# CONFIGURATION
# =========================================================

st.set_page_config(
    page_title="SiteWatch | Uptime Monitor",
    page_icon="🌐",
    layout="wide",
    initial_sidebar_state="collapsed",
)

REGION = "eu-north-1"
LAMBDA_NAME = "sitewatch-monitor"

LOCAL_HISTORY = "monitoring_history.csv"
AWS_HISTORY = "aws_monitoring_history.csv"
INCIDENT_FILE = "incident_history.csv"

REFRESH_INTERVAL = 60_000


# =========================================================
# LIGHT DASHBOARD STYLING
# =========================================================

st.markdown(
    """
    <style>
    .stApp {
        background-color: #F5F7FB;
        color: #172033;
    }

    [data-testid="stHeader"] {
        background-color: #F5F7FB;
    }

    [data-testid="stSidebar"] {
        background-color: #FFFFFF;
    }

    [data-testid="stMetric"] {
        background-color: #FFFFFF;
        border: 1px solid #E2E8F0;
        padding: 18px;
        border-radius: 12px;
    }

    div[data-testid="stDataFrame"] {
        background-color: #FFFFFF;
        border-radius: 10px;
    }

    .sitewatch-subtitle {
        color: #64748B;
        margin-top: -12px;
        margin-bottom: 24px;
    }

    .sitewatch-section {
        color: #172033;
        font-weight: 650;
    }

    hr {
        border-color: #E2E8F0;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# =========================================================
# GENERAL HELPERS
# =========================================================

def now_text():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def load_csv(path):
    if not os.path.exists(path):
        return pd.DataFrame()

    try:
        return pd.read_csv(path)
    except (pd.errors.EmptyDataError, pd.errors.ParserError):
        return pd.DataFrame()


def append_csv(path, rows):
    """Append rows while preserving existing CSV records."""
    if rows is None or rows.empty:
        return

    old = load_csv(path)

    combined = pd.concat(
        [old, rows],
        ignore_index=True,
        sort=False,
    )

    combined.to_csv(path, index=False)


def normalize_results(results, source):
    """Standardize local and AWS results for consistent history."""
    results = results.copy()

    if "Website" not in results.columns:
        results["Website"] = "Unknown"

    if "Status" not in results.columns:
        results["Status"] = "DOWN"

    if "Checked At" not in results.columns:
        results["Checked At"] = now_text()

    if "Response Time (ms)" not in results.columns:
        results["Response Time (ms)"] = None

    if "HTTP Code" not in results.columns:
        results["HTTP Code"] = None

    if "Error" not in results.columns:
        results["Error"] = ""

    results["Status"] = (
        results["Status"].astype(str).str.upper()
    )

    results["Source"] = source

    return results


# =========================================================
# INCIDENT TRACKING
# =========================================================

def record_incidents(new_results, history_path, source):
    """
    Record status transitions for each source.
    Initial checks establish a baseline and do not create incidents.
    """
    old_history = load_csv(history_path)
    incidents = load_csv(INCIDENT_FILE)
    new_incidents = []

    if old_history.empty or not {
        "Website",
        "Status",
    }.issubset(old_history.columns):
        return

    for _, current in new_results.iterrows():
        website = current["Website"]
        new_status = str(current["Status"]).upper()

        if new_status not in ("UP", "DOWN"):
            continue

        previous = old_history[
            old_history["Website"] == website
        ]

        if previous.empty:
            continue

        old_status = str(
            previous.iloc[-1]["Status"]
        ).upper()

        if old_status not in ("UP", "DOWN"):
            continue

        if old_status == new_status:
            continue

        new_incidents.append({
            "Website": website,
            "Source": source,
            "Event": (
                "RECOVERED"
                if new_status == "UP"
                else "DOWN"
            ),
            "Previous Status": old_status,
            "New Status": new_status,
            "Detected At": current["Checked At"],
        })

    if new_incidents:
        append_csv(
            INCIDENT_FILE,
            pd.DataFrame(new_incidents),
        )


# =========================================================
# LOCAL MONITORING
# =========================================================

def run_local_check():
    results = pd.DataFrame(check_all_websites())

    if results.empty:
        raise RuntimeError("The local monitor returned no results.")

    results = normalize_results(results, "Local")

    record_incidents(
        results,
        LOCAL_HISTORY,
        "Local",
    )

    append_csv(LOCAL_HISTORY, results)

    return results


# =========================================================
# AWS LAMBDA MONITORING
# =========================================================

def find_aws_cli():
    executable = shutil.which("aws")

    if executable:
        return executable

    windows_path = (
        r"C:\Program Files\Amazon\AWSCLIV2\aws.exe"
    )

    if os.path.exists(windows_path):
        return windows_path

    raise RuntimeError(
        "AWS CLI was not found. Restart VS Code after installation."
    )


def invoke_aws_lambda():
    """
    Invoke the existing Lambda through the AWS CLI.
    Uses the current AWS CLI login and never embeds AWS keys.
    """
    aws_exe = find_aws_cli()
    response_path = None

    try:
        with tempfile.NamedTemporaryFile(
            suffix=".json",
            delete=False,
        ) as temp_file:
            response_path = temp_file.name

        command = [
            aws_exe,
            "lambda",
            "invoke",
            "--function-name",
            LAMBDA_NAME,
            "--region",
            REGION,
            "--cli-binary-format",
            "raw-in-base64-out",
            "--payload",
            "{}",
            "--no-cli-pager",
            response_path,
        ]

        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

        if completed.returncode != 0:
            raise RuntimeError(
                completed.stderr.strip()
                or completed.stdout.strip()
                or "AWS Lambda invocation failed."
            )

        metadata = json.loads(
            completed.stdout.strip() or "{}"
        )

        with open(
            response_path,
            "r",
            encoding="utf-8",
        ) as response_file:
            payload = json.load(response_file)

        if metadata.get("FunctionError"):
            raise RuntimeError(
                "Lambda returned a function error: "
                + json.dumps(payload)
            )

        if not isinstance(payload, dict):
            raise RuntimeError(
                "Lambda returned an unexpected response."
            )

        results = payload.get("results", [])

        if not results:
            raise RuntimeError(
                "Lambda returned no website results."
            )

        checked_at = now_text()
        rows = []

        for result in results:
            rows.append({
                "Website": result.get(
                    "website", "Unknown"
                ),
                "URL": result.get("website", ""),
                "Status": str(
                    result.get("status", "DOWN")
                ).upper(),
                "HTTP Code": result.get("http_code"),
                "Response Time (ms)": result.get(
                    "response_time_ms"
                ),
                "Error": result.get("error") or "",
                "Checked At": checked_at,
                "Source": "AWS Lambda",
            })

        aws_results = normalize_results(
            pd.DataFrame(rows),
            "AWS Lambda",
        )

        record_incidents(
            aws_results,
            AWS_HISTORY,
            "AWS Lambda",
        )

        append_csv(AWS_HISTORY, aws_results)

        return aws_results

    finally:
        if response_path and os.path.exists(response_path):
            os.remove(response_path)


# =========================================================
# HEADER
# =========================================================

st.title("🌐 SiteWatch")
st.markdown(
    '<p class="sitewatch-subtitle">'
    'Cloud Uptime & Incident Monitor'
    '</p>',
    unsafe_allow_html=True,
)

# =========================================================
# MONITORING CONTROLS
# =========================================================

st.subheader("Monitoring Controls")

control1, control2, control3 = st.columns(3)

with control1:
    local_button = st.button(
        "Check Websites Locally",
        use_container_width=True,
    )

with control2:
    aws_button = st.button(
        "Refresh AWS Results",
        use_container_width=True,
    )

with control3:
    auto_local = st.toggle(
        "Auto-check locally (60 sec)",
        value=False,
    )

if auto_local:
    refresh_count = st_autorefresh(
        interval=REFRESH_INTERVAL,
        key="sitewatch_local_autorefresh",
    )
else:
    refresh_count = 0

if "last_local_refresh" not in st.session_state:
    st.session_state["last_local_refresh"] = None

should_check_local = local_button or (
    auto_local
    and refresh_count
    != st.session_state["last_local_refresh"]
)

if should_check_local:
    try:
        run_local_check()
        st.session_state["last_local_refresh"] = refresh_count
        st.success("Local monitoring check completed.")
    except Exception as error:
        st.error(f"Local monitoring failed: {error}")


# =========================================================
# AWS: RUN ON FIRST SESSION OR MANUAL REFRESH
# =========================================================

if "aws_checked_this_session" not in st.session_state:
    st.session_state["aws_checked_this_session"] = False

if "aws_latest" not in st.session_state:
    st.session_state["aws_latest"] = pd.DataFrame()

if "aws_error" not in st.session_state:
    st.session_state["aws_error"] = None

should_check_aws = (
    aws_button
    or not st.session_state["aws_checked_this_session"]
)

if should_check_aws:
    with st.spinner("Checking websites through AWS Lambda..."):
        try:
            aws_results = invoke_aws_lambda()

            st.session_state["aws_latest"] = (
                aws_results.drop_duplicates(
                    subset=["Website"],
                    keep="last",
                )
            )
            st.session_state["aws_error"] = None

        except Exception as error:
            st.session_state["aws_error"] = str(error)

            # Fall back to previously saved AWS results.
            saved = load_csv(AWS_HISTORY)

            if not saved.empty and "Website" in saved.columns:
                st.session_state["aws_latest"] = (
                    saved.drop_duplicates(
                        subset=["Website"],
                        keep="last",
                    )
                )

        finally:
            st.session_state["aws_checked_this_session"] = True

if st.session_state["aws_error"]:
    st.warning(
        "Could not refresh AWS results. "
        "Previously saved results may still be shown.\n\n"
        + st.session_state["aws_error"]
    )


# =========================================================
# AWS DASHBOARD
# =========================================================

st.divider()
st.subheader("☁️ AWS Lambda Monitoring")

aws_latest = st.session_state["aws_latest"]

if aws_latest.empty:
    st.info(
        "AWS results will appear after a successful Lambda invocation."
    )

else:
    aws_latest = aws_latest.copy()
    aws_latest["Status"] = (
        aws_latest["Status"].astype(str).str.upper()
    )

    aws_up = int(aws_latest["Status"].eq("UP").sum())
    aws_down = int(aws_latest["Status"].eq("DOWN").sum())

    a1, a2, a3 = st.columns(3)
    a1.metric("AWS Websites", len(aws_latest))
    a2.metric("AWS Websites UP", aws_up)
    a3.metric("AWS Websites DOWN", aws_down)

    st.dataframe(
        aws_latest,
        use_container_width=True,
        hide_index=True,
    )


# =========================================================
# AWS STATISTICS AND CHARTS
# =========================================================

st.subheader("AWS Uptime Statistics")

aws_history = load_csv(AWS_HISTORY)

if not aws_history.empty and {
    "Website",
    "Status",
}.issubset(aws_history.columns):

    aws_history["Status"] = (
        aws_history["Status"].astype(str).str.upper()
    )

    aws_valid = aws_history[
        aws_history["Status"].isin(["UP", "DOWN"])
    ].copy()

    if not aws_valid.empty:
        aws_summary = []

        for website, group in aws_valid.groupby(
            "Website",
            sort=False,
        ):
            total = len(group)
            up_count = int(group["Status"].eq("UP").sum())
            down_count = int(group["Status"].eq("DOWN").sum())

            avg_response = None

            if "Response Time (ms)" in group.columns:
                times = pd.to_numeric(
                    group["Response Time (ms)"],
                    errors="coerce",
                ).dropna()

                if not times.empty:
                    avg_response = round(times.mean(), 2)

            current_rows = aws_valid[
                aws_valid["Website"] == website
            ]

            aws_summary.append({
                "Website": website,
                "Current Status": current_rows.iloc[-1]["Status"],
                "Uptime (%)": round(
                    up_count / total * 100, 2
                ),
                "UP Checks": up_count,
                "DOWN Checks": down_count,
                "Total Checks": total,
                "Average Response (ms)": avg_response,
            })

        st.dataframe(
            pd.DataFrame(aws_summary),
            use_container_width=True,
            hide_index=True,
        )

        aws_overall = (
            aws_valid["Status"].eq("UP").mean() * 100
        )

        st.metric(
            "Overall AWS Recorded Uptime",
            f"{aws_overall:.2f}%",
        )

        st.caption(
            "Calculated from recorded AWS checks, not continuous "
            "availability between checks."
        )

        # Readable response-time chart, limited to recent records.
        if {
            "Checked At",
            "Website",
            "Response Time (ms)",
        }.issubset(aws_valid.columns):

            chart = aws_valid.copy()

            chart["Checked At"] = pd.to_datetime(
                chart["Checked At"],
                errors="coerce",
            )
            chart["Response Time (ms)"] = pd.to_numeric(
                chart["Response Time (ms)"],
                errors="coerce",
            )

            chart = chart.dropna(
                subset=["Checked At", "Response Time (ms)"]
            )

            chart = chart.sort_values("Checked At")

            st.subheader("AWS Response-Time Trends")

            if not chart.empty:
                # Keep the most recent 100 records per website.
                chart = (
                    chart.groupby("Website", group_keys=False)
                    .tail(100)
                )

                chart_data = chart.pivot_table(
                    index="Checked At",
                    columns="Website",
                    values="Response Time (ms)",
                    aggfunc="last",
                ).sort_index()

                st.line_chart(
                    chart_data,
                    y_label="Response time (ms)",
                    x_label="Check time",
                )
            else:
                st.info("AWS response-time data is not available.")
    else:
        st.info("AWS history has no valid UP/DOWN records yet.")
else:
    st.info("AWS statistics will appear after successful checks.")


# =========================================================
# LOCAL UPTIME STATISTICS
# =========================================================

st.divider()
st.subheader("💻 Local Monitoring Statistics")

local_history = load_csv(LOCAL_HISTORY)

if not local_history.empty and {
    "Website",
    "Status",
}.issubset(local_history.columns):

    local_history["Status"] = (
        local_history["Status"].astype(str).str.upper()
    )

    local_valid = local_history[
        local_history["Status"].isin(["UP", "DOWN"])
    ].copy()

    if not local_valid.empty:
        local_latest = local_valid.drop_duplicates(
            subset=["Website"],
            keep="last",
        )

        local_up = int(local_latest["Status"].eq("UP").sum())
        local_down = int(local_latest["Status"].eq("DOWN").sum())

        overall_local_uptime = (
            local_valid["Status"].eq("UP").mean() * 100
        )

        l1, l2, l3, l4 = st.columns(4)
        l1.metric("Websites", len(local_latest))
        l2.metric("Currently UP", local_up)
        l3.metric("Currently DOWN", local_down)
        l4.metric("Recorded Checks", len(local_valid))

        st.metric(
            "Overall Local Recorded Uptime",
            f"{overall_local_uptime:.2f}%",
        )

        local_summary = []

        for website, group in local_valid.groupby(
            "Website",
            sort=False,
        ):
            total = len(group)
            up_count = int(group["Status"].eq("UP").sum())
            down_count = int(group["Status"].eq("DOWN").sum())

            avg_response = None

            if "Response Time (ms)" in group.columns:
                times = pd.to_numeric(
                    group["Response Time (ms)"],
                    errors="coerce",
                ).dropna()

                if not times.empty:
                    avg_response = round(times.mean(), 2)

            latest_rows = local_latest[
                local_latest["Website"] == website
            ]

            local_summary.append({
                "Website": website,
                "Current Status": latest_rows.iloc[-1]["Status"],
                "Uptime (%)": round(up_count / total * 100, 2),
                "UP Checks": up_count,
                "DOWN Checks": down_count,
                "Total Checks": total,
                "Average Response (ms)": avg_response,
            })

        st.dataframe(
            pd.DataFrame(local_summary),
            use_container_width=True,
            hide_index=True,
        )

        if {
            "Checked At",
            "Website",
            "Response Time (ms)",
        }.issubset(local_valid.columns):

            local_chart = local_valid.copy()

            local_chart["Checked At"] = pd.to_datetime(
                local_chart["Checked At"],
                errors="coerce",
            )
            local_chart["Response Time (ms)"] = pd.to_numeric(
                local_chart["Response Time (ms)"],
                errors="coerce",
            )

            local_chart = local_chart.dropna(
                subset=["Checked At", "Response Time (ms)"]
            ).sort_values("Checked At")

            st.subheader("Local Response-Time Trends")

            if not local_chart.empty:
                local_chart = (
                    local_chart.groupby(
                        "Website",
                        group_keys=False,
                    ).tail(100)
                )

                local_chart_data = local_chart.pivot_table(
                    index="Checked At",
                    columns="Website",
                    values="Response Time (ms)",
                    aggfunc="last",
                ).sort_index()

                st.line_chart(
                    local_chart_data,
                    y_label="Response time (ms)",
                    x_label="Check time",
                )
            else:
                st.info("Local response-time data is not available.")
else:
    st.info(
        "Click 'Check Websites Locally' to begin local monitoring."
    )


# =========================================================
# INCIDENT HISTORY: BOTH SOURCES
# =========================================================

st.divider()
st.subheader("🚨 Incident History")

incidents = load_csv(INCIDENT_FILE)

if incidents.empty:
    st.success("No status transitions recorded yet.")
else:
    if "Detected At" in incidents.columns:
        incidents = incidents.sort_values(
            "Detected At",
            ascending=False,
        )

    down_events = (
        incidents["Event"].astype(str).str.upper().eq("DOWN").sum()
        if "Event" in incidents.columns
        else 0
    )

    recovered_events = (
        incidents["Event"].astype(str).str.upper().eq("RECOVERED").sum()
        if "Event" in incidents.columns
        else 0
    )

    i1, i2, i3 = st.columns(3)
    i1.metric("Status Transitions", len(incidents))
    i2.metric("DOWN Events", int(down_events))
    i3.metric("RECOVERED Events", int(recovered_events))

    st.dataframe(
        incidents,
        use_container_width=True,
        hide_index=True,
    )

    st.caption(
        "Incidents are recorded when consecutive checks show a "
        "status transition. Failures between checks may be missed."
    )


# =========================================================
# HISTORY TABLES
# =========================================================

with st.expander("View full local monitoring history"):
    if local_history.empty:
        st.info("No local history recorded.")
    else:
        local_display = local_history.copy()

        if "Checked At" in local_display.columns:
            local_display = local_display.sort_values(
                "Checked At",
                ascending=False,
            )

        st.dataframe(
            local_display,
            use_container_width=True,
            hide_index=True,
        )

with st.expander("View full AWS monitoring history"):
    if aws_history.empty:
        st.info("No AWS history recorded.")
    else:
        aws_display = aws_history.copy()

        if "Checked At" in aws_display.columns:
            aws_display = aws_display.sort_values(
                "Checked At",
                ascending=False,
            )

        st.dataframe(
            aws_display,
            use_container_width=True,
            hide_index=True,
        )


# =========================================================
# FOOTER
# =========================================================

st.caption(
    "SiteWatch | Local checks run from your computer. AWS checks "
    "invoke the existing Lambda on dashboard startup or manual "
    "refresh. EventBridge Scheduler remains disabled. Local and "
    "AWS monitoring histories are stored separately."
)
