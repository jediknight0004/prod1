import streamlit as st
import json
import os
import time
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed
from dotenv import load_dotenv

load_dotenv()

from swarm.orchestrator import run_orchestrator
from swarm.agents import (
    run_eligibility_agent,
    run_coding_agent,
    run_authorization_agent,
    run_timely_filing_agent,
    run_payer_policy_agent,
)
from swarm.reconciler import run_reconciler
from swarm.appeal import run_appeal_agent

st.set_page_config(
    page_title="Denials Analysis Swarm",
    page_icon="🏥",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
[data-testid="stMetricValue"] { font-size: 1.1rem; }
.swarm-header { background: linear-gradient(90deg, #1a3c5e, #2d6a9f); color: white; padding: 20px 24px; border-radius: 10px; margin-bottom: 16px; }
.agent-running { opacity: 0.6; }
</style>
""", unsafe_allow_html=True)


@st.cache_data
def load_sample_denials():
    path = os.path.join(os.path.dirname(__file__), "data", "sample_denials.json")
    with open(path) as f:
        return json.load(f)


def severity_color(severity: str) -> str:
    return {"HIGH": "🔴", "MEDIUM": "🟡", "LOW": "🟢"}.get(severity, "⚪")


def render_agent_result(col, name: str, icon: str, result: dict):
    with col:
        issue = result.get("issue_found", False)
        conf = result.get("confidence", 0)
        elapsed = result.get("elapsed_seconds", 0)
        sev = result.get("severity", "LOW")

        with st.container(border=True):
            if issue:
                st.markdown(f"**{icon} {name}**")
                st.error(f"⚠️ Issue Found", icon=None)
                st.caption(result.get("root_cause", ""))
            else:
                st.markdown(f"**{icon} {name}**")
                st.success("✅ Clear", icon=None)
                st.caption(str(result.get("evidence", ""))[:80])

            st.caption(f"{severity_color(sev)} {sev} | {conf*100:.0f}% conf | {elapsed}s")

            with st.expander("Details"):
                st.write(f"**Evidence:** {result.get('evidence', 'N/A')}")
                st.write(f"**Recommendation:** {result.get('recommendation', 'N/A')}")


def render_agent_placeholder(col, name: str, icon: str):
    with col:
        with st.container(border=True):
            st.markdown(f"**{icon} {name}**")
            st.caption("🔄 Analyzing in parallel...")
            st.caption("— | — | —")


def run_parallel_agents(denial: dict) -> list:
    fns = [
        run_eligibility_agent,
        run_coding_agent,
        run_authorization_agent,
        run_timely_filing_agent,
        run_payer_policy_agent,
    ]
    results = [None] * len(fns)
    with ThreadPoolExecutor(max_workers=5) as executor:
        future_to_idx = {executor.submit(fn, denial): i for i, fn in enumerate(fns)}
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            try:
                results[idx] = future.result()
            except Exception as e:
                results[idx] = {
                    "agent_name": f"Agent {idx}",
                    "issue_found": False,
                    "root_cause": None,
                    "confidence": 0,
                    "severity": "LOW",
                    "recommendation": f"Agent error: {str(e)[:100]}",
                    "evidence": "Error during analysis",
                    "elapsed_seconds": 0,
                }
    return results


def main():
    # Header
    st.markdown("""
    <div class="swarm-header">
        <h2 style="margin:0; color:white;">🏥 Denials Analysis Swarm</h2>
        <p style="margin:4px 0 0 0; color:#cce0ff; font-size:0.9rem;">
            Multi-agent AI orchestration — 5 specialist agents running in parallel
        </p>
    </div>
    """, unsafe_allow_html=True)

    # API key check
    if not os.getenv("ANTHROPIC_API_KEY"):
        st.error("ANTHROPIC_API_KEY not set. Copy .env.example to .env and add your key.")
        st.stop()

    # Sidebar
    with st.sidebar:
        st.header("⚙️ Data Source")
        data_source = st.radio("Input:", ["Sample Denials", "Upload CSV"])

        denials = load_sample_denials()

        if data_source == "Upload CSV":
            uploaded = st.file_uploader("Upload denials CSV", type=["csv"])
            if uploaded:
                try:
                    df = pd.read_csv(uploaded)
                    # Normalize list columns that may be stored as strings
                    for col in ["cpt_codes", "icd10_codes", "modifiers", "remark_codes"]:
                        if col in df.columns:
                            df[col] = df[col].apply(
                                lambda x: x.split(",") if isinstance(x, str) else []
                            )
                    denials = df.to_dict(orient="records")
                    st.success(f"✅ Loaded {len(denials)} records")
                except Exception as e:
                    st.error(f"Error reading CSV: {e}")

        st.divider()
        st.caption("🔒 **Privacy Notice**")
        st.caption(
            "This demo uses the Anthropic API. Your data is **not** used for model training "
            "and is encrypted in transit. No third-party vendors have access to uploaded data."
        )
        st.caption("API Policy: [anthropic.com/legal/privacy](https://www.anthropic.com/legal/privacy)")

    # Claim selector
    st.subheader("1️⃣ Select Denial Claim")
    denial_labels = {
        f"{d.get('claim_id')} — {d.get('denial_code')} — {d.get('payer_name')} — ${d.get('billed_amount', 0):,.0f}": i
        for i, d in enumerate(denials)
    }
    selected_label = st.selectbox("Choose a claim to analyze:", list(denial_labels.keys()))
    selected_idx = denial_labels[selected_label]
    denial = denials[selected_idx]

    # Claim summary strip
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Claim ID", denial.get("claim_id", "N/A"))
    c2.metric("Billed Amount", f"${denial.get('billed_amount', 0):,.0f}")
    c3.metric("Denial Code", denial.get("denial_code", "N/A"))
    c4.metric("Payer", str(denial.get("payer_name", ""))[:18])
    c5.metric("Date of Service", denial.get("date_of_service", "N/A"))

    st.caption(
        f"**CPT:** {', '.join(denial.get('cpt_codes', []))} | "
        f"**ICD-10:** {', '.join(denial.get('icd10_codes', []))} | "
        f"**Denial:** {denial.get('denial_description', 'N/A')}"
    )

    st.divider()

    # Run button
    run_clicked = st.button("▶️ Run Swarm Analysis", type="primary", use_container_width=True)

    if run_clicked:

        # ── Step 1: Orchestrator ──────────────────────────────────────────────
        st.subheader("2️⃣ Orchestrator")
        with st.status("Orchestrator decomposing denial...", expanded=True) as orch_status:
            orch = run_orchestrator(denial)
            orch_status.update(label="✅ Orchestrator complete — dispatching 5 parallel agents", state="complete")

        o1, o2, o3 = st.columns(3)
        o1.metric("Category", orch.get("denial_category", "N/A"))
        o2.metric("Risk Level", orch.get("risk_level", "N/A"))
        o3.metric("Key Concern", str(orch.get("key_concern", ""))[:35])
        st.info(f"**Orchestrator Summary:** {orch.get('denial_summary', '')}")
        st.caption(f"**Initial Hypothesis:** {orch.get('initial_hypothesis', '')}")

        st.divider()

        # ── Step 2: Parallel Agents ───────────────────────────────────────────
        st.subheader("3️⃣ Parallel Agent Swarm")

        AGENTS = [
            ("Eligibility", "👤"),
            ("Coding & Billing", "💊"),
            ("Prior Authorization", "📋"),
            ("Timely Filing", "⏰"),
            ("Payer Policy", "📜"),
        ]

        # Show placeholders while running
        agent_cols = st.columns(5)
        for col, (name, icon) in zip(agent_cols, AGENTS):
            render_agent_placeholder(col, name, icon)

        start_time = time.time()
        with st.spinner("5 specialist agents analyzing simultaneously..."):
            agent_results = run_parallel_agents(denial)
        total_time = round(time.time() - start_time, 1)

        # Replace placeholders with results using a fresh render pass
        for col, (name, icon), result in zip(agent_cols, AGENTS, agent_results):
            col.empty()

        agent_cols2 = st.columns(5)
        for col, (name, icon), result in zip(agent_cols2, AGENTS, agent_results):
            render_agent_result(col, name, icon, result)

        issues_found = sum(1 for r in agent_results if r and r.get("issue_found"))
        st.success(
            f"⚡ All 5 agents completed in **{total_time}s** (parallel execution) — "
            f"**{issues_found}/5** agents flagged issues"
        )

        st.divider()

        # ── Step 3: Reconciliation ────────────────────────────────────────────
        st.subheader("4️⃣ Reconciliation Agent")
        with st.status("Reconciling all agent findings...", expanded=True) as recon_status:
            recon = run_reconciler(denial, agent_results)
            recon_status.update(label="✅ Root cause identified", state="complete")

        r1, r2, r3, r4 = st.columns(4)
        r1.metric("Primary Agent", recon.get("primary_agent", "N/A"))
        r2.metric("Confidence", f"{recon.get('overall_confidence', 0)*100:.0f}%")
        r3.metric("Priority", recon.get("priority", "N/A"))
        r4.metric("Correction Path", recon.get("correction_type", "N/A"))

        st.error(f"🎯 **Root Cause:** {recon.get('primary_root_cause', 'N/A')}", icon=None)

        if recon.get("contributing_factors"):
            st.caption("**Contributing factors:** " + " | ".join(recon.get("contributing_factors")))

        correctable = recon.get("correctable", False)
        st.caption(f"**Correctable:** {'✅ Yes' if correctable else '❌ No — consider write-off'}")

        st.divider()

        # ── Step 4: Appeal Strategy ───────────────────────────────────────────
        st.subheader("5️⃣ Appeal Strategy Agent")
        with st.status("Generating appeal strategy and letter...", expanded=True) as appeal_status:
            appeal = run_appeal_agent(denial, recon)
            appeal_status.update(label="✅ Appeal strategy ready", state="complete")

        if appeal.get("appeal_recommended"):
            a1, a2, a3, a4 = st.columns(4)
            a1.metric("Appeal Type", appeal.get("appeal_type", "N/A"))
            a2.metric("Recovery Probability", f"{appeal.get('estimated_recovery_probability', 0)*100:.0f}%")
            a3.metric("File Within", f"{appeal.get('appeal_deadline_days', 'N/A')} days")
            a4.metric("Assign To", appeal.get("assigned_to", "N/A"))

            al, ar = st.columns(2)
            with al:
                st.markdown("**Key Arguments:**")
                for arg in appeal.get("key_arguments", []):
                    st.markdown(f"• {arg}")
            with ar:
                st.markdown("**Required Documentation:**")
                for doc in appeal.get("required_documentation", []):
                    st.markdown(f"• {doc}")

            with st.expander("📄 View Appeal Letter Draft", expanded=False):
                letter = appeal.get("appeal_letter", "")
                st.text_area("Appeal Letter", letter, height=320, label_visibility="collapsed")
                st.download_button(
                    label="⬇️ Download Appeal Letter",
                    data=letter,
                    file_name=f"appeal_{denial.get('claim_id', 'claim')}.txt",
                    mime="text/plain",
                )
        else:
            st.warning(
                f"Appeal not recommended: {appeal.get('reason_if_not', 'Based on root cause analysis, recovery unlikely.')}"
            )

        st.divider()

        # ── Final Summary ─────────────────────────────────────────────────────
        st.subheader("📊 Swarm Analysis Summary")
        s1, s2 = st.columns(2)

        with s1:
            st.markdown("**Claim & Denial**")
            st.markdown(f"""
| Field | Value |
|-------|-------|
| Claim ID | {denial.get('claim_id')} |
| Billed Amount | ${denial.get('billed_amount', 0):,.2f} |
| Payer | {denial.get('payer_name')} |
| Denial Code | {denial.get('denial_code')} |
| Root Cause | {str(recon.get('primary_root_cause', ''))[:60]} |
| Priority | {recon.get('priority')} |
""")

        with s2:
            st.markdown("**Swarm Performance & Outcome**")
            st.markdown(f"""
| Field | Value |
|-------|-------|
| Total Analysis Time | {total_time}s |
| Agents Run | 5 (parallel) |
| Issues Flagged | {issues_found}/5 tracks |
| Confidence | {recon.get('overall_confidence', 0)*100:.0f}% |
| Appeal Recommended | {'Yes' if appeal.get('appeal_recommended') else 'No'} |
| Est. Recovery | {appeal.get('estimated_recovery_probability', 0)*100:.0f}% |
| Assigned To | {appeal.get('assigned_to', 'N/A')} |
""")


if __name__ == "__main__":
    main()
