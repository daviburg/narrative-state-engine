# CrewAI Orchestration Layer

This directory contains the autonomous agent orchestration system for narrative-state-engine,
built on [CrewAI](https://github.com/crewAIInc/crewAI).

## Overview

While VS Code custom agents (`.github/agents/`) provide interactive specialist roles,
this CrewAI layer enables **autonomous parallel execution** — multiple specialist agents
working concurrently on well-defined tasks without human intervention.

## Structure

```
crew/
├── README.md           # This file
├── config/
│   ├── agents.yaml     # Agent role definitions
│   └── tasks.yaml      # Task templates
├── tools/
│   ├── __init__.py
│   ├── extraction.py   # Extraction pipeline tools
│   ├── benchmark.py    # Inference benchmark tools
│   ├── testing.py      # Test execution tools
│   └── git_ops.py      # Git/GitHub operations
├── crews/
│   ├── __init__.py
│   ├── extraction_crew.py   # Extraction run orchestration
│   ├── optimization_crew.py # Inference optimization workflow
│   └── release_crew.py      # Feature → test → review → merge
├── main.py             # CLI entry point
└── requirements.txt    # CrewAI dependencies
```

## Quick Start

```bash
# Install dependencies (from repo root)
pip install -r crew/requirements.txt

# Run an optimization crew
python -m crew.main optimize --target b70 --model qwen3:30b-a3b

# Run extraction with full crew
python -m crew.main extract --session test-validation --turns 1-50

# Run release workflow
python -m crew.main release --branch feat/my-feature
```

## Relationship to VS Code Agents

| Layer | Mode | Best For |
|-------|------|----------|
| `.github/agents/` | Interactive (human-in-loop) | Design, planning, ad-hoc review |
| `crew/` | Autonomous (batch) | Extraction runs, benchmarks, CI pipelines |

The VS Code coordinator agent can generate task specs that CrewAI executes autonomously.
