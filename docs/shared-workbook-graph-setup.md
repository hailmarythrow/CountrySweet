# Persistent OneDrive/SharePoint workbook preparation

The Microsoft Graph adapter is prepared but not activated. It uses stable
`drive_id` and `item_id` values; it never stores a local desktop path or a
public sharing link.

## Planned refresh flow

1. An authenticated publisher selects the existing `.xlsx` item once through
   an approved Microsoft file-picker flow or an administrator-provided
   drive/item reference.
2. The selected IDs are stored in the Firestore `active_workbook/current`
   metadata document, along with the `graph://drives/.../items/...` source
   reference.
3. A manual refresh requests Graph file metadata and compares the saved
   `eTag`/`cTag` source version.
4. If changed, Graph content is downloaded and passed to the existing workbook
   parser/validator.
5. Only a successful validation may publish a new immutable `workbook_versions`
   record and advance the active pointer.
6. A refresh audit record can store the validated Entra employee `oid` and
   validation outcome in `workbook_refresh_history`.

The adapter does not upload, modify, or delete the remote workbook. It also
does not run automatically; refresh remains an explicit user action.

## Microsoft Graph permissions for administrator review

Use delegated permissions initially, with least privilege:

- `Files.Read` for a workbook in the signed-in employee's OneDrive.
- For a shared SharePoint document library, the administrator should choose
  the narrowest approved combination, commonly `Files.Read.All` or
  `Sites.Read.All`, based on the selected drive/site scope.
- `User.Read` is only needed if a future implementation calls Microsoft Graph
  for profile data; the current identity design uses validated OIDC claims.

If refresh must run without an employee session, an application-permission
design is required instead and needs administrator consent. That is not
implemented here. The relevant Graph operations are:

```text
GET /drives/{drive-id}/items/{item-id}
GET /drives/{drive-id}/items/{item-id}/content
```

The metadata request reads the file name, size, modification time, eTag/cTag,
and web URL. The content request supplies bytes to the existing parser; no
workbook bytes are placed in Firestore.

## What is testable before authorization

The repository includes offline tests for stable source-reference construction,
Graph metadata/version parsing, changed-file detection, and blocking failed
validation before publishing. A real Graph download, file-picker selection,
and refresh audit cannot be claimed until Entra access tokens and administrator
consent are available.
