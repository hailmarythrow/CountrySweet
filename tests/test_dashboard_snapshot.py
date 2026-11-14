import unittest
from datetime import datetime, timezone

import pandas as pd

from utils.dashboard_snapshot import (
    _collections,
    _decoded_chunk_rows,
    _encoded_chunk_payload,
    _frame_records,
    _records_checksum,
    _restore_snapshot_cached,
    restore_dashboard_snapshot,
)


class DashboardSnapshotEncodingTests(unittest.TestCase):
    def test_compressed_chunk_round_trips_and_legacy_rows_still_read(self):
        rows = [{"product": "Japanese", "stored_lbs": 123.0}] * 50
        payload, stored_bytes = _encoded_chunk_payload(rows)
        self.assertGreater(stored_bytes, 0)
        self.assertEqual(_decoded_chunk_rows(payload), rows)
        self.assertEqual(_decoded_chunk_rows({"rows": rows}), rows)


class _FakeSnapshot:
    def __init__(self, document_id, payload):
        self.id = document_id
        self._payload = payload
        self.exists = payload is not None

    def to_dict(self):
        return dict(self._payload or {})


class _FakeReference:
    def __init__(self, client, collection, document_id):
        self.client = client
        self.collection_name = collection
        self.id = document_id

    def get(self):
        self.client.reads += 1
        return _FakeSnapshot(self.id, self.client.documents.get(self.collection_name, {}).get(self.id))


class _FakeCollection:
    def __init__(self, client, name):
        self.client = client
        self.name = name

    def document(self, document_id):
        return _FakeReference(self.client, self.name, document_id)


class _FakeClient:
    def __init__(self):
        self.documents = {}
        self.reads = 0

    def collection(self, name):
        self.documents.setdefault(name, {})
        return _FakeCollection(self, name)

    def get_all(self, references):
        return [reference.get() for reference in references]


class DashboardSnapshotCacheTests(unittest.TestCase):
    def test_immutable_snapshot_is_reused_by_version_without_rechecking_chunks(self):
        client = _FakeClient()
        namespace = "performance_test"
        snapshot_id = "snapshot-001"
        snapshot_collection, chunk_collection, _ = _collections(namespace)
        client.collection(snapshot_collection)
        client.collection(chunk_collection)
        frame = pd.DataFrame({
            "product": ["Covington Medium"],
            "variety": ["Covington"],
            "classification": ["Organic"],
            "size": ["Medium"],
            "stored_lbs": [1000.0],
            "forecast_demand_lbs": [100.0],
        })
        records = _frame_records(frame)
        encoded, _ = _encoded_chunk_payload(records)
        chunk_id = "snapshot-001__inventory__00000"
        client.documents[chunk_collection][chunk_id] = {
            **encoded,
            "records_checksum": _records_checksum(records),
            "row_count": len(records),
        }
        client.documents[snapshot_collection][snapshot_id] = {
            "snapshot_id": snapshot_id,
            "status": "complete",
            "source_workbook_id": "test-workbook",
            "source_filename": "synthetic.xlsx",
            "source_version": "v1",
            "source_checksum": "checksum-001",
            "validation_status": "validated",
            "published_at": datetime.now(timezone.utc),
            "available_sheets": ["Current Week Model"],
            "preferred_sheet": "Current Week Model",
            "semantic_sheets": {"inventory": "Current Week Model", "inventory_timeline": "Current Week Model"},
            "manifests": [{
                "frame_name": "Current Week Model",
                "columns": list(frame.columns),
                "row_count": len(records),
                "chunk_ids": [chunk_id],
                "records_checksum": _records_checksum(records),
                "datetime_columns": [],
                "diagnostics": {"semantic": "inventory"},
            }],
            "stats": {"frame_count": 1, "chunk_count": 1, "row_count": 1, "payload_bytes": 100},
        }

        _restore_snapshot_cached.clear()
        first = restore_dashboard_snapshot(client=client, namespace=namespace, snapshot_id=snapshot_id)
        reads_after_first = client.reads
        second = restore_dashboard_snapshot(client=client, namespace=namespace, snapshot_id=snapshot_id)

        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertEqual(first.snapshot_id, second.snapshot_id)
        self.assertEqual(reads_after_first, client.reads)
        self.assertEqual(second.report.frames["Current Week Model"].iloc[0]["stored_lbs"], 1000.0)
        self.assertIn("metadata_read_ms", first.stats)
        self.assertIn("chunk_fetch_ms", first.stats)
        self.assertIn("deserialize_ms", first.stats)


if __name__ == "__main__":
    unittest.main()
