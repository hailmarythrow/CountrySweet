# Development dashboard snapshot persistence

The optional Firebase snapshot path is enabled only when:

```powershell
$env:BAKO_DEV_FIRESTORE_SNAPSHOTS = "true"
```

It uses the separate `bako_sweet_dev` namespace:

- `bako_sweet_dev_dashboard_snapshots/{snapshot_id}` stores immutable snapshot metadata.
- `bako_sweet_dev_dashboard_snapshot_chunks/{snapshot_id}__...` stores bounded normalized dataframe records.
- `bako_sweet_dev_dashboard_snapshot_active/current` points to the last complete snapshot.

Workbook bytes, raw spreadsheet cells, employee identities, and production
`active_workbook/current` metadata are not written by this path.

The snapshot contains the unique frames referenced by the parser's dashboard
semantics: inventory, inventory timeline, harvest/sales, purchasing/loads, and
pricing frames when those worksheets are present. Each frame is chunked below
the Firestore document limit and verified by row count and checksum before the
active pointer advances.

If publication or restoration fails, the previous active pointer and the last
validated local dataset remain intact. Concurrent publishers must supply the
previous snapshot version; stale attempts are rejected transactionally.

The feature is intentionally not enabled in the default dashboard process.
Before publishing real Country Sweet data, obtain explicit storage approval and
replace the development namespace with a reviewed production data-governance
design.
