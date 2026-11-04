#!/usr/bin/env python3
"""Merge the scores into a private CSV and a shareable summary.

out/scores.csv has every tweet and its score. Keep it private.
out/summary.json has yearly totals and no tweet text. Share this one.

Usage:
    python scripts/finish.py --name "@yourhandle" --model "Claude Opus 5.5"
"""
import argparse
import collections
import csv
import datetime as dt
import importlib.util
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = 'net-positive-tweets/1'
MEAN_OFFSET_LIMIT = 0.3
BATCH_OFFSET_LIMIT = 0.5
WITHIN_ONE_FLOOR = 0.85


def load_check():
    # Loaded by path so this still works when Python runs with -I.
    spec = importlib.util.spec_from_file_location('check', ROOT / 'scripts' / 'check.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def csv_safe(text):
    """Stop spreadsheet apps from running a tweet as a formula."""
    return "'" + text if text[:1] in ('=', '+', '-', '@', '\t', '\r') else text


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8-sig').splitlines() if line.strip()]


def calibration(anchor_scores):
    """Compare each batch's anchor scores with the reference scores."""
    if not anchor_scores:
        return None
    refs = {a['id']: a['reference'] for a in json.loads((ROOT / 'scripts' / 'anchors.json').read_text(encoding='utf-8'))}
    diffs = []
    per_batch = {}
    for batch, items in anchor_scores.items():
        d = [score - refs[anchor] for anchor, score in items]
        per_batch[batch] = statistics.mean(d)
        diffs.extend(d)
    return {'anchors': len(refs), 'batches': len(per_batch), 'mean_offset': round(statistics.mean(diffs), 3),
            'exact': round(sum(x == 0 for x in diffs) / len(diffs), 3),
            'within_one': round(sum(abs(x) <= 1 for x in diffs) / len(diffs), 3),
            'largest_batch_offset': round(max(per_batch.values(), key=abs), 3)}


def main():
    parser = argparse.ArgumentParser(description='Merge scores into scores.csv and summary.json.')
    parser.add_argument('--out', type=Path, default=ROOT / 'out', help='output folder (default: out)')
    parser.add_argument('--name', help='name to show on the comparison page (default: your handle)')
    parser.add_argument('--model', required=True, help='full name of the AI model that did the scoring, such as "Claude Opus 5.5"')
    parser.add_argument('--allow-missing-anchors', action='store_true',
                        help='accept scores made without the reference tweets, and skip per-batch checks')
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding='utf-8')

    meta = json.loads((args.out / 'meta.json').read_text(encoding='utf-8'))
    tweets = json.loads((args.out / 'tweets.json').read_text(encoding='utf-8'))

    if not args.allow_missing_anchors:
        check = load_check()
        failed = False
        for batch_file in sorted((args.out / 'batches').glob('batch_*.txt')):
            _, problems = check.check_batch(batch_file, args.out / 'labels' / f'{batch_file.stem}.jsonl')
            for p in problems[:10]:
                print(f'{batch_file.stem}: {p}')
            failed = failed or bool(problems)
        if failed:
            sys.exit('Fix the problems above, then run this again.')

    labels = {}
    anchor_scores = collections.defaultdict(list)
    for path in sorted((args.out / 'labels').glob('batch_*.jsonl')):
        for item in read_jsonl(path):
            target = meta['entries'].get(str(item['n']))
            if target is None:
                sys.exit(f"{path.name}: n={item['n']} matches no entry.")
            if 'anchor' in target:
                anchor_scores[path.stem].append((target['anchor'], item['score']))
            elif target['n'] in labels:
                sys.exit(f"Entry n={item['n']} was scored twice.")
            else:
                labels[target['n']] = item
    missing = [t['n'] for t in tweets if t['n'] not in labels]
    if missing:
        sys.exit(f'{len(missing)} tweets have no score, for example {missing[:10]}.')

    csv_path = args.out / 'scores.csv'
    with csv_path.open('w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        writer.writerow(['tweet_number', 'batch_entry', 'date', 'type', 'deleted', 'replying_to', 'score', 'label', 'confidence', 'reason', 'likes', 'retweets', 'url', 'tweet'])
        for t in tweets:
            item = labels[t['n']]
            s = item['score']
            writer.writerow([t['n'], t['entry'], t['date'], t['type'], 'yes' if t['source'] == 'deleted' else 'no', t['reply_to'], s,
                             'net positive' if s > 0 else 'net negative' if s < 0 else 'neutral',
                             item['confidence'], csv_safe(item['reason']), t['likes'], t['retweets'], t['url'], csv_safe(t['text'])])

    years = {}
    for t in tweets:
        s = labels[t['n']]['score']
        y = years.setdefault(t['date'][:4], collections.Counter())
        y['tweets'] += 1
        if s > 0:
            y['positive_tweets'] += 1
            y['positive_points'] += s
        elif s < 0:
            y['negative_tweets'] += 1
            y['negative_points'] += s
        else:
            y['neutral_tweets'] += 1
    summary = {
        'schema': SCHEMA,
        'name': args.name or (f"@{meta['handle']}" if meta.get('handle') else 'Unnamed'),
        'model': args.model,
        'rubric_version': meta['rubric_version'],
        'through': meta['through'],
        'sampled': meta['sampled'],
        'tweets': len(tweets),
        'low_confidence': sum(1 for t in tweets if labels[t['n']]['confidence'] == 'low'),
        'calibration': calibration(anchor_scores),
        'created': dt.date.today().isoformat(),
        'years': [{'year': int(k), 'tweets_in_archive': meta['archive_counts'][k], 'tweets': v['tweets'],
                   'positive_tweets': v['positive_tweets'], 'neutral_tweets': v['neutral_tweets'],
                   'negative_tweets': v['negative_tweets'], 'positive_points': v['positive_points'],
                   'negative_points': v['negative_points']} for k, v in sorted(years.items())],
    }
    summary_path = args.out / 'summary.json'
    summary_path.write_text(json.dumps(summary, indent=1), encoding='utf-8')

    print(f"{'Year':<6}{'Tweets':>8}{'Average':>9}{'Pos pts':>9}{'Neg pts':>9}{'Pos':>7}{'Neu':>7}{'Neg':>7}")
    for y in summary['years']:
        avg = (y['positive_points'] + y['negative_points']) / y['tweets']
        print(f"{y['year']:<6}{y['tweets']:>8,}{avg:>+9.2f}{y['positive_points']:>9,}{y['negative_points']:>9,}"
              f"{y['positive_tweets']:>7,}{y['neutral_tweets']:>7,}{y['negative_tweets']:>7,}")
    pos_pts = sum(y['positive_points'] for y in summary['years'])
    neg_pts = sum(y['negative_points'] for y in summary['years'])
    print(f"{'Total':<6}{len(tweets):>8,}{(pos_pts + neg_pts) / len(tweets):>+9.2f}{pos_pts:>9,}{neg_pts:>9,}"
          f"{sum(y['positive_tweets'] for y in summary['years']):>7,}{sum(y['neutral_tweets'] for y in summary['years']):>7,}"
          f"{sum(y['negative_tweets'] for y in summary['years']):>7,}")
    cal = summary['calibration']
    if cal:
        print(f"\nReference tweets: mean offset {cal['mean_offset']:+.2f}, {cal['exact']:.0%} exact, {cal['within_one']:.0%} within 1 point, "
              f"largest batch offset {cal['largest_batch_offset']:+.2f}.")
        if (abs(cal['mean_offset']) > MEAN_OFFSET_LIMIT or abs(cal['largest_batch_offset']) > BATCH_OFFSET_LIMIT
                or cal['within_one'] < WITHIN_ONE_FLOOR):
            print('Reference check: WARNING. These results are outside the normal range, so comparisons with other people will be unreliable.')
        else:
            print('Reference check: OK.')
    else:
        print('\nNo reference tweets were scored, so this run has no calibration check.')
    print(f'\nKeep private: {csv_path}')
    print(f'Safe to share: {summary_path} (yearly totals and no tweet text)')


if __name__ == '__main__':
    main()
