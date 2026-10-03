import sys
from pathlib import Path
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from disc_ids import match_disc_id
from regions import REGIONS

class DiscIdentityTests(unittest.TestCase):
    def test_modded_game_region(self):
        for identity in REGIONS:
            self.assertEqual(match_disc_id(identity[:4] + '99', REGIONS), identity)
        self.assertIsNone(match_disc_id('ZZZZ99', REGIONS))
