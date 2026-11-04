#!/usr/bin/env python3
"""Read a Twitter/X archive zip and write batches of tweets for scoring.

Only the tweet files, the account file and tweet images are read. Direct
messages, contacts, IP logs and every other file in the archive stay untouched.

Usage:
    python scripts/prepare.py path/to/archive.zip
    python scripts/prepare.py path/to/archive.zip --sample 2000
"""
import argparse
import bisect
import collections
import datetime as dt
import html
import json
import random
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUBRIC_VERSION = '1'
# Fitted to real runs with Claude Code subagents: 8 batches, 530,000 characters and 177 images
# used about 1.4 million tokens, and one small batch used about 120,000. Overhead varies by setup.
TOKENS_PER_CHAR = 0.8
TOKENS_PER_IMAGE = 1200
TOKENS_PER_BATCH = 100000
DATA_FILE = re.compile(r'(?:^|/)data/(tweets?|note-tweet|deleted-tweets?|account)(?:-part\d+)?\.js$')
MEDIA_FILE = re.compile(r'(?:^|/)data/(?:deleted_)?tweets?_media/([^/]+)$')
IMAGE_EXT = {'jpg', 'jpeg', 'png', 'gif', 'webp'}


def read_archive(zip_path):
    """Return tweets, deleted tweets, notes and the account from the allowed files only."""
    found = {'tweets': [], 'deleted': [], 'notes': [], 'account': []}
    kinds = {'tweet': 'tweets', 'tweets': 'tweets', 'deleted-tweet': 'deleted', 'deleted-tweets': 'deleted',
             'note-tweet': 'notes', 'account': 'account'}
    with zipfile.ZipFile(zip_path) as z:
        for name in z.namelist():
            match = DATA_FILE.search(name)
            if not match:
                continue
            text = z.read(name).decode('utf-8-sig')
            found[kinds[match.group(1)]].extend(json.loads(text[text.index('=') + 1:].strip().rstrip(';')))
    return found


def own_words(text):
    return re.sub(r'(@\w+\s*)', '', re.sub(r'https?://\S+', '', text)).strip()


def flatten(found):
    """Make one clean, date-sorted row per tweet, with long-form notes joined in."""
    account = found['account'][0]['account'] if found['account'] else {}
    my_id = account.get('accountId', '')
    handle = account.get('username', '')

    def norm(s):
        return re.sub(r'\s+', ' ', html.unescape(s)).strip()

    def drop_mentions(s):
        return re.sub(r'^(@\w+\s+)+', '', s)

    notes = []
    for item in found['notes']:
        note = item.get('noteTweet', item)
        when = dt.datetime.fromisoformat(note['createdAt'].replace('Z', '+00:00'))
        notes.append((when, norm(note['core']['text']), html.unescape(note['core']['text'])))
    notes.sort(key=lambda x: x[0])
    note_times = [x[0] for x in notes]
    used = set()

    rows = []
    seen = set()
    for source, items in (('live', found['tweets']), ('deleted', found['deleted'])):
        for item in items:
            t = item.get('tweet', item)
            tid = t['id_str']
            if tid in seen:
                continue
            seen.add(tid)
            text = html.unescape(t['full_text'])
            when = dt.datetime.strptime(t['created_at'], '%a %b %d %H:%M:%S %z %Y')

            joined = False
            head = drop_mentions(norm(re.sub(r'https://t\.co/\S+', '', text)).rstrip('…').strip())
            if head and (len(norm(text)) >= 200 or '…' in text):
                lo = bisect.bisect_left(note_times, when - dt.timedelta(seconds=600))
                hi = bisect.bisect_right(note_times, when + dt.timedelta(seconds=600))
                best = None
                for i in range(lo, hi):
                    if i not in used and drop_mentions(notes[i][1]).startswith(head[:35]):
                        gap = abs((notes[i][0] - when).total_seconds())
                        if best is None or gap < best[0]:
                            best = (gap, i)
                if best:
                    used.add(best[1])
                    text = notes[best[1]][2]
                    joined = True

            entities = t.get('entities', {}) or {}
            urls = entities.get('urls', []) or []
            for u in urls:
                text = text.replace(u.get('url', '\0'), u.get('expanded_url', u.get('url', '')))
            media = (t.get('extended_entities') or entities).get('media', []) or []
            for m in media:
                text = text.replace(m.get('url', '\0'), '')

            reply_id = t.get('in_reply_to_status_id_str') or ''
            reply_user = t.get('in_reply_to_screen_name') or ''
            links_to_tweet = any(re.search(r'(twitter|x)\.com/\w+/status/\d+', u.get('expanded_url', '')) for u in urls)
            if text.startswith('RT @'):
                kind = 'retweet'
            elif my_id and t.get('in_reply_to_user_id_str') == my_id:
                kind = 'self_reply'
            elif reply_id or reply_user:
                kind = 'reply'
            elif links_to_tweet:
                kind = 'quote'
            else:
                kind = 'original'
            rows.append({'id': tid, 'date': when.strftime('%Y-%m-%d'), 'ts': when.isoformat(), 'type': kind,
                         'reply_to': reply_user, 'parent_id': reply_id, 'text': text.strip(),
                         'media': [m.get('type', 'photo') for m in media], 'likes': int(t.get('favorite_count', 0) or 0),
                         'retweets': int(t.get('retweet_count', 0) or 0), 'source': source, 'long_form': joined,
                         'url': f'https://x.com/{handle or "i"}/status/{tid}'})
    rows.sort(key=lambda r: r['ts'])
    for i, r in enumerate(rows, 1):
        r['n'] = i
    return rows, handle, len(used), len(notes)


def sample_rows(rows, size, seed):
    """Pick a random sample with each year keeping its share of tweets."""
    if size >= len(rows):
        return rows
    rng = random.Random(seed)
    by_year = collections.defaultdict(list)
    for r in rows:
        by_year[r['date'][:4]].append(r)
    picked = []
    for group in by_year.values():
        k = max(1, round(size * len(group) / len(rows)))
        picked.extend(rng.sample(group, min(k, len(group))))
    return sorted(picked, key=lambda r: r['n'])


def extract_images(zip_path, rows, media_dir):
    """Save images for tweets with short captions, because there the image carries the meaning."""
    media_dir.mkdir(parents=True, exist_ok=True)
    ffmpeg = shutil.which('ffmpeg')
    wanted = {r['id']: r for r in rows if r['media'] and len(own_words(r['text'])) < 80}
    count = 0
    with zipfile.ZipFile(zip_path) as z:
        for name in z.namelist():
            match = MEDIA_FILE.search(name)
            if not match or '-' not in match.group(1):
                continue
            base = match.group(1)
            row = wanted.get(base.split('-')[0])
            if row is None or len(row['view']) >= 4:
                continue
            ext = base.rsplit('.', 1)[-1].lower()
            if ext in IMAGE_EXT:
                dest = media_dir / base
                dest.write_bytes(z.read(name))
                row['view'].append(str(dest))
                count += 1
            elif ext == 'mp4' and ffmpeg:
                with tempfile.TemporaryDirectory() as tmp:
                    video = Path(tmp) / 'clip.mp4'
                    video.write_bytes(z.read(name))
                    dest = media_dir / (base + '.jpg')
                    subprocess.run([ffmpeg, '-y', '-loglevel', 'error', '-ss', '0.5', '-i', str(video),
                                    '-frames:v', '1', '-vf', 'scale=720:-2', str(dest)], check=False)
                if dest.exists():
                    row['view'].append(str(dest))
                    count += 1
    return count


def render(r, by_id, recent):
    who = {'reply': f"reply to @{r['reply_to']}" if r['reply_to'] else 'reply (target unknown)',
           'self_reply': 'reply in their own thread', 'original': 'original',
           'quote': 'quote tweet', 'retweet': 'retweet'}[r['type']]
    head = f"=== n={r['entry']} | {r['date']} | {who}"
    if r.get('source') == 'deleted':
        head += ' | deleted by author'
    if r.get('media'):
        head += f" | media: {','.join(sorted(set(r['media'])))}"
    lines = [head]
    parent = by_id.get(r.get('parent_id'))
    if parent:
        if parent.get('entry') in recent:
            lines.append(f"(continues n={parent['entry']})")
        else:
            lines.append(f"(continues an earlier tweet, which said: {parent['text'][:300]!r})")
    if r.get('view'):
        lines.append('images to view: ' + ' ; '.join(r['view']))
    elif r.get('media'):
        lines.append('(image not supplied)')
    lines.append(r['text'] or '(no text)')
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description='Turn a Twitter/X archive zip into scoring batches.')
    parser.add_argument('archive', type=Path, help='path to the archive zip')
    parser.add_argument('--out', type=Path, default=ROOT / 'out', help='output folder (default: out)')
    parser.add_argument('--batch-chars', type=int, default=60000, help='size of one batch in characters')
    parser.add_argument('--sample', type=int, help='score a random sample of this many tweets')
    parser.add_argument('--seed', type=int, default=2026, help='random seed for sampling and anchor placement')
    parser.add_argument('--no-images', action='store_true', help='do not extract images')
    parser.add_argument('--force', action='store_true', help='replace an output folder that already has scores')
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding='utf-8')

    labels_dir = args.out / 'labels'
    if labels_dir.exists() and any(labels_dir.iterdir()) and not args.force:
        sys.exit(f'{labels_dir} already has scores. Move them or pass --force to start over.')
    for sub in ('batches', 'labels', 'media'):
        shutil.rmtree(args.out / sub, ignore_errors=True)
    (args.out / 'batches').mkdir(parents=True)
    labels_dir.mkdir()

    found = read_archive(args.archive)
    rows, handle, joined, note_count = flatten(found)
    if not rows:
        sys.exit('No tweets found. Point this at the zip you downloaded from Twitter/X.')
    counts = collections.Counter(r['date'][:4] for r in rows)
    chosen = sample_rows(rows, args.sample, args.seed) if args.sample else rows
    for r in chosen:
        r['view'] = []
    images = 0 if args.no_images else extract_images(args.archive, chosen, args.out / 'media')

    anchors = json.loads((ROOT / 'scripts' / 'anchors.json').read_text(encoding='utf-8'))

    by_id = {r['id']: r for r in rows}
    batches = [[]]
    size = 0
    for r in chosen:
        if size >= args.batch_chars and r['type'] != 'self_reply':
            batches.append([])
            size = 0
        batches[-1].append(r)
        size += len(r['text']) + 90
    # Random entry numbers, and a random date between each reference tweet's neighbors, so the
    # numbering and the dates do not point them out. This stops accidental spotting and not cheating.
    total = len(chosen) + len(anchors) * len(batches)
    next_entry = iter(random.Random(args.seed).sample(range(10000, 10000 + total * 4), total))
    entries = {}
    for r in chosen:
        r['entry'] = next(next_entry)
        entries[r['entry']] = {'n': r['n']}
    total_chars = 0
    for i, batch in enumerate(batches, 1):
        place = random.Random(args.seed + i)
        seq = list(batch)
        for a in anchors:
            pos = place.randint(0, len(seq))
            after = dt.date.fromisoformat(seq[pos - 1]['date']) if pos else dt.date.fromisoformat(seq[0]['date']) - dt.timedelta(days=30)
            before = dt.date.fromisoformat(seq[pos]['date']) if pos < len(seq) else after + dt.timedelta(days=30)
            date = after + dt.timedelta(days=place.randint(0, (before - after).days))
            row = {'entry': next(next_entry), 'date': date.isoformat(), 'type': 'original', 'reply_to': '',
                   'parent_id': '', 'text': a['text'], 'media': [], 'source': 'live'}
            entries[row['entry']] = {'anchor': a['id']}
            seq.insert(pos, row)
        parts = []
        recent = []
        for r in seq:
            parts.append(render(r, by_id, set(recent[-6:])))
            recent.append(r['entry'])
        text = '\n\n'.join(parts) + '\n'
        total_chars += len(text)
        (args.out / 'batches' / f'batch_{i:03d}.txt').write_text(text, encoding='utf-8')

    (args.out / 'tweets.json').write_text(json.dumps(chosen, ensure_ascii=False), encoding='utf-8')
    meta = {'handle': handle, 'rubric_version': RUBRIC_VERSION, 'archive_counts': dict(counts),
            'through': rows[-1]['date'], 'sampled': len(chosen) < len(rows), 'entries': {str(k): v for k, v in entries.items()}}
    (args.out / 'meta.json').write_text(json.dumps(meta), encoding='utf-8')

    print(f'Account: @{handle or "unknown"}')
    print(f'Tweets in archive: {len(rows):,}. Long tweets restored to full text: {joined} of {note_count}.')
    print(f'Tweets to score: {len(chosen):,}' + (' (a random sample)' if meta['sampled'] else ''))
    print(f'Images extracted: {images}')
    print(f'Batches: {len(batches)} in {args.out / "batches"}')
    print(f'Entries to score: {total:,} (your tweets plus {len(anchors)} reference tweets in each batch).')
    overhead = len(batches) * TOKENS_PER_BATCH
    cost = total_chars * TOKENS_PER_CHAR + images * TOKENS_PER_IMAGE + overhead
    print(f'Rough cost: {round(cost, -3):,.0f} tokens with a coding agent. This includes about {overhead:,} of agent overhead, which varies by setup.')
    if len(batches) > 20:
        print('That is a lot. Consider --sample 2000, which keeps averages comparable.')


if __name__ == '__main__':
    main()
