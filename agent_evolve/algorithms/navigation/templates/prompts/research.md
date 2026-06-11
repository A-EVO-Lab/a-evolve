REGIME TO INVESTIGATE: {regime}

ALREADY TESTED (from research_log):
{known_results}

INSTRUCTIONS:
1. First, study what exists: read /solver_workspace/infra/ and tools/
   to understand current capabilities. Browse GitHub or the web for
   open-source implementations that solve similar search problems.
2. Then test MULTIPLE new approaches for this regime in the sandbox.
3. For each, make a real HTTP request or test command.
4. Record EXACTLY what you called, what came back, whether it's usable.
4. Write each finding as a JSON line to /evolver_workspace/tests/research_{regime}.jsonl:
   echo '{{"cycle": {evo_number}, "regime": "{regime}", "approach": "<name>", "endpoint": "<url>", "tested": true, "works": true, "latency_ms": <ms>, "coverage": ["<subtypes>"], "does_not_cover": [], "complementary_to": [], "sample_output": "<first lines>", "credential_needed": false, "credential_env": "", "error": "", "notes": "<tips>"}}' >> /evolver_workspace/tests/research_{regime}.jsonl
5. Test at least 2-3 different approaches per regime.
6. At the end, confirm what you wrote by running:
   cat /evolver_workspace/tests/research_{regime}.jsonl

{benchmark_context}
