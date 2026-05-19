# Deprecated Agent Files

**These files are DEPRECATED and should not be used.**

## Why Deprecated?

The original architecture used LLM-powered agents for each processing stage:
- `deadband_agent_sam.py` - LLM-based deadband filtering
- `sketch_agent_sam.py` - LLM-based sketch generation
- `anomaly_agent_sam.py` - LLM-based anomaly detection
- `fleet_agent_sam.py` - LLM-based fleet monitoring

**Problem:** This approach triggered an LLM call for every sensor reading, making it:
- Extremely expensive ($3.1M/year for 100 sensors)
- Slow (LLM latency in the ingestion path)
- Unreliable (backpressure from LLM throttling)

## Replacement Architecture

The new architecture uses **deterministic microservices** for data processing:

| Old (Deprecated) | New (Use This) |
|------------------|----------------|
| `deadband_agent_sam.py` | `deadband_service.py` |
| `sketch_agent_sam.py` | `sketch_service.py` |
| `anomaly_agent_sam.py` | `anomaly_service.py` |
| `fleet_agent_sam.py` | (merged into `anomaly_service.py`) |
| `mock_pipeline.py` | (combined single-process variant — not launched by any script; superseded by the microservices above) |

## Key Difference

- **Old:** Every sensor message → LLM call → expensive
- **New:** Deterministic Python code → LLM only for user queries → 99% cost reduction

See `BLOG_POST.md` for the full architectural rationale.
