import tempfile
import unittest

from app.state import BotState


class TestBotState(unittest.TestCase):
    def test_state_persistence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = f"{tmp}/state.json"
            state = BotState(path=path)
            state.set_profile("org/repo", "strict")
            state.add_memory("org/repo", 12, "expected behavior")
            state.set_meme_mode("org/repo", True)
            state.set_last_review(
                "org/repo",
                12,
                {"summary": "ok", "findings": [{"id": "F1"}]},
            )

            restored = BotState(path=path)
            self.assertEqual(restored.get_profile("org/repo"), "strict")
            self.assertEqual(restored.get_memory("org/repo", 12), ["expected behavior"])
            self.assertTrue(restored.get_meme_mode("org/repo"))
            self.assertEqual(
                restored.get_last_review("org/repo", 12)["summary"],
                "ok",
            )

    def test_meme_rotation_persists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = f"{tmp}/state.json"
            state = BotState(path=path)

            idx1 = state.next_meme_index("org/repo", "fail", 4)
            idx2 = state.next_meme_index("org/repo", "fail", 4)
            idx3 = state.next_meme_index("org/repo", "fail", 4)
            self.assertEqual((idx1, idx2, idx3), (0, 1, 2))

            restored = BotState(path=path)
            idx4 = restored.next_meme_index("org/repo", "fail", 4)
            self.assertEqual(idx4, 3)

            idx5 = restored.next_meme_index("org/repo", "fail", 4)
            self.assertEqual(idx5, 0)


if __name__ == "__main__":
    unittest.main()
