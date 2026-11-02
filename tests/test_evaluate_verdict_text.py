"""The verdict text says fewer or more tokens in plain words, never a signed percentage."""

from nexus_mcp.evaluate import quick


def _summary(grep_tokens, nexus_tokens):
    def side(tokens):
        return {"hit_at_1": 0.5, "hit_at_3": 0.8, "median_tokens": tokens, "median_ms": 1.0}

    return {"grep": side(grep_tokens), "nexus": side(nexus_tokens)}


def test_verdict_text_says_fewer_when_nexus_reads_less():
    reason = quick.verdict(_summary(4000, 1000), files=300)["reasons"][0]
    assert "75% fewer with Nexus-MCP" in reason


def test_verdict_text_says_more_when_nexus_reads_more():
    reason = quick.verdict(_summary(250, 650), files=300)["reasons"][0]
    assert "160% more with Nexus-MCP" in reason
