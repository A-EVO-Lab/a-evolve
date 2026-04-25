"""Regression test: BEDROCK_RETRY_MAX_ATTEMPTS / BEDROCK_READ_TIMEOUT_SEC
env vars must propagate through every in-matrix BedrockModel construction
site to the underlying boto3 client config.

Sites covered (in-matrix per EvolverBench plan_v2 AC-6 audit table):
  - agents/swe/agent.py::SweAgent
  - agents/mcp/agent.py::McpAgent          (mcp evolve solver)
  - agents/mcp_mh/agent.py::McpMHAgent     (mcp baseline solver)
  - agents/terminal/agent.py::TerminalAgent (tb evolve solver)
  - agents/skillbench/agent.py::SkillBenchAgent (sb evolve solver)

Out-of-scope sites (NOT exercised by Exp1 v3 matrix):
  - algorithms/mas_adaptive_skill/orchestrator.py
  - agents/arc/strands_agent.py

The BedrockProvider path (sites 1 + sb-baseline) is covered by the existing
agent_evolve.llm.bedrock module-level construction tests.

Run via:  python tests/test_bedrock_config_propagation.py
"""
from __future__ import annotations

import os
import pathlib
import sys
import tempfile
from typing import Any

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def _get_boto_cfg(strands_agent: Any):
    """Locate the boto3 client config inside a strands `Agent`."""
    m = strands_agent.model
    for attr in ("client", "_client", "boto_client", "_boto_client"):
        c = getattr(m, attr, None)
        if c is not None and hasattr(c, "meta"):
            return c.meta.config
    raise AssertionError(
        f"could not locate boto3 client on strands BedrockModel "
        f"(type={type(m).__name__}, attrs={[a for a in dir(m) if not a.startswith('_')][:15]})"
    )


def _make_workspace() -> pathlib.Path:
    """Minimal fake workspace tree the agents accept at construction time."""
    ws = pathlib.Path(tempfile.mkdtemp(prefix="bedrock_cfg_test_"))
    for sub in ("skills", "memory", "prompts"):
        (ws / sub).mkdir()
    (ws / "prompts" / "system.md").write_text("# test")
    return ws


def _assert_default(cfg) -> None:
    """Default env: 15 retries (= total_max_attempts 16) + 600s read timeout."""
    assert cfg.retries == {"mode": "adaptive", "total_max_attempts": 16}, (
        f"default retries mismatch: {cfg.retries}"
    )
    assert cfg.read_timeout == 600, f"default read_timeout: {cfg.read_timeout}"


def _assert_override(cfg) -> None:
    """Env override: 20 retries (= total_max_attempts 21) + 900s read timeout."""
    assert cfg.retries == {"mode": "adaptive", "total_max_attempts": 21}, (
        f"override retries mismatch: {cfg.retries}"
    )
    assert cfg.read_timeout == 900, f"override read_timeout: {cfg.read_timeout}"


def _clear_env() -> None:
    for k in (
        "BEDROCK_RETRY_MAX_ATTEMPTS",
        "BEDROCK_READ_TIMEOUT_SEC",
        "BEDROCK_CONNECT_TIMEOUT_SEC",
    ):
        os.environ.pop(k, None)


def test_terminal_agent():
    """TerminalAgent → strands BedrockModel → boto3 client (site #6)."""
    from agent_evolve.agents.terminal.agent import TerminalAgent

    ws = _make_workspace()
    try:
        _clear_env()
        agent = TerminalAgent(workspace_dir=str(ws), model_id="test-model")
        _assert_default(_get_boto_cfg(agent._build_strands_agent()))

        os.environ["BEDROCK_RETRY_MAX_ATTEMPTS"] = "20"
        os.environ["BEDROCK_READ_TIMEOUT_SEC"] = "900"
        agent2 = TerminalAgent(workspace_dir=str(ws), model_id="test-model")
        _assert_override(_get_boto_cfg(agent2._build_strands_agent()))
    finally:
        import shutil
        shutil.rmtree(ws, ignore_errors=True)
        _clear_env()
    print("[OK] TerminalAgent (site #6, tb evolve solver)")


def test_swe_agent():
    """SweAgent → strands BedrockModel → boto3 client (site #2)."""
    from agent_evolve.agents.swe.agent import SweAgent

    ws = _make_workspace()
    try:
        _clear_env()
        agent = SweAgent(workspace_dir=str(ws), model_id="test-model")
        # SweAgent._build_strands_agent returns (agent, modules) — pop just agent
        result = agent._build_strands_agent()
        strands_agent = result[0] if isinstance(result, tuple) else result
        _assert_default(_get_boto_cfg(strands_agent))

        os.environ["BEDROCK_RETRY_MAX_ATTEMPTS"] = "20"
        os.environ["BEDROCK_READ_TIMEOUT_SEC"] = "900"
        agent2 = SweAgent(workspace_dir=str(ws), model_id="test-model")
        result2 = agent2._build_strands_agent()
        strands_agent2 = result2[0] if isinstance(result2, tuple) else result2
        _assert_override(_get_boto_cfg(strands_agent2))
    finally:
        import shutil
        shutil.rmtree(ws, ignore_errors=True)
        _clear_env()
    print("[OK] SweAgent (site #2, swe evolve solver)")


def test_skillbench_agent_construction():
    """SkillBenchAgent: verify the BedrockModel construction call carries
    boto_client_config=bedrock_boto_config(). Full instantiation requires
    a SkillBench task workspace which is heavy; we instead inspect the
    source to confirm the construction site is patched, then construct
    only the BedrockModel directly with the same args.
    """
    src = (REPO_ROOT / "agent_evolve" / "agents" / "skillbench" / "agent.py").read_text()
    assert "boto_client_config=bedrock_boto_config()" in src, (
        "SkillBenchAgent BedrockModel construction missing boto_client_config="
        "bedrock_boto_config() — site #7 patch regressed"
    )

    # Direct construction sanity: BedrockModel + bedrock_boto_config integration.
    from agent_evolve.llm._bedrock_config import bedrock_boto_config
    try:
        from strands.models import BedrockModel
    except ImportError:
        print("[SKIP] SkillBenchAgent (strands not available; source-grep PASSED)")
        return

    _clear_env()
    model = BedrockModel(
        model_id="test-model",
        region_name="us-west-2",
        max_tokens=1024,
        boto_client_config=bedrock_boto_config(),
    )
    cfg = _get_boto_cfg(type("Wrap", (), {"model": model})())
    _assert_default(cfg)

    os.environ["BEDROCK_RETRY_MAX_ATTEMPTS"] = "20"
    os.environ["BEDROCK_READ_TIMEOUT_SEC"] = "900"
    model2 = BedrockModel(
        model_id="test-model",
        region_name="us-west-2",
        max_tokens=1024,
        boto_client_config=bedrock_boto_config(),
    )
    cfg2 = _get_boto_cfg(type("Wrap", (), {"model": model2})())
    _assert_override(cfg2)
    _clear_env()
    print("[OK] SkillBenchAgent (site #7, sb evolve solver — source-grep + direct BedrockModel)")


def test_mcp_agent_construction():
    """McpAgent: same source-grep + direct BedrockModel approach as SkillBench
    (full McpAgent instantiation requires KeyRegistry + container plumbing).
    """
    src = (REPO_ROOT / "agent_evolve" / "agents" / "mcp" / "agent.py").read_text()
    assert "boto_client_config=bedrock_boto_config()" in src, (
        "McpAgent BedrockModel construction missing boto_client_config="
        "bedrock_boto_config() — site #5 patch regressed"
    )

    from agent_evolve.llm._bedrock_config import bedrock_boto_config
    try:
        from strands.models import BedrockModel
    except ImportError:
        print("[SKIP] McpAgent (strands not available; source-grep PASSED)")
        return

    _clear_env()
    model = BedrockModel(
        model_id="test-model",
        region_name="us-west-2",
        max_tokens=1024,
        boto_client_config=bedrock_boto_config(),
    )
    _assert_default(_get_boto_cfg(type("Wrap", (), {"model": model})())
                    )
    print("[OK] McpAgent (site #5, mcp evolve solver — source-grep + direct BedrockModel)")


def test_mcp_mh_agent_construction():
    """McpMHAgent (mcp baseline solver, site #3): source-grep — already
    patched in the prior round."""
    src = (REPO_ROOT / "agent_evolve" / "agents" / "mcp_mh" / "agent.py").read_text()
    assert "boto_client_config=bedrock_boto_config()" in src, (
        "McpMHAgent BedrockModel construction missing boto_client_config="
        "bedrock_boto_config() — site #3 patch regressed"
    )
    print("[OK] McpMHAgent (site #3, mcp baseline solver — source-grep)")


def test_out_of_scope_sites_unmodified():
    """The two out-of-scope sites must NOT carry boto_client_config — they
    are NOT exercised by the Exp1 matrix and were intentionally not patched
    (DEC-2 of plan_v2). Touching them would expand blast radius for no
    matrix benefit.
    """
    for path, name in [
        ("agent_evolve/algorithms/mas_adaptive_skill/orchestrator.py",
         "mas_adaptive_skill (site #8)"),
        ("agent_evolve/agents/arc/strands_agent.py", "ARC (site #9)"),
    ]:
        src = (REPO_ROOT / path).read_text()
        assert "boto_client_config=bedrock_boto_config()" not in src, (
            f"{name} unexpectedly carries the shared Bedrock config — that "
            f"path is out-of-scope per plan_v2 DEC-2; revert if accidental"
        )
    print("[OK] out-of-scope sites #8 (mas_adaptive_skill) and #9 (ARC) unmodified")


def main():
    test_terminal_agent()
    test_swe_agent()
    test_skillbench_agent_construction()
    test_mcp_agent_construction()
    test_mcp_mh_agent_construction()
    test_out_of_scope_sites_unmodified()
    print()
    print("All Bedrock-config propagation regression tests passed.")


if __name__ == "__main__":
    main()
