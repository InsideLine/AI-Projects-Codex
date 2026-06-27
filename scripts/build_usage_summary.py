from __future__ import annotations

import argparse
import json
from pathlib import Path

from license_agent.usage_summary import build_company_usage_summary, write_company_usage_summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a compact company usage summary from ProcessInfo JSONL.")
    parser.add_argument(
        "--processinfo-root",
        default="local_data/raw/aws_dynamodb_full/processinfo",
        help="Root folder containing ProcessInfo DynamoDB export records.jsonl files.",
    )
    parser.add_argument(
        "--output",
        default="local_data/curated/aws_usage/company_usage_summary.json",
        help="Output JSON summary path.",
    )
    args = parser.parse_args()

    summary = build_company_usage_summary(args.processinfo_root)
    output_path = write_company_usage_summary(summary, args.output)
    print(json.dumps({"output": str(Path(output_path)), "meta": summary["meta"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
