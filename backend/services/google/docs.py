"""Text-only, range-preserving Docs edits with original UTF-16 coordinates and revision CAS."""

from urllib.parse import quote

from backend.schemas.google import (
    DOC_MIME,
    DocumentEdit,
    GoogleDocumentSnapshot,
    GoogleFileRef,
    GoogleTextBlock,
    GoogleWritePreview,
)
from backend.services.google.access import google_url
from backend.services.google.client import DOCS_URL
from backend.services.google.errors import GoogleServiceError
from backend.services.google.mutations import GoogleMutations, fingerprint
from backend.services.google.sheets import snippet


def text_blocks(document):
    blocks, warnings = [], []

    def body(value, tab_id):
        for node in value.get("content", []):
            paragraph = node.get("paragraph")
            if paragraph is None:
                if "sectionBreak" not in node:
                    warnings.append(
                        "Tables/other structures are preserved and cannot be edited here."
                    )
                continue
            for element in paragraph.get("elements", []):
                run = element.get("textRun")
                if run is None or any(key.startswith("suggested") for key in element):
                    warnings.append(
                        "Images, suggestions and non-text elements are preserved, not editable."
                    )
                    continue
                content = run.get("content", "")
                start, end = element.get("startIndex"), element.get("endIndex")
                if (
                    not isinstance(content, str)
                    or type(start) is not int
                    or type(end) is not int
                    or start < 1
                    or end - start != len(content.encode("utf-16-le")) // 2
                ):
                    raise GoogleServiceError(422, "Unsupported Google Docs text indices")
                blocks.append(
                    GoogleTextBlock(tab_id=tab_id, start_index=start, end_index=end, text=content)
                )

    def tabs(items, depth=0):
        if depth > 8 or len(items) > 100:
            raise GoogleServiceError(422, "Google Docs tab structure exceeds bounds")
        for tab in items:
            tab_id = tab.get("tabProperties", {}).get("tabId")
            if not isinstance(tab_id, str) or not tab_id:
                raise GoogleServiceError(422, "Unsupported Google Docs tab")
            body(tab.get("documentTab", {}).get("body", {}), tab_id)
            tabs(tab.get("childTabs", []), depth + 1)

    if document.get("tabs"):
        tabs(document["tabs"])
    else:
        body(document.get("body", {}), None)
    if len(blocks) > 10000 or sum(len(block.text) for block in blocks) > 200000:
        raise GoogleServiceError(422, "Google Doc exceeds text bounds; use a smaller document")
    return blocks, list(dict.fromkeys(warnings))


def compile_edit(blocks, edit):
    chosen = [block for block in blocks if block.tab_id == edit.tab_id]
    if not chosen:
        raise GoogleServiceError(422, "Choose the exact document tab returned by read")
    operations = sorted(edit.operations, key=lambda op: op.start_index, reverse=True)
    previous_start, previous_insert = None, False
    requests, before = [], []
    for op in operations:
        start, end = op.start_index, op.end_index or op.start_index
        insertion = op.kind == "insert_text"
        if previous_start is not None and (
            end > previous_start or (end == previous_start and (insertion or previous_insert))
        ):
            raise GoogleServiceError(
                422, "Document edits overlap; prepare separate nonoverlapping edits"
            )
        previous_start, previous_insert = start, insertion
        block = next(
            (
                item
                for item in chosen
                if item.start_index <= start < item.end_index
                and end <= item.end_index - int(item.text.endswith("\n"))
            ),
            None,
        )
        if block is None:
            raise GoogleServiceError(
                422, "Edit must remain inside an editable text run; preserve structures/newlines"
            )
        boundaries = {block.start_index}
        position = block.start_index
        for char in block.text:
            position += len(char.encode("utf-16-le")) // 2
            boundaries.add(position)
        if start not in boundaries or end not in boundaries:
            raise GoogleServiceError(422, "Edit splits a UTF-16 character")
        span = {"startIndex": start, "endIndex": end}
        location = {"index": start}
        if edit.tab_id:
            span["tabId"] = location["tabId"] = edit.tab_id
        if not insertion:
            requests.append({"deleteContentRange": {"range": span}})
        if op.text:
            requests.append({"insertText": {"location": location, "text": op.text}})
        raw = block.text.encode("utf-16-le")
        old = raw[(start - block.start_index) * 2 : (end - block.start_index) * 2].decode(
            "utf-16-le"
        )
        before.append({"start_index": start, "end_index": end, "text": old})
    return requests, before


class GoogleDocs:
    def __init__(self, access, sessions):
        self.access, self.sessions = access, sessions
        self.journal = GoogleMutations(access, sessions)

    async def document(self, auth):
        data = await self.access.credentials.http.request(
            "GET",
            DOCS_URL + "/" + quote(auth.ref.file_id, safe=""),
            token=auth.token,
            params={"includeTabsContent": "true"},
        )
        if data.get("documentId") != auth.ref.file_id:
            raise GoogleServiceError(422, "Google Docs identity mismatch")
        return data

    async def read(self, context, ref, *, offset=0, limit=100):
        if offset < 0 or not 1 <= limit <= 100:
            raise GoogleServiceError(422, "Invalid Google Docs page")
        async with self.sessions() as session:
            auth = await self.access.authorize(session, context, ref, "read_doc")
        data = await self.document(auth)
        blocks, warnings = text_blocks(data)
        return GoogleDocumentSnapshot(
            file_id=ref.file_id,
            revision_id=data.get("revisionId"),
            blocks=blocks[offset : offset + limit],
            next_offset=offset + limit if offset + limit < len(blocks) else None,
            warnings=warnings,
        )

    async def prepare(self, context, ref, edit):
        edit = DocumentEdit.model_validate(edit)
        async with self.sessions() as session:
            auth = await self.access.authorize(session, context, ref, "write_doc")
        data = await self.document(auth)
        if data.get("revisionId") != edit.revision_id:
            raise GoogleServiceError(409, "Google Docs revision changed; read and prepare again")
        blocks, warnings = text_blocks(data)
        requests, before = compile_edit(blocks, edit)
        preview = GoogleWritePreview(
            operation="edit_doc",
            title="Edit text: " + auth.name,
            target=google_url(ref.file_id, DOC_MIME) + " · tab " + str(edit.tab_id),
            before=snippet(before),
            after=snippet(edit.model_dump(mode="json")),
            warning="Preview may be truncated. Original UTF-16 coordinates; non-text components "
            "and paragraph terminators are preserved. " + " ".join(warnings),
        )
        return await self.journal.prepare(
            context,
            connection_id=ref.connection_id,
            file_id=ref.file_id,
            connection_generation=auth.connection_generation,
            grant_generation=auth.grant_generation,
            kind="edit_doc",
            request={"ref": ref.model_dump(mode="json"), "edit": edit.model_dump(mode="json")},
            data={"requests": requests, "revision_id": edit.revision_id},
            snapshot_hash=fingerprint(edit.revision_id),
            preview=preview,
        )

    async def execute(self, context, **arguments):
        async def authorize(session, row):
            if row.kind != "edit_doc":
                raise GoogleServiceError(422, "Proposal is not a Docs edit")
            return await self.access.authorize(
                session,
                context,
                GoogleFileRef.model_validate(row.payload["request"]["ref"]),
                "write_doc",
            )

        async def snapshot(row, auth):
            return fingerprint((await self.document(auth)).get("revisionId"))

        async def write(row, auth, session):
            await self.access.credentials.http.request(
                "POST",
                DOCS_URL + "/" + quote(row.file_id, safe="") + ":batchUpdate",
                token=auth.token,
                json={
                    "requests": row.payload["data"]["requests"],
                    "writeControl": {"requiredRevisionId": row.payload["data"]["revision_id"]},
                },
            )
            return {"url": google_url(row.file_id, DOC_MIME)}

        return await self.journal.execute(
            context,
            tool_name="google_docs_edit",
            authorize=authorize,
            snapshot=snapshot,
            write=write,
            **arguments,
        )

    async def prepare_create(self, context, connection_id, *, title):
        if not isinstance(title, str) or not 1 <= len(title.strip()) <= 255:
            raise GoogleServiceError(422, "Choose a document title of 1-255 characters")
        async with self.sessions() as session:
            auth = await self.access.authorize_connection(session, context, connection_id)
        return await self.journal.prepare(
            context,
            connection_id=connection_id,
            file_id=None,
            connection_generation=auth.connection_generation,
            grant_generation=None,
            kind="create_doc",
            request={"title": title},
            data={"title": title},
            snapshot_hash=fingerprint(auth.connection_generation),
            preview=GoogleWritePreview(
                operation="create_doc",
                title="Create: " + title,
                target="Google Drive (My Drive)",
                before="No document",
                after="New blank native Google Doc: " + title,
                warning="Creates one blank Doc. Add content with a separately approved text edit. "
                "An unknown outcome must be reconciled before creating again.",
            ),
        )

    async def create(self, context, **arguments):
        async def authorize(session, row):
            if row.kind != "create_doc":
                raise GoogleServiceError(422, "Proposal is not a Docs creation")
            return await self.access.authorize_connection(session, context, row.connection_id)

        async def snapshot(row, auth):
            return fingerprint(auth.connection_generation)

        async def write(row, auth, session):
            data = await self.access.credentials.http.request(
                "POST", DOCS_URL, token=auth.token, json={"title": row.payload["data"]["title"]}
            )
            identity = GoogleFileRef(
                connection_id=row.connection_id,
                file_id=data.get("documentId"),
                context_id=context.conversation_id,
            ).file_id
            metadata = await self.access.metadata(identity, auth.token)
            if metadata["mimeType"] != DOC_MIME:
                raise GoogleServiceError(422, "Google did not create a native document")
            ref = await self.access.register_created(session, context, auth, metadata)
            return {"url": google_url(identity, DOC_MIME), "file_ref": ref.model_dump(mode="json")}

        return await self.journal.execute(
            context,
            tool_name="google_docs_create",
            authorize=authorize,
            snapshot=snapshot,
            write=write,
            **arguments,
        )
