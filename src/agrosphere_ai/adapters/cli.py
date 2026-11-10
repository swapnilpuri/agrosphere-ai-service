"""Generated-only local commands. No public import/training API."""
import argparse
from datetime import date
import json
import os
from pathlib import Path
from agrosphere_ai.adapters.generated_corpus import generated_corpus
from agrosphere_ai.adapters.runtime import configured_runtime
from agrosphere_ai.domain.baselines import Scope


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    generate = sub.add_parser("generate")
    for name in ("estate", "block", "timezone", "start", "seed", "output"):
        generate.add_argument("--" + name, required=True)
    imp = sub.add_parser("import-generated")
    imp.add_argument("--corpus", required=True)
    args = parser.parse_args()
    if args.command == "generate":
        value = generated_corpus(Scope(args.estate, args.block), args.timezone, date.fromisoformat(args.start), args.seed)
        path = Path(args.output)
        if path.exists():
            raise RuntimeError("Do not overwrite an existing corpus")
        path.write_text(json.dumps(value, separators=(",", ":")), encoding="utf-8")
        return
    runtime = configured_runtime()
    if runtime is None:
        raise RuntimeError("Generated M4 is disabled")
    identity = runtime.store.import_generated(args.corpus)
    print(json.dumps({"datasetId": identity, "mode": "GENERATED_POINT_IN_TIME"}))


if __name__ == "__main__":
    main()
