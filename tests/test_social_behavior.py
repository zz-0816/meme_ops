import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import database


class SocialBehaviorTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.original_db_path = database.DB_PATH
        database.DB_PATH = Path(self.tempdir.name) / "social-test.db"
        database.init_db()
        conn = database.get_connection()
        conn.execute("INSERT INTO users(address, nickname) VALUES('0xauthor', '作者')")
        conn.execute("INSERT INTO users(address, nickname) VALUES('0xreader', '读者')")
        conn.execute("INSERT INTO users(address, nickname) VALUES('0xthird', '第三人')")
        conn.execute("INSERT INTO posts(author, content) VALUES('0xauthor', '原始帖子')")
        conn.commit()
        conn.close()

    def tearDown(self):
        database.DB_PATH = self.original_db_path
        self.tempdir.cleanup()

    def test_repost_is_a_toggle(self):
        self.assertTrue(database.toggle_repost(1, "0xreader"))
        self.assertFalse(database.toggle_repost(1, "0xreader"))
        conn = database.get_connection()
        count = conn.execute("SELECT COUNT(*) FROM post_reposts").fetchone()[0]
        conn.close()
        self.assertEqual(count, 0)

    def test_cannot_repost_own_post(self):
        with self.assertRaises(PermissionError):
            database.toggle_repost(1, "0xauthor")

    def test_quote_creates_visible_linked_post(self):
        quote_post_id = database.create_quote(1, "0xreader", "我的引用观点")
        conn = database.get_connection()
        quote = conn.execute(
            "SELECT author, content, quoted_post_id FROM posts WHERE id = ?",
            (quote_post_id,),
        ).fetchone()
        conn.close()
        self.assertEqual(dict(quote), {
            "author": "0xreader",
            "content": "我的引用观点",
            "quoted_post_id": 1,
        })

    def test_bookmarks_are_private_per_wallet(self):
        self.assertTrue(database.toggle_bookmark(1, "0xreader"))
        self.assertEqual(len(database.get_bookmarked_posts("0xreader")), 1)
        self.assertEqual(database.get_bookmarked_posts("0xthird"), [])
        self.assertFalse(database.toggle_bookmark(1, "0xreader"))

    def test_reply_appears_only_in_post_detail(self):
        reply_id = database.create_post(
            "0xreader", "reply", parent_post_id=1,
        )
        detail = database.get_post_detail(1, "0xthird")
        self.assertEqual(detail["reply_count"], 1)
        self.assertEqual(detail["replies"][0]["id"], reply_id)
        self.assertNotIn(reply_id, [post["id"] for post in database.get_timeline("0xthird")])

    def test_following_timeline_only_contains_network(self):
        database.create_post("0xthird", "not followed")
        self.assertTrue(database.toggle_follow("0xreader", "0xauthor"))
        posts = database.get_following_timeline("0xreader")
        self.assertEqual([post["author"] for post in posts], ["0xauthor"])


if __name__ == "__main__":
    unittest.main()
