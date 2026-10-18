from __future__ import annotations

import argparse
import json
from pathlib import Path

from agent_core.evaluation import (
    DEFAULT_EVAL_DATASET_PATH,
    load_eval_dataset,
    summarize_eval_dataset,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate an offline eval dataset and rubric.")
    parser.add_argument(
        "dataset",
        nargs="?",
        type=Path,
        default=DEFAULT_EVAL_DATASET_PATH,
        help="Path to the JSON dataset.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    dataset = load_eval_dataset(args.dataset)
    print(json.dumps(summarize_eval_dataset(dataset), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
