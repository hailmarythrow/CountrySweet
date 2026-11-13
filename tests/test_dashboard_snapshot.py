import unittest

from utils.dashboard_snapshot import _decoded_chunk_rows, _encoded_chunk_payload


class DashboardSnapshotEncodingTests(unittest.TestCase):
    def test_compressed_chunk_round_trips_and_legacy_rows_still_read(self):
        rows = [{"product": "Japanese", "stored_lbs": 123.0}] * 50
        payload, stored_bytes = _encoded_chunk_payload(rows)
        self.assertGreater(stored_bytes, 0)
        self.assertEqual(_decoded_chunk_rows(payload), rows)
        self.assertEqual(_decoded_chunk_rows({"rows": rows}), rows)


if __name__ == "__main__":
    unittest.main()
