"""Optimization crew — benchmarks and recommends inference configuration."""

from crewai import Agent, Crew, Process, Task

from crew.tools.benchmark import benchmark_llama_server, benchmark_ollama


def create_optimization_crew(
    target: str = "b70",
    model: str = "qwen2.5:14b",
    llm: str = "ollama/qwen2.5:14b-8k",
) -> Crew:
    """Create a crew for inference optimization on a specific GPU target.

    Args:
        target: 'b70' for Intel Arc B580, 'rtx4070' for NVIDIA RTX 4070
        model: Model name to benchmark
        llm: LLM for the agents themselves to use for reasoning
    """

    if target == "b70":
        tools = [benchmark_llama_server]
        backstory = (
            "You optimize llama-server on Intel Arc B580 (SYCL). Key constraints: "
            "must use -np 1, thinking can't be disabled, baseline is 52.7 tok/s. "
            "You test quantization levels, context sizes, and batch parameters."
        )
    else:
        tools = [benchmark_ollama]
        backstory = (
            "You optimize Ollama on RTX 4070 (12GB VRAM). Baseline is ~60 tok/s "
            "with qwen2.5:14b. You balance model size, quantization, and context "
            "window to maximize throughput within the VRAM budget."
        )

    benchmarker = Agent(
        role=f"{target.upper()} Inference Benchmarker",
        goal=f"Benchmark {model} on {target} and find optimal parameters",
        backstory=backstory,
        tools=tools,
        llm=llm,
        verbose=True,
    )

    analyst = Agent(
        role="Performance Analyst",
        goal="Analyze benchmark results and recommend optimal configuration",
        backstory=(
            "You analyze inference benchmark data, compare against baselines, "
            "and produce actionable configuration recommendations. You consider "
            "throughput, stability, and quality tradeoffs."
        ),
        tools=[],
        llm=llm,
        verbose=True,
    )

    benchmark_task = Task(
        description=(
            f"Run benchmarks for {model} on {target}. Test at least 3 different "
            "configurations (vary context size, quantization if available). "
            "Record tok/s for each configuration."
        ),
        expected_output="Benchmark table with configuration parameters and tok/s results.",
        agent=benchmarker,
    )

    analysis_task = Task(
        description=(
            "Analyze the benchmark results. Compare to known baselines. "
            "Recommend the optimal llm.json configuration with rationale. "
            "Include the recommended server launch command."
        ),
        expected_output=(
            "Configuration recommendation: llm.json values, server command, "
            "expected throughput, and tradeoff analysis."
        ),
        agent=analyst,
    )

    return Crew(
        agents=[benchmarker, analyst],
        tasks=[benchmark_task, analysis_task],
        process=Process.sequential,
        verbose=True,
    )
