"""Verify a local PDF through API upload, MinIO, paginated tools and persisted Context.

Run with the API virtualenv, PYTHONPATH=apps/api/src:packages/models/src, --pdf PATH
and --socket PATH of serve_runtime.py. Requires the existing isolated verification
MySQL (13306), MinIO (19000) and migrated schema configured by
check_live_worker_evidence. Creates a uniquely labelled local conversation and tool
records, retained for inspection. Does not call a model, seed catalogs, migrate,
deploy, or print document text/credentials. JSON stdout contains identifiers/metrics.
"""

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import time
import uuid


async def verify(pdf: Path, socket: str) -> dict:
    """Exercise real local storage and executable tools; fail on missing or injected text."""
    os.environ["NEXUSPILOT_FILE_RUNTIME_SOCKET"] = socket
    import check_live_worker_evidence as local
    import httpx
    from nexuspilot_models.contracts import ToolCall
    from sqlalchemy import select

    from nexuspilot_api.main import app
    from nexuspilot_api.models import (
        LlmContextSource,
        LlmModelCatalogVersion,
        LlmModelAttempt,
    )
    from nexuspilot_api.models.enums import AttemptStatus
    from nexuspilot_api.services.agent_file_tool_service import AgentFileToolService
    from nexuspilot_api.schemas.context_builds import ContextBuildRead
    from nexuspilot_api.services.parsed_artifact_service import (
        validate_parsed_context_sources,
    )
    from nexuspilot_api.services.user_file_service import read_parsed_file

    content = pdf.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    key = uuid.uuid4().hex
    storage = local.ObjectStorage()
    started = time.perf_counter()
    report = {
        "source_bytes": len(content),
        "source_sha256": digest,
        "model_requests": 0,
    }
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://127.0.0.1/api/v1/",
            headers={"X-API-Key": local.get_settings().api_key},
        ) as client,
    ):

        async def request(method, path, **kwargs):
            """Require successful local API results without printing response bodies on failure."""
            response = await client.request(method, path, **kwargs)
            assert response.is_success, (
                path,
                response.status_code,
                response.text[:300],
            )
            return response.json()

        owner = (await request("GET", "auth/me"))["user_id"]
        session = await request(
            "POST",
            "sessions",
            json={
                "user_id": owner,
                "title": f"PDF pagination verification {key[:8]}",
            },
        )
        file = await request(
            "POST",
            "files",
            json={
                "filename": pdf.name,
                "size_bytes": len(content),
                "content_hash": digest,
                "idempotency_key": f"pdf-verification-{key}",
            },
        )
        upload_started = time.perf_counter()
        uploaded = await request(
            "PUT", f"files/{file['file_id']}/content", content=content
        )
        assert uploaded["status"] == "ready"
        report.update(
            file_id=file["file_id"],
            session_id=session["session_id"],
            upload_seconds=round(time.perf_counter() - upload_started, 3),
        )
        turn = await request(
            "POST",
            f"sessions/{session['session_id']}/turns",
            json={
                "user_id": owner,
                "content_text": "按页检查附件，仅读取所需段落。",
                "file_ids": [file["file_id"]],
            },
        )
        run_id = turn["run"]["run_id"]
        async with local.database_session_factory() as db:
            catalog = await db.scalar(
                select(LlmModelCatalogVersion)
                .where(
                    LlmModelCatalogVersion.provider == "deepseek",
                )
                .order_by(LlmModelCatalogVersion.created_at.desc())
            )
            assert catalog is not None, (
                "Requires existing local model metadata (no model call)"
            )
            model = catalog.model
        payload = {
            "user_id": owner,
            "session_id": session["session_id"],
            "run_id": run_id,
            "current_user_message_id": turn["message"]["message_id"],
            "provider": "deepseek",
            "model": model,
            "idempotency_key": f"pdf-context-{key}",
        }
        context = await request("POST", "context-builds", json=payload)
        context_id = context["context_build_id"]
        manifest_message = next(
            m for m in context["messages"] if m["source_type"] == "parsed_artifact"
        )
        manifest = json.loads(
            manifest_message["content"].split("\n", 1)[1].rsplit("\n", 1)[0]
        )
        assert (
            manifest["schema_version"] == "file_manifest.v1"
            and "blocks" not in manifest
        )
        assert manifest["reading"]["returned_blocks"] == 0
        assert (await request("POST", "context-builds", json=payload))[
            "messages"
        ] == context["messages"]
        assert (await request("GET", f"context-builds/{context_id}"))[
            "messages"
        ] == context["messages"]
        async with local.database_session_factory() as db:
            await validate_parsed_context_sources(
                db, storage, ContextBuildRead.model_validate(context)
            )
            file_row = await db.get(local.UserFile, file["file_id"])
            assert await storage.verify_object(file_row.storage_uri, digest)
            assert await storage.verify_object(
                file_row.parsed_uri, file_row.parsed_hash
            )
            document = await read_parsed_file(storage, file_row)
            assert file_row.parsed_size > 55000
            sources = list(
                await db.scalars(
                    select(LlmContextSource).where(
                        LlmContextSource.context_build_id == context_id,
                    )
                )
            )
            assert all(
                document.blocks[-1].text not in s.content_text
                for s in sources
                if s.content_text
            )
            # A completed zero-usage diagnostic attempt identifies these manually driven
            # tool calls; it never represents an actual Provider HTTP request.
            attempt = LlmModelAttempt(
                run_id=run_id,
                context_build_id=context_id,
                provider="local_verification",
                model="no-model-call",
                request_type="tool_verification",
                status=AttemptStatus.COMPLETED,
                input_tokens=0,
                output_tokens=0,
            )
            db.add(attempt)
            await db.commit()
            tools = AgentFileToolService(db, storage)
            offset, received, pages = 0, [], []
            while True:
                before = time.perf_counter()
                record = await tools.execute(
                    attempt_id=attempt.attempt_id,
                    call=ToolCall(
                        id=f"page-{offset}",
                        name="read_file_content",
                        arguments={
                            "file_id": file_row.file_id,
                            "offset": offset,
                            "limit": 50,
                        },
                    ),
                )
                assert record.status == "completed", record.output_json
                result = record.output_json["result"]
                assert result["parsed_hash"] == file_row.parsed_hash
                page = result["data"]
                received.extend(page["blocks"])
                pages.append(
                    {
                        "offset": offset,
                        "count": len(page["blocks"]),
                        "next_offset": page["next_offset"],
                        "has_more": page["has_more"],
                        "tool_call_id": record.tool_call_id,
                        "seconds": round(time.perf_counter() - before, 3),
                    }
                )
                if not page["has_more"]:
                    break
                assert page["next_offset"] > offset
                offset = page["next_offset"]
            assert received == [b.model_dump(mode="json") for b in document.blocks]
            report.update(
                run_id=run_id,
                context_build_id=context_id,
                parsed_bytes=file_row.parsed_size,
                parsed_sha256=file_row.parsed_hash,
                blocks=len(document.blocks),
                extracted_text_bytes=sum(len(b.text.encode()) for b in document.blocks),
                total_pages=document.total_pages,
                parse_status=document.status,
                warnings=document.warnings,
                uncovered_pages=document.uncovered_pages,
                parser_version=document.parser_version,
                page_reads=pages,
                context_manifest_bytes=len(manifest_message["content"].encode()),
                minio_integrity=True,
                complete_pagination=True,
                context_reload=True,
                unread_text_absent=True,
            )
        followup = await request(
            "POST",
            f"sessions/{session['session_id']}/turns",
            json={
                "user_id": owner,
                "content_text": "继续核对上一轮附件，不重新上传。",
            },
        )
        followup_context = await request(
            "POST",
            "context-builds",
            json={
                **payload,
                "run_id": followup["run"]["run_id"],
                "current_user_message_id": followup["message"]["message_id"],
                "idempotency_key": f"pdf-followup-{key}",
            },
        )
        next_manifest = next(
            m
            for m in followup_context["messages"]
            if m["source_type"] == "parsed_artifact"
        )
        assert next_manifest["content"] == manifest_message["content"]
        report["followup_manifest_unchanged"] = True
    report["total_seconds"] = round(time.perf_counter() - started, 3)
    return report


def main() -> None:
    """Parse local inputs and print only validated metrics for the verification report."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", required=True, type=Path)
    parser.add_argument("--socket", required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            asyncio.run(verify(args.pdf, args.socket)), ensure_ascii=False, indent=2
        )
    )


if __name__ == "__main__":
    main()
