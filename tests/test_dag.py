"""Tests for the Planner DAG schema."""

from __future__ import annotations

import pytest

from agentos.schemas import DAGNode, TaskDAG


def test_minimal_dag() -> None:
    dag = TaskDAG(
        task_id="t-1",
        nodes=[
            DAGNode(
                stage_id="research",
                agent="openclaw",
                brief_template="Research {topic}",
                expected_artifact_type="research_report",
            ),
        ],
    )
    assert dag.task_id == "t-1"
    assert len(dag.nodes) == 1
    assert dag.nodes[0].stage_id == "research"


def test_duplicate_stage_id_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate stage_id"):
        TaskDAG(
            task_id="t-1",
            nodes=[
                DAGNode(stage_id="same", agent="openclaw",
                        brief_template="x", expected_artifact_type="r"),
                DAGNode(stage_id="same", agent="claude",
                        brief_template="y", expected_artifact_type="r"),
            ],
        )


def test_missing_input_rejected() -> None:
    with pytest.raises(ValueError, match="missing inputs"):
        TaskDAG(
            task_id="t-1",
            nodes=[
                DAGNode(stage_id="research", agent="openclaw",
                        brief_template="x", expected_artifact_type="r"),
                DAGNode(
                    stage_id="code",
                    agent="codex",
                    brief_template="based on {research_summary}",
                    inputs=["research"],   # ok
                    expected_artifact_type="pr_diff",
                ),
                DAGNode(
                    stage_id="deploy",
                    agent="openclaw",
                    brief_template="ship {unknown_artifact}",  # unknown ref
                    inputs=["code", "ghost_stage"],            # ghost_stage missing
                    expected_artifact_type="deploy_log",
                ),
            ],
        )


def test_parallel_group_validated() -> None:
    with pytest.raises(ValueError, match="negative parallel_group"):
        TaskDAG(
            task_id="t-1",
            nodes=[
                DAGNode(
                    stage_id="r",
                    agent="openclaw",
                    brief_template="x",
                    expected_artifact_type="r",
                    parallel_group=-1,
                ),
            ],
        )


def test_tool_subset_round_trip() -> None:
    node = DAGNode(
        stage_id="plan",
        agent="openclaw",
        brief_template="draft a plan",
        expected_artifact_type="plan_doc",
        tool_subset=[],  # plan-only per ADR-0009
    )
    assert node.tool_subset == []


def test_dag_cache_key_on_node() -> None:
    node = DAGNode(
        stage_id="r",
        agent="openclaw",
        brief_template="x",
        expected_artifact_type="r",
        dag_cache_key="research:v1:openai-embedding",
    )
    assert node.dag_cache_key == "research:v1:openai-embedding"


def test_unknown_agent_warns_but_does_not_fail() -> None:
    import warnings

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        node = DAGNode(
            stage_id="custom",
            agent="my_custom_agent",
            brief_template="x",
            expected_artifact_type="r",
        )
    assert any("unknown agent" in str(w.message) for w in caught)
    assert node.agent == "my_custom_agent"