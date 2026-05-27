"""
BCon Dashboard — Fundo #2
Streamlit dashboard for the BCon Meta ad account.

Run:
    streamlit run bcon_dashboard.py
"""

import hmac
import os
from pathlib import Path
from datetime import date, timedelta

import streamlit as st
import pandas as pd

# ── Page config (must be first Streamlit call) ────────────────────────────────
st.set_page_config(
    page_title="BCon Dashboard — Fundo",
    page_icon="📈",
    layout="wide",
)

# ── Auth gate ─────────────────────────────────────────────────────────────────
def _check_credentials(username: str, password: str) -> bool:
    """Constant-time comparison to prevent timing attacks."""
    ok_user = hmac.compare_digest(username, st.secrets["auth"]["username"])
    ok_pass = hmac.compare_digest(password, st.secrets["auth"]["password"])
    return ok_user and ok_pass


def _show_login() -> None:
    """Center a login form and handle submission."""
    _, col, _ = st.columns([1, 1.4, 1])
    with col:
        st.markdown("## 🔐 BCon Dashboard Login")
        with st.form("login_form"):
            username = st.text_input("Username")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Log in", use_container_width=True)
        if submitted:
            if _check_credentials(username, password):
                st.session_state["authenticated"] = True
                st.rerun()
            else:
                st.error("Incorrect username or password.")


if "authenticated" not in st.session_state:
    st.session_state["authenticated"] = False

if not st.session_state["authenticated"]:
    _show_login()
    st.stop()

# ── Debug error collector (shown only to logged-in users in sidebar) ──────────
_DEBUG_ERRORS: list = []

# ── Load credentials from st.secrets (Streamlit Cloud) or local .env ─────────
from facebook_business.api import FacebookAdsApi          # noqa: E402
from facebook_business.adobjects.adaccount import AdAccount  # noqa: E402
from google.cloud import bigquery                          # noqa: E402
from google.oauth2 import service_account                  # noqa: E402

def _get_meta_token() -> str:
    """Read Meta token from st.secrets (cloud) or local .env (dev)."""
    try:
        return st.secrets["meta"]["access_token"]
    except Exception as e:
        _DEBUG_ERRORS.append(f"Meta token load failed: {e}")
        _env_path = Path(__file__).parent.parent.parent / "99-meta/secrets/.env.fundo-marketing"
        if _env_path.exists():
            for _line in _env_path.read_text().splitlines():
                if _line.startswith("META_ACCESS_TOKEN="):
                    return _line.split("=", 1)[1].strip()
        raise RuntimeError("Meta access token not found in secrets or local .env")


def _get_bq_client() -> bigquery.Client:
    """Build a BigQuery client using service account secrets (cloud) or ADC (dev)."""
    try:
        sa_info = dict(st.secrets["gcp_service_account"])
        creds = service_account.Credentials.from_service_account_info(
            sa_info,
            scopes=["https://www.googleapis.com/auth/bigquery.readonly"],
        )
        return bigquery.Client(project=BQ_PROJECT, credentials=creds)
    except Exception as e:
        _DEBUG_ERRORS.append(f"BQ client fallback to ADC: {e}")
        return bigquery.Client(project=BQ_PROJECT)  # falls back to ADC locally


# ── Constants ─────────────────────────────────────────────────────────────────
META_TOKEN      = _get_meta_token()
BCON_ACCOUNT_ID = "act_1978609185832759"
BCON_CONV_EVENT = "complete_registration"
BQ_PROJECT      = "fundodata"
BQ_FUNNEL       = "fundodata.fundo_db.lead_funnel_v5"

# ── Formatters ────────────────────────────────────────────────────────────────
def fc(v):
    """Format as currency: $1,234"""
    return f"${v:,.0f}" if v is not None and not (isinstance(v, float) and pd.isna(v)) else "—"


def fp(v):
    """Format as percentage: 1.23%"""
    return f"{v:.2f}%" if v is not None and not (isinstance(v, float) and pd.isna(v)) else "—"


def fn(v):
    """Format as integer with commas: 1,234"""
    return f"{v:,.0f}" if v is not None and not (isinstance(v, float) and pd.isna(v)) else "—"


# ── Meta helpers ──────────────────────────────────────────────────────────────
def _extract_action(actions, action_type: str) -> float:
    """Extract the value for a specific action_type from the Meta actions list."""
    if not isinstance(actions, list):
        return 0.0
    for a in actions:
        if a.get("action_type") == action_type:
            return float(a.get("value", 0))
    return 0.0


def _extract_cpa(cpa_list, action_type: str):
    """Extract the cost_per_action value for a specific action_type."""
    if not isinstance(cpa_list, list):
        return None
    for a in cpa_list:
        if a.get("action_type") == action_type:
            v = a.get("value")
            return float(v) if v else None
    return None


def _to_float(val, default=0.0) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _to_int(val, default=0) -> int:
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


# ── Totals row helper ─────────────────────────────────────────────────────────
def _add_totals(df: pd.DataFrame, label_col: str = "Campaign") -> pd.DataFrame:
    """
    Append a TOTALS row to a display DataFrame.

    - Numeric cols are summed, EXCEPT:
      - Frequency: averaged
      - CTR: recalculated from Clicks / Impressions
      - CPA: recalculated from Spend / Conversions
      - CPFA: recalculated from Spend / Funded
      - Approval Rate / Funded Rate: recalculated from component totals
    """
    if df.empty:
        return df

    total: dict = {label_col: "TOTAL / AVG"}

    # Sum numeric cols generically, then overwrite derived metrics
    numeric_cols = df.select_dtypes("number").columns
    for col in numeric_cols:
        if col in ("Frequency",):
            total[col] = df[col].mean()
        elif col in ("CTR", "CPA", "CPFA", "Approval Rate", "Funded Rate"):
            total[col] = None  # Overwritten below
        else:
            total[col] = df[col].sum(skipna=True)

    # Derived recalculations
    impr   = total.get("Impressions", 0) or 0
    clicks = total.get("Clicks", 0) or 0
    conv   = total.get("Conversions", 0) or 0
    spend  = total.get("Spend", 0) or 0
    funded = total.get("Funded", 0) or 0
    leads  = total.get("Leads", 0) or 0
    approved = total.get("Approved", 0) or 0
    funded_count = total.get("Funded", 0) or 0

    if "CTR" in df.columns:
        total["CTR"] = (clicks / impr * 100) if impr else 0.0
    if "CPA" in df.columns:
        total["CPA"] = (spend / conv) if conv else None
    if "CPFA" in df.columns:
        total["CPFA"] = (spend / funded) if funded else None
    if "Approval Rate" in df.columns:
        total["Approval Rate"] = (approved / leads) if leads else 0.0
    if "Funded Rate" in df.columns:
        total["Funded Rate"] = (funded_count / leads) if leads else 0.0

    # Pass through non-numeric label cols that might exist
    for col in df.columns:
        if col not in total:
            total[col] = "" if df[col].dtype == object else None

    return pd.concat([df, pd.DataFrame([total])], ignore_index=True)


# ── Meta API calls ────────────────────────────────────────────────────────────
@st.cache_data(ttl=300, show_spinner=False)
def get_bcon_summary(d_since, d_until) -> pd.DataFrame:
    """Account-level summary: single aggregated row for the KPI header."""
    FacebookAdsApi.init(access_token=META_TOKEN)
    account = AdAccount(BCON_ACCOUNT_ID)
    params = {
        "time_range": {"since": str(d_since), "until": str(d_until)},
        "level":      "account",
        "fields":     [
            "spend", "impressions", "clicks", "ctr",
            "reach", "frequency", "actions", "cost_per_action_type",
        ],
    }
    try:
        results = list(account.get_insights(params=params))
    except Exception as e:
        _DEBUG_ERRORS.append(f"Meta summary: {e}")
        st.error("Unable to load summary data. Please try again later.")
        return pd.DataFrame()

    if not results:
        return pd.DataFrame()

    d = dict(results[0])
    actions  = d.get("actions", [])
    cpa_list = d.get("cost_per_action_type", [])
    return pd.DataFrame([{
        "Spend":       _to_float(d.get("spend")),
        "Impressions": _to_int(d.get("impressions")),
        "Clicks":      _to_int(d.get("clicks")),
        "CTR":         _to_float(d.get("ctr")),
        "Frequency":   _to_float(d.get("frequency")),
        "LPV":         _to_int(_extract_action(actions, "landing_page_view")),
        "Conversions": _extract_action(actions, BCON_CONV_EVENT),
        "CPA":         _extract_cpa(cpa_list, BCON_CONV_EVENT),
    }])


@st.cache_data(ttl=300, show_spinner=False)
def get_bcon_daily(d_since, d_until) -> pd.DataFrame:
    """Daily time-series at account level for the trend charts."""
    FacebookAdsApi.init(access_token=META_TOKEN)
    account = AdAccount(BCON_ACCOUNT_ID)
    params = {
        "time_range":      {"since": str(d_since), "until": str(d_until)},
        "time_increment":  1,
        "level":           "account",
        "fields":          [
            "date_start", "spend", "impressions", "clicks", "actions",
        ],
    }
    rows = []
    try:
        for row in account.get_insights(params=params):
            d = dict(row)
            actions = d.get("actions", [])
            rows.append({
                "Date":        d.get("date_start", ""),
                "Spend":       _to_float(d.get("spend")),
                "Impressions": _to_int(d.get("impressions")),
                "Clicks":      _to_int(d.get("clicks")),
                "Conversions": _extract_action(actions, BCON_CONV_EVENT),
            })
    except Exception as e:
        _DEBUG_ERRORS.append(f"Meta daily: {e}")
        st.error("Unable to load daily trend data. Please try again later.")
    return pd.DataFrame(rows)


@st.cache_data(ttl=300, show_spinner=False)
def get_bcon_campaigns(d_since, d_until) -> pd.DataFrame:
    """Campaign-level Meta insights."""
    FacebookAdsApi.init(access_token=META_TOKEN)
    account = AdAccount(BCON_ACCOUNT_ID)
    params = {
        "time_range": {"since": str(d_since), "until": str(d_until)},
        "level":      "campaign",
        "fields":     [
            "campaign_name", "campaign_id",
            "spend", "impressions", "clicks", "ctr",
            "reach", "frequency", "actions", "cost_per_action_type",
        ],
    }
    rows = []
    try:
        for row in account.get_insights(params=params):
            d = dict(row)
            actions  = d.get("actions", [])
            cpa_list = d.get("cost_per_action_type", [])
            rows.append({
                "campaign_id": d.get("campaign_id", ""),
                "Campaign":   d.get("campaign_name", ""),
                "Spend":       _to_float(d.get("spend")),
                "Impressions": _to_int(d.get("impressions")),
                "Clicks":      _to_int(d.get("clicks")),
                "CTR":         _to_float(d.get("ctr")),
                "Frequency":   _to_float(d.get("frequency")),
                "LPV":         _to_int(_extract_action(actions, "landing_page_view")),
                "Conversions": _extract_action(actions, BCON_CONV_EVENT),
                "CPA":         _extract_cpa(cpa_list, BCON_CONV_EVENT),
            })
    except Exception as e:
        _DEBUG_ERRORS.append(f"Meta campaigns: {e}")
        st.error("Unable to load campaign data. Please try again later.")
    return pd.DataFrame(rows)


@st.cache_data(ttl=300, show_spinner=False)
def get_bcon_adsets(d_since, d_until) -> pd.DataFrame:
    """Ad-set-level Meta insights."""
    FacebookAdsApi.init(access_token=META_TOKEN)
    account = AdAccount(BCON_ACCOUNT_ID)
    params = {
        "time_range": {"since": str(d_since), "until": str(d_until)},
        "level":      "adset",
        "fields":     [
            "campaign_name", "campaign_id", "adset_name", "adset_id",
            "spend", "impressions", "clicks", "ctr",
            "reach", "frequency", "actions", "cost_per_action_type",
        ],
    }
    rows = []
    try:
        for row in account.get_insights(params=params):
            d = dict(row)
            actions  = d.get("actions", [])
            cpa_list = d.get("cost_per_action_type", [])
            rows.append({
                "campaign_id": d.get("campaign_id", ""),
                "Campaign":   d.get("campaign_name", ""),
                "adset_id":   d.get("adset_id", ""),
                "Ad Set":     d.get("adset_name", ""),
                "Spend":       _to_float(d.get("spend")),
                "Impressions": _to_int(d.get("impressions")),
                "Clicks":      _to_int(d.get("clicks")),
                "CTR":         _to_float(d.get("ctr")),
                "Frequency":   _to_float(d.get("frequency")),
                "LPV":         _to_int(_extract_action(actions, "landing_page_view")),
                "Conversions": _extract_action(actions, BCON_CONV_EVENT),
                "CPA":         _extract_cpa(cpa_list, BCON_CONV_EVENT),
            })
    except Exception:
        st.error("Unable to load ad set data. Please try again later.")
    return pd.DataFrame(rows)


@st.cache_data(ttl=300, show_spinner=False)
def get_bcon_ads(d_since, d_until) -> pd.DataFrame:
    """Ad-level Meta insights."""
    FacebookAdsApi.init(access_token=META_TOKEN)
    account = AdAccount(BCON_ACCOUNT_ID)
    params = {
        "time_range": {"since": str(d_since), "until": str(d_until)},
        "level":      "ad",
        "fields":     [
            "campaign_name", "campaign_id", "adset_name", "adset_id",
            "ad_name", "ad_id",
            "spend", "impressions", "clicks", "ctr",
            "reach", "frequency", "actions", "cost_per_action_type",
        ],
    }
    rows = []
    try:
        for row in account.get_insights(params=params):
            d = dict(row)
            actions  = d.get("actions", [])
            cpa_list = d.get("cost_per_action_type", [])
            rows.append({
                "campaign_id": d.get("campaign_id", ""),
                "Campaign":   d.get("campaign_name", ""),
                "adset_id":   d.get("adset_id", ""),
                "Ad Set":     d.get("adset_name", ""),
                "ad_id":      d.get("ad_id", ""),
                "Ad":         d.get("ad_name", ""),
                "Spend":       _to_float(d.get("spend")),
                "Impressions": _to_int(d.get("impressions")),
                "Clicks":      _to_int(d.get("clicks")),
                "CTR":         _to_float(d.get("ctr")),
                "Frequency":   _to_float(d.get("frequency")),
                "LPV":         _to_int(_extract_action(actions, "landing_page_view")),
                "Conversions": _extract_action(actions, BCON_CONV_EVENT),
                "CPA":         _extract_cpa(cpa_list, BCON_CONV_EVENT),
            })
    except Exception:
        st.error("Unable to load ad data. Please try again later.")
    return pd.DataFrame(rows)


# ── BigQuery calls ────────────────────────────────────────────────────────────
@st.cache_data(ttl=600, show_spinner=False)
def get_bcon_campaign_funded(d_since, d_until) -> dict:
    """
    Returns {campaign_id_str: row} for BCon funded deals, attributed via UTMParameters.
    Filtered by date deal was FUNDED (fl.timestamp), not lead creation date.
    """
    try:
        client = _get_bq_client()
        query = f"""
            SELECT
                utm.UTMValue                        AS campaign_id,
                COUNT(DISTINCT fl.DisplayNumber)    AS funded,
                SUM(lo.ApprovedAmount)              AS funded_amount
            FROM `fundodata.fundo_db_export_live.funded_loans` fl
            JOIN `fundodata.fundo_db_export_live.Loans` lo
              ON fl.DisplayNumber = lo.DisplayNumber
            JOIN `fundodata.fundo_db_export.UTMParameters` utm
              ON lo.LeadId = utm.LeadId
            WHERE utm.UTMKey = 'UTM_CAMPAIGN'
              AND fl.lead_provider_name IN ('BCon', 'bcon')
              AND DATE(fl.timestamp) BETWEEN '{d_since}' AND '{d_until}'
            GROUP BY 1
        """
        df = client.query(query).to_dataframe()
        return {str(r["campaign_id"]): r for _, r in df.iterrows()}
    except Exception as e:
        _DEBUG_ERRORS.append(f"BQ funded attribution: {e}")
        st.warning("Funded attribution unavailable — data may be incomplete.")
        return {}


@st.cache_data(ttl=600, show_spinner=False)
def get_bcon_funnel(d_since, d_until):
    """
    BCon funnel data from lead_funnel_v5.
    Returns (DataFrame, error_string | None).
    """
    try:
        client = _get_bq_client()
        query = f"""
            SELECT
                lead_provider_name                                  AS channel,
                COUNT(*)                                            AS leads,
                SUM(flag_approved)                                  AS approved,
                SAFE_DIVIDE(SUM(flag_approved), COUNT(*))           AS approval_rate,
                SUM(flag_funded_in_the_period)                      AS funded,
                SAFE_DIVIDE(
                    SUM(flag_funded_in_the_period), COUNT(*)
                )                                                   AS funded_rate,
                SUM(amt_funded_in_the_period)                       AS funded_amount,
                AVG(ApprovedAmount)                                 AS avg_offer_amount
            FROM `{BQ_FUNNEL}`
            WHERE lead_created_date BETWEEN '{d_since}' AND '{d_until}'
              AND LOWER(lead_provider_name) = 'bcon'
            GROUP BY 1
            ORDER BY funded DESC
        """
        df = client.query(query).to_dataframe()
        return df, None
    except Exception:
        return pd.DataFrame(), "BigQuery query failed. Please try again later."


# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("📅 Date Range")
    preset = st.radio(
        "Preset",
        ["Today", "Yesterday", "Last 7 days", "Last 14 days", "Last 30 days", "Custom"],
        index=2,
    )
    today = date.today()
    if preset == "Today":
        d_since, d_until = today, today
    elif preset == "Yesterday":
        d_since = d_until = today - timedelta(1)
    elif preset == "Last 7 days":
        d_since, d_until = today - timedelta(7), today - timedelta(1)
    elif preset == "Last 14 days":
        d_since, d_until = today - timedelta(14), today - timedelta(1)
    elif preset == "Last 30 days":
        d_since, d_until = today - timedelta(30), today - timedelta(1)
    else:
        d_since = st.date_input("From", today - timedelta(7))
        d_until = st.date_input("To",   today - timedelta(1))

    st.caption(f"📆 {d_since} → {d_until}")
    st.divider()

    if st.button("🔄 Refresh data", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

    st.divider()
    st.caption("Meta token expires 2026-07-17")

    if st.button("🚪 Log out", use_container_width=True):
        st.session_state["authenticated"] = False
        st.rerun()

# ── Page header ───────────────────────────────────────────────────────────────
st.title("📈 BCon Dashboard — Fundo #2")
st.caption(f"Meta account: {BCON_ACCOUNT_ID} · Conv event: `{BCON_CONV_EVENT}`")

# ── Live diagnostic (runs every page load, not cached) ───────────────────────
_diag_errors = []
try:
    from facebook_business.api import FacebookAdsApi as _FBApi
    from facebook_business.adobjects.adaccount import AdAccount as _AdAcc
    _FBApi.init(access_token=META_TOKEN)
    # Must iterate cursor to trigger actual HTTP request
    list(_AdAcc(BCON_ACCOUNT_ID).get_insights(params={
        "time_range": {"since": str(d_since), "until": str(d_until)},
        "level": "account", "fields": ["spend"],
    }))
    _diag_errors.append("✅ Meta API: OK")
except Exception as _e:
    _diag_errors.append(f"❌ Meta API: {type(_e).__name__}: {_e}")

try:
    _bqc = _get_bq_client()
    list(_bqc.query("SELECT 1").result())
    _diag_errors.append("✅ BigQuery: OK")
except Exception as _e:
    _diag_errors.append(f"❌ BigQuery: {type(_e).__name__}: {_e}")

# Always show diagnostic so we know what's happening
with st.expander("🔧 Connection status", expanded=any("❌" in e for e in _diag_errors)):
    st.code("\n".join(_diag_errors))
    st.caption(f"Token prefix: {META_TOKEN[:20]}..." if META_TOKEN else "Token: MISSING")

if any("❌" in e for e in _diag_errors):
    st.stop()

# ── Fetch all data ────────────────────────────────────────────────────────────
with st.spinner("Pulling Meta data..."):
    summary_df  = get_bcon_summary(d_since, d_until)
    campaign_df = get_bcon_campaigns(d_since, d_until)
    daily_df    = get_bcon_daily(d_since, d_until)

with st.spinner("Pulling BQ data..."):
    camp_funded = get_bcon_campaign_funded(d_since, d_until)
    funnel_df, funnel_err = get_bcon_funnel(d_since, d_until)

# ── Derive account-level funded from BQ funnel ───────────────────────────────
def _bq_funded() -> int:
    if funnel_df.empty:
        return 0
    return int(funnel_df["funded"].sum())


def _bq_funded_amt() -> float:
    if funnel_df.empty:
        return 0.0
    return float(funnel_df["funded_amount"].sum())


total_funded = _bq_funded()
total_funded_amt = _bq_funded_amt()

# ── KPI header row (9 metrics) ────────────────────────────────────────────────
st.subheader("Account Summary")

if summary_df.empty:
    st.info("No Meta data for this date range.")
else:
    s = summary_df.iloc[0]
    total_spend = s["Spend"]
    total_conv  = s["Conversions"]
    conv_cpa    = (total_spend / total_conv) if total_conv else None
    cpfa        = (total_spend / total_funded) if total_funded else None

    k1, k2, k3, k4, k5, k6, k7, k8, k9 = st.columns(9)
    k1.metric("Spend",       fc(total_spend))
    k2.metric("Impressions", fn(s["Impressions"]))
    k3.metric("Clicks",      fn(s["Clicks"]))
    k4.metric("CTR",         fp(s["CTR"]))
    k5.metric("LPV",         fn(s["LPV"]))
    k6.metric("Conversions", fn(total_conv))
    k7.metric("Conv CPA",    fc(conv_cpa))
    k8.metric("Funded",      fn(total_funded))
    k9.metric("CPFA",        fc(cpfa))

st.divider()

# ── Daily trend charts ────────────────────────────────────────────────────────
st.subheader("Daily Trends")

if daily_df.empty:
    st.info("No daily trend data for this date range.")
else:
    daily_df = daily_df.set_index("Date").sort_index()
    c_left, c_right = st.columns(2)
    with c_left:
        st.markdown("**Daily Spend ($)**")
        st.line_chart(daily_df[["Spend"]])
    with c_right:
        st.markdown("**Daily Conversions**")
        st.line_chart(daily_df[["Conversions"]])

st.divider()

# ── Helper: attach funded to any level df proportionally ─────────────────────
def _attach_funded_proportional(df: pd.DataFrame, channel_funded: int) -> pd.DataFrame:
    """
    Allocate channel-level funded deals to rows proportionally by Conversions.
    Used for ad-set and ad level where UTM attribution is not available.
    """
    df = df.copy()
    total_conv = df["Conversions"].sum()
    if total_conv > 0:
        df["Funded"] = df["Conversions"].apply(
            lambda c: channel_funded * c / total_conv
        )
    else:
        df["Funded"] = 0.0
    df["CPFA"] = df.apply(
        lambda r: r["Spend"] / r["Funded"] if r["Funded"] > 0 else None, axis=1
    )
    return df


# ── Tabs ──────────────────────────────────────────────────────────────────────
tab_campaigns, tab_adsets, tab_ads, tab_funnel = st.tabs([
    "📊 Campaigns", "📁 Ad Sets", "🎯 Ads", "🔁 Funnel",
])

# ── Campaigns tab ─────────────────────────────────────────────────────────────
with tab_campaigns:
    if campaign_df.empty:
        st.info("No campaign data for this date range.")
    else:
        camp = campaign_df.copy()

        # UTM attribution for funded
        utm_total = sum(
            int(camp_funded.get(str(cid), {}).get("funded", 0) or 0)
            for cid in camp["campaign_id"]
        )
        if utm_total > 0:
            camp["Funded"] = camp["campaign_id"].apply(
                lambda cid: float(int(camp_funded.get(str(cid), {}).get("funded", 0) or 0))
            )
            camp["Funded $"] = camp["campaign_id"].apply(
                lambda cid: float(camp_funded.get(str(cid), {}).get("funded_amount", 0) or 0)
            )
            st.caption("Campaign CPFA via UTM attribution (funded_loans → Loans → UTMParameters)")
        else:
            # Proportional fallback
            t_conv = camp["Conversions"].sum()
            camp["Funded"] = camp["Conversions"].apply(
                lambda c: total_funded * c / t_conv if t_conv > 0 else 0.0
            )
            # Proportional funded $ using channel total
            camp["Funded $"] = camp["Conversions"].apply(
                lambda c: total_funded_amt * c / t_conv if t_conv > 0 else 0.0
            )
            st.caption(
                "Campaign CPFA estimated proportionally from conversions "
                "(UTMParameters export stale — refresh needed for exact attribution)"
            )

        camp["CPFA"] = camp.apply(
            lambda r: r["Spend"] / r["Funded"] if r["Funded"] > 0 else None, axis=1
        )

        display_cols = [
            "Campaign", "Spend", "Impressions", "Clicks", "CTR",
            "LPV", "Conversions", "CPA", "Funded", "Funded $", "CPFA", "Frequency",
        ]
        camp_display = camp[display_cols].sort_values("Spend", ascending=False)
        camp_display = _add_totals(camp_display, label_col="Campaign")

        st.dataframe(
            camp_display.style.format({
                "Spend":       "${:,.0f}",
                "Impressions": "{:,.0f}",
                "Clicks":      "{:,.0f}",
                "CTR":         "{:.2f}%",
                "LPV":         "{:,.0f}",
                "Conversions": "{:,.1f}",
                "CPA":         lambda v: fc(v),
                "Funded":      "{:.1f}",
                "Funded $":    "${:,.0f}",
                "CPFA":        lambda v: fc(v),
                "Frequency":   "{:.2f}",
            }),
            use_container_width=True,
            hide_index=True,
        )

# ── Ad Sets tab ───────────────────────────────────────────────────────────────
with tab_adsets:
    adset_df_raw = get_bcon_adsets(d_since, d_until)

    if adset_df_raw.empty:
        st.info("No ad set data for this date range.")
    else:
        # Campaign filter
        campaign_options = sorted(adset_df_raw["Campaign"].unique().tolist())
        selected_campaign_as = st.selectbox(
            "Filter by Campaign",
            ["All campaigns"] + campaign_options,
            key="adset_campaign_filter",
        )

        adset_df = adset_df_raw.copy()
        if selected_campaign_as != "All campaigns":
            adset_df = adset_df[adset_df["Campaign"] == selected_campaign_as]

        if adset_df.empty:
            st.info("No ad sets for the selected campaign.")
        else:
            adset_df = _attach_funded_proportional(adset_df, total_funded)

            display_cols = [
                "Campaign", "Ad Set", "Spend", "Impressions", "Clicks", "CTR",
                "LPV", "Conversions", "CPA", "Funded", "CPFA", "Frequency",
            ]
            adset_display = adset_df[display_cols].sort_values("Spend", ascending=False)
            adset_display = _add_totals(adset_display, label_col="Campaign")

            # Fix label column for totals row (multi-column label)
            adset_display.loc[adset_display.index[-1], "Ad Set"] = ""

            st.caption("Funded allocated proportionally from channel total by conversions share.")
            st.dataframe(
                adset_display.style.format({
                    "Spend":       "${:,.0f}",
                    "Impressions": "{:,.0f}",
                    "Clicks":      "{:,.0f}",
                    "CTR":         "{:.2f}%",
                    "LPV":         "{:,.0f}",
                    "Conversions": "{:,.1f}",
                    "CPA":         lambda v: fc(v),
                    "Funded":      "{:.1f}",
                    "CPFA":        lambda v: fc(v),
                    "Frequency":   "{:.2f}",
                }),
                use_container_width=True,
                hide_index=True,
            )

# ── Ads tab ───────────────────────────────────────────────────────────────────
with tab_ads:
    ads_df_raw = get_bcon_ads(d_since, d_until)

    if ads_df_raw.empty:
        st.info("No ad data for this date range.")
    else:
        # Campaign filter
        campaign_options_ads = sorted(ads_df_raw["Campaign"].unique().tolist())
        selected_campaign_ads = st.selectbox(
            "Filter by Campaign",
            ["All campaigns"] + campaign_options_ads,
            key="ads_campaign_filter",
        )

        ads_df = ads_df_raw.copy()
        if selected_campaign_ads != "All campaigns":
            ads_df = ads_df[ads_df["Campaign"] == selected_campaign_ads]

        # Ad Set filter (depends on campaign selection)
        adset_options_ads = sorted(ads_df["Ad Set"].unique().tolist())
        selected_adset_ads = st.selectbox(
            "Filter by Ad Set",
            ["All ad sets"] + adset_options_ads,
            key="ads_adset_filter",
        )
        if selected_adset_ads != "All ad sets":
            ads_df = ads_df[ads_df["Ad Set"] == selected_adset_ads]

        if ads_df.empty:
            st.info("No ads for the selected filters.")
        else:
            ads_df = _attach_funded_proportional(ads_df, total_funded)

            display_cols = [
                "Campaign", "Ad Set", "Ad", "Spend", "Impressions", "Clicks", "CTR",
                "LPV", "Conversions", "CPA", "Funded", "CPFA", "Frequency",
            ]
            ads_display = ads_df[display_cols].sort_values("Spend", ascending=False)
            ads_display = _add_totals(ads_display, label_col="Campaign")

            # Clear secondary label cols on totals row
            ads_display.loc[ads_display.index[-1], "Ad Set"] = ""
            ads_display.loc[ads_display.index[-1], "Ad"] = ""

            st.caption("Funded allocated proportionally from channel total by conversions share.")
            st.dataframe(
                ads_display.style.format({
                    "Spend":       "${:,.0f}",
                    "Impressions": "{:,.0f}",
                    "Clicks":      "{:,.0f}",
                    "CTR":         "{:.2f}%",
                    "LPV":         "{:,.0f}",
                    "Conversions": "{:,.1f}",
                    "CPA":         lambda v: fc(v),
                    "Funded":      "{:.1f}",
                    "CPFA":        lambda v: fc(v),
                    "Frequency":   "{:.2f}",
                }),
                use_container_width=True,
                hide_index=True,
            )

# ── Funnel tab ────────────────────────────────────────────────────────────────
with tab_funnel:
    st.caption(
        f"Source: `{BQ_FUNNEL}` · Filtered to BCon leads · "
        f"Date range: {d_since} → {d_until}"
    )

    if funnel_err:
        st.error("Unable to load funnel data. Please try again later.")
    elif funnel_df.empty:
        st.info("No BCon funnel data for this date range.")
    else:
        # Compute CPFA using total Meta spend from summary
        total_spend_for_cpfa = summary_df["Spend"].sum() if not summary_df.empty else 0.0

        funnel = funnel_df.copy()
        funnel["CPFA"] = funnel.apply(
            lambda r: total_spend_for_cpfa / r["funded"] if r["funded"] > 0 else None,
            axis=1,
        )

        display = funnel.rename(columns={
            "channel":          "Channel",
            "leads":            "Leads",
            "approved":         "Approved",
            "approval_rate":    "Approval Rate",
            "funded":           "Funded",
            "funded_rate":      "Funded Rate",
            "funded_amount":    "Funded $",
            "avg_offer_amount": "Avg Offer $",
        })

        # Totals row
        f_leads    = display["Leads"].sum()
        f_approved = display["Approved"].sum()
        f_funded   = display["Funded"].sum()
        funnel_total = {
            "Channel":       "TOTAL / AVG",
            "Leads":         f_leads,
            "Approved":      f_approved,
            "Approval Rate": (f_approved / f_leads) if f_leads else 0.0,
            "Funded":        f_funded,
            "Funded Rate":   (f_funded / f_leads) if f_leads else 0.0,
            "Funded $":      display["Funded $"].sum(),
            "Avg Offer $":   display["Avg Offer $"].mean(),
            "CPFA":          (total_spend_for_cpfa / f_funded) if f_funded else None,
        }
        display = pd.concat(
            [display, pd.DataFrame([funnel_total])], ignore_index=True
        )

        st.dataframe(
            display.style.format({
                "Leads":         "{:,.0f}",
                "Approved":      "{:,.0f}",
                "Approval Rate": "{:.1%}",
                "Funded":        "{:,.0f}",
                "Funded Rate":   "{:.1%}",
                "Funded $":      "${:,.0f}",
                "Avg Offer $":   "${:,.0f}",
                "CPFA":          lambda v: fc(v),
            }),
            use_container_width=True,
            hide_index=True,
        )
