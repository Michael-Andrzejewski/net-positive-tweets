#!/usr/bin/env python3
"""Check score files against their batches.

Usage:
    python scripts/check.py              check every batch
    python scripts/check.py batch_003    check one batch
"""
import argparse
import collections
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def check_batch(batch_file, labels_file):
    """Return the labels found and a list of problems."""
    wanted = {int(x) for x in re.findall(r'^=== n=(\d+) \|', batch_file.read_text(encoding='utf-8'), re.M)}
    if not labels_file.exists():
        return {}, [f'{labels_file.name} does not exist yet']
    labels = {}
    problems = []
    for i, line in enumerate(labels_file.read_text(encoding='utf-8-sig').splitlines(), 1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except ValueError as e:
            problems.append(f'line {i}: bad JSON ({e})')
            continue
        n = item.get('n')
        if n in labels:
            problems.append(f'line {i}: n={n} appears twice')
        labels[n] = item
        score = item.get('score')
        if not isinstance(score, int) or isinstance(score, bool) or not -5 <= score <= 5:
            problems.append(f'n={n}: score must be an integer from -5 to 5')
        if item.get('confidence') not in ('high', 'medium', 'low'):
            problems.append(f'n={n}: confidence must be high, medium or low')
        reason = item.get('reason')
        if not isinstance(reason, str) or not reason.strip():
            problems.append(f'n={n}: reason is empty')
        elif len(reason.split()) > 25:
            problems.append(f'n={n}: reason is too long ({len(reason.split())} words)')
    missing = sorted(wanted - set(labels))
    extra = sorted(k for k in labels if k not in wanted)
    if missing:
        problems.append(f'{len(missing)} entries have no score: {missing[:20]}')
    if extra:
        problems.append(f'{len(extra)} scores match no entry: {extra[:20]}')
    return labels, problems


def main():
    parser = argparse.ArgumentParser(description='Check score files against their batches.')
    parser.add_argument('batch', nargs='?', help='one batch name, such as batch_003')
    parser.add_argument('--out', type=Path, default=ROOT / 'out', help='output folder (default: out)')
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding='utf-8')
    files = [args.out / 'batches' / f'{args.batch}.txt'] if args.batch else sorted((args.out / 'batches').glob('batch_*.txt'))
    if not files or not files[0].exists():
        sys.exit('No batch found. Run scripts/prepare.py first.')
    bad = 0
    for batch_file in files:
        labels, problems = check_batch(batch_file, args.out / 'labels' / f'{batch_file.stem}.jsonl')
        spread = sorted(collections.Counter(x.get('score') for x in labels.values() if isinstance(x.get('score'), int)).items())
        print(f"{batch_file.stem}: {len(labels)} scores, {'OK' if not problems else 'NEEDS FIXING'}, spread {spread}")
        for p in problems[:30]:
            print('  ' + p)
        bad += bool(problems)
    sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()
