"""Run the whole pipeline on an invented archive. Run with: python -m unittest discover tests"""
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tests'))
import make_fake_archive as fake  # noqa: E402


def run(*args):
    return subprocess.run([sys.executable, *map(str, args)], capture_output=True, text=True, encoding='utf-8')


class Pipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls.tmp.name)
        cls.zip = cls.dir / 'archive.zip'
        cls.total = fake.build(cls.zip)
        cls.out = cls.dir / 'out'
        cls.prep = run(ROOT / 'scripts/prepare.py', cls.zip, '--out', cls.out)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def tweets(self):
        return json.loads((self.out / 'tweets.json').read_text(encoding='utf-8'))

    def entries(self):
        return json.loads((self.out / 'meta.json').read_text(encoding='utf-8'))['entries']

    def fill_labels(self, score_of=lambda target: 1):
        """Write valid scores for every entry in every batch."""
        entries = self.entries()
        for batch in sorted((self.out / 'batches').glob('batch_*.txt')):
            ns = re.findall(r'^=== n=(\d+) \|', batch.read_text(encoding='utf-8'), re.M)
            lines = [json.dumps({'n': int(n), 'score': score_of(entries[n]), 'confidence': 'high', 'reason': 'Test reason.'}) for n in ns]
            (self.out / 'labels' / f'{batch.stem}.jsonl').write_text('\n'.join(lines), encoding='utf-8')

    def test_1_every_tweet_is_read(self):
        self.assertEqual(self.prep.returncode, 0, self.prep.stderr)
        self.assertEqual(len(self.tweets()), self.total)

    def test_2_long_form_note_is_joined(self):
        long_ones = [t for t in self.tweets() if t['long_form']]
        self.assertEqual(len(long_ones), 1)
        self.assertEqual(long_ones[0]['text'], fake.LONG_NOTE)

    def test_3_private_files_are_never_read(self):
        for path in self.out.rglob('*'):
            if path.is_file():
                self.assertNotIn(fake.DM_MARKER, path.read_text(encoding='utf-8', errors='ignore'), path)

    def test_4_batches_hide_likes(self):
        text = (self.out / 'batches' / 'batch_001.txt').read_text(encoding='utf-8')
        self.assertNotIn('likes', text.lower().replace('it likes', ''))

    def test_5_reference_tweets_are_blinded(self):
        anchors = json.loads((ROOT / 'scripts/anchors.json').read_text(encoding='utf-8'))
        for batch in (self.out / 'batches').glob('batch_*.txt'):
            text = batch.read_text(encoding='utf-8')
            for a in anchors:
                self.assertIn(a['text'], text)
            self.assertNotIn('reference', text.lower())
            self.assertNotIn('example', ' '.join(a['text'] for a in anchors).lower())
            # Dates stay in order, so the reference tweets do not stand out by date.
            dates = re.findall(r'^=== n=\d+ \| (\d{4}-\d{2}-\d{2}) \|', text, re.M)
            self.assertEqual(dates, sorted(dates))
            # This archive has one tweet per date, so copied dates would reveal the reference tweets.
            self.assertLessEqual(len(dates) - len(set(dates)), 3)
            # Entry numbers are all the same length and carry no order.
            numbers = [int(x) for x in re.findall(r'^=== n=(\d+) \|', text, re.M)]
            self.assertNotEqual(numbers, sorted(numbers))
            self.assertEqual(len({len(str(x)) for x in numbers}), 1)

    def test_6_bad_labels_are_refused(self):
        self.fill_labels()
        path = self.out / 'labels' / 'batch_001.jsonl'
        good = path.read_text(encoding='utf-8')
        path.write_text(good.replace('"score": 1', '"score": 9', 1), encoding='utf-8')
        self.assertEqual(run(ROOT / 'scripts/check.py', '--out', self.out).returncode, 1)
        self.assertNotEqual(run(ROOT / 'scripts/finish.py', '--out', self.out, '--model', 'test').returncode, 0)
        path.write_text('\n'.join(good.splitlines()[1:]), encoding='utf-8')
        self.assertEqual(run(ROOT / 'scripts/check.py', '--out', self.out).returncode, 1)
        path.write_text(good, encoding='utf-8')

    def test_7_summary_has_right_totals_and_no_tweet_text(self):
        anchors = {a['id']: a['reference'] for a in json.loads((ROOT / 'scripts/anchors.json').read_text(encoding='utf-8'))}
        scores = {t['n']: [2, 0, -1][t['n'] % 3] for t in self.tweets()}
        self.fill_labels(lambda target: anchors[target['anchor']] if 'anchor' in target else scores[target['n']])
        self.assertNotEqual(run(ROOT / 'scripts/finish.py', '--out', self.out).returncode, 0, 'the model name is required')
        done = run(ROOT / 'scripts/finish.py', '--out', self.out, '--name', 'Test Person', '--model', 'test')
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        raw = (self.out / 'summary.json').read_text(encoding='utf-8')
        summary = json.loads(raw)
        self.assertEqual(sum(y['tweets'] for y in summary['years']), self.total)
        self.assertEqual(sum(y['positive_points'] for y in summary['years']), sum(s for s in scores.values() if s > 0))
        self.assertEqual(sum(y['negative_points'] for y in summary['years']), sum(s for s in scores.values() if s < 0))
        self.assertEqual(sum(y['neutral_tweets'] for y in summary['years']), sum(s == 0 for s in scores.values()))
        self.assertEqual(summary['calibration']['mean_offset'], 0)
        self.assertEqual(summary['calibration']['within_one'], 1)
        self.assertEqual(summary['calibration']['exact'], 1)
        self.assertIn('Reference check: OK', done.stdout)
        self.assertIn('Total', done.stdout)
        csv_text = (self.out / 'scores.csv').read_text(encoding='utf-8-sig')
        self.assertIn('tweet_number,batch_entry,date,type,deleted', csv_text.splitlines()[0])
        self.assertEqual(sum(',yes,' in line for line in csv_text.splitlines()), 1)
        for t in self.tweets():
            if len(t['text']) > 12:
                self.assertNotIn(t['text'], raw)

    def test_7b_cancelling_misses_still_show(self):
        """Scores off by +2 and -2 average to zero, but the exact and within-one rates expose them."""
        anchors = {a['id']: a['reference'] for a in json.loads((ROOT / 'scripts/anchors.json').read_text(encoding='utf-8'))}
        self.fill_labels(lambda target: (max(-5, min(5, anchors[target['anchor']] + (2 if target['anchor'] % 2 else -2)))) if 'anchor' in target else 1)
        done = run(ROOT / 'scripts/finish.py', '--out', self.out, '--model', 'test')
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn('Reference check: WARNING', done.stdout)
        cal = json.loads((self.out / 'summary.json').read_text(encoding='utf-8'))['calibration']
        self.assertEqual(cal['mean_offset'], 0)
        self.assertEqual(cal['within_one'], 0)

    def test_8_spreadsheet_formulas_are_neutralized(self):
        spec = __import__('importlib.util').util.spec_from_file_location('finish', ROOT / 'scripts/finish.py')
        finish = __import__('importlib.util').util.module_from_spec(spec)
        spec.loader.exec_module(finish)
        self.assertEqual(finish.csv_safe('=HYPERLINK("x")'), '\'=HYPERLINK("x")')
        self.assertEqual(finish.csv_safe('plain'), 'plain')

    def test_9_sample_keeps_every_year_and_real_counts(self):
        out = self.dir / 'sample'
        result = run(ROOT / 'scripts/prepare.py', self.zip, '--out', out, '--sample', '12')
        self.assertEqual(result.returncode, 0, result.stderr)
        meta = json.loads((out / 'meta.json').read_text(encoding='utf-8'))
        chosen = json.loads((out / 'tweets.json').read_text(encoding='utf-8'))
        self.assertTrue(meta['sampled'])
        self.assertLess(len(chosen), self.total)
        self.assertEqual(sum(meta['archive_counts'].values()), self.total)
        self.assertEqual({t['date'][:4] for t in chosen}, set(meta['archive_counts']))

    def test_a_scores_are_not_overwritten_by_accident(self):
        out = self.dir / 'keep'
        self.assertEqual(run(ROOT / 'scripts/prepare.py', self.zip, '--out', out).returncode, 0)
        (out / 'labels' / 'batch_001.jsonl').write_text('{}', encoding='utf-8')
        self.assertNotEqual(run(ROOT / 'scripts/prepare.py', self.zip, '--out', out).returncode, 0)
        self.assertTrue((out / 'labels' / 'batch_001.jsonl').exists())


if __name__ == '__main__':
    unittest.main()
