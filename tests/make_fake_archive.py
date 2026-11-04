#!/usr/bin/env python3
"""Write a small invented Twitter archive for testing. Every tweet here is made up.

Usage: python tests/make_fake_archive.py path/to/fake_archive.zip
"""
import json
import sys
import zipfile

DM_MARKER = 'SECRET-DM-CONTENT-DO-NOT-READ'
LONG_NOTE = ('I spent a month comparing how three note-taking apps handle offline edits, and the results surprised me. '
             'Two of them lost data when the same note changed on two devices. The third kept both versions and asked me '
             'to choose. If your notes matter to you, test this yourself before you trust any sync feature. Here is how '
             'I ran the test, step by step, so that anyone can repeat it at home with two old phones.')

# (date, text, reply_to)
TWEETS = [
    ('Mon Mar 04 10:00:00 +0000 2019', 'Finally finished my first 10k. Slow, but I did it!', None),
    ('Tue Mar 05 11:00:00 +0000 2019', '@example_coach Thanks for the training plan, it made a big difference', 'example_coach'),
    ('Sat Jun 15 18:30:00 +0000 2019', 'People who drive slowly in the left lane are the absolute worst humans alive', None),
    ('Thu Jan 09 09:15:00 +0000 2020', 'lol', None),
    ('Wed Apr 22 14:00:00 +0000 2020', 'Tip for working from home: put your laptop on a stack of books so the screen is at eye level. My neck pain went away in a week.', None),
    ('Fri May 08 20:45:00 +0000 2020', '@example_news This is a stupid take and you should feel bad', 'example_news'),
    ('Sun Sep 13 16:20:00 +0000 2020', 'I made a free printable chore chart for kids. Take it if it helps: https://example.com/chores', None),
    ('Mon Feb 01 08:00:00 +0000 2021', 'Coffee', None),
    ('Tue Mar 16 12:10:00 +0000 2021', '@example_chef Does this recipe work with oat milk?', 'example_chef'),
    ('Wed Jul 21 22:05:00 +0000 2021', 'Everything is getting worse and no one is going to fix it', None),
    ('Thu Oct 07 10:30:00 +0000 2021', 'I read the city budget so you do not have to. Parks got cut by 12 percent while parking got 8 percent more. Full numbers: https://example.com/budget', None),
    ('Fri Jan 14 19:00:00 +0000 2022', '@example_friend Congrats on the new job!! So well deserved', 'example_friend'),
    ('Sat Apr 02 11:45:00 +0000 2022', 'My landlord is a crook and everyone in this building knows it', None),
    ('Sun Aug 28 15:15:00 +0000 2022', 'Saw a dog in a tiny raincoat today and it fixed my whole week', None),
    ('Mon Nov 07 09:40:00 +0000 2022', 'Reminder that you can ask your bank to waive overdraft fees. I did it twice and they agreed both times. It took five minutes on the phone.', None),
    ('Tue Feb 14 13:25:00 +0000 2023', '@example_author Loved the book, especially the chapter on memory. I had not seen that study before.', 'example_author'),
    ('Wed May 31 17:50:00 +0000 2023', 'Anyone who still uses that framework has no business writing software', None),
    ('Thu Sep 21 21:10:00 +0000 2023', 'Nobody asked but here is my ranking of soups', None),
    ('Fri Dec 22 07:35:00 +0000 2023', 'Here is what I learned after a year of learning piano as an adult: short daily practice beats long weekly practice, and recording yourself helps more than you expect.', None),
    ('Sat Mar 09 12:00:00 +0000 2024', '@example_dev I think this has a race condition. If two requests arrive together, both read the old value. A lock or a compare-and-swap would fix it.', 'example_dev'),
    ('Sun Jun 16 18:20:00 +0000 2024', 'I am so tired', None),
    ('Mon Oct 28 10:05:00 +0000 2024', 'Bought a plant', None),
    ('Tue Jan 21 16:40:00 +0000 2025', 'We are all going to lose our jobs within the year. Mark my words.', None),
    ('Wed Apr 09 09:55:00 +0000 2025', '@example_mayor Thank you for fixing the crossing on 5th. My kids walk there every day.', 'example_mayor'),
    ('Thu Aug 14 20:30:00 +0000 2025', 'That politician is a lying sack of garbage and so is everyone who votes for him', None),
]


def tweet(i, created, text, reply_to, **extra):
    t = {'id_str': str(1000 + i), 'id': str(1000 + i), 'created_at': created, 'full_text': text,
         'favorite_count': str(i % 7), 'retweet_count': '0', 'entities': {'urls': [], 'user_mentions': []},
         'in_reply_to_screen_name': reply_to, 'in_reply_to_user_id_str': '555' if reply_to else None,
         'in_reply_to_status_id_str': str(500 + i) if reply_to else None}
    t.update(extra)
    return {'tweet': t}


def build(path):
    items = [tweet(i, *row) for i, row in enumerate(TWEETS)]
    half = len(items) // 2
    stub = LONG_NOTE[:270] + '…'
    items.append(tweet(90, 'Sat Oct 11 12:00:00 +0000 2025', stub, None))
    deleted = [tweet(91, 'Sun Oct 12 12:00:00 +0000 2025', 'A tweet I deleted later', None, deleted_at='2025-10-13')]
    notes = [{'noteTweet': {'noteTweetId': '77', 'createdAt': '2025-10-11T12:00:00.000Z', 'core': {'text': LONG_NOTE}}}]
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('data/account.js', 'window.YTD.account.part0 = ' + json.dumps([{'account': {'accountId': '42', 'username': 'example_person'}}]))
        z.writestr('data/tweets.js', 'window.YTD.tweets.part0 = ' + json.dumps(items[:half]))
        z.writestr('data/tweets-part1.js', 'window.YTD.tweets.part1 = ' + json.dumps(items[half:]))
        z.writestr('data/note-tweet.js', 'window.YTD.note_tweet.part0 = ' + json.dumps(notes))
        z.writestr('data/deleted-tweets.js', 'window.YTD.deleted_tweets.part0 = ' + json.dumps(deleted))
        z.writestr('data/direct-messages.js', 'window.YTD.direct_messages.part0 = ' + json.dumps([{'text': DM_MARKER}]))
        z.writestr('data/contact.js', 'window.YTD.contact.part0 = ' + json.dumps([{'email': DM_MARKER}]))
    return len(items) + len(deleted)


if __name__ == '__main__':
    print(build(sys.argv[1]), 'tweets written')
