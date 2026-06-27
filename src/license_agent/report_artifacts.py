from __future__ import annotations

import html
import io
import re
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .settings import LicenseAgentSettings


@dataclass(frozen=True)
class ReportSection:
    heading: str
    body: tuple[str, ...] = ()
    bullets: tuple[str, ...] = ()


def publish_word_report(
    settings: LicenseAgentSettings,
    *,
    job_id: str,
    report_document: dict[str, Any],
    s3_client: Any | None = None,
) -> dict[str, str]:
    """Create a docx report artifact in S3 and return a short-lived download URL."""
    if not settings.report_s3_bucket:
        return {}

    client = s3_client or _boto3_s3_client(settings.aws_region)
    title = str(report_document.get("title") or "License Violation Review")
    subject = _safe_key_part(str(report_document.get("subject") or "unknown"))
    key = f"reports/{datetime.now(timezone.utc).strftime('%Y/%m/%d')}/{job_id}-{subject}.docx"
    body = build_docx_report(report_document)
    client.put_object(
        Bucket=settings.report_s3_bucket,
        Key=key,
        Body=body,
        ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ServerSideEncryption="AES256",
    )
    url = client.generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.report_s3_bucket, "Key": key},
        ExpiresIn=60 * 60 * 24 * 7,
    )
    return {"bucket": settings.report_s3_bucket, "key": key, "url": url, "title": title}


def build_docx_report(report_document: dict[str, Any]) -> bytes:
    title = str(report_document.get("title") or "License Violation Review")
    subtitle = str(report_document.get("subtitle") or "")
    sections = _sections_from_document(report_document)
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    paragraphs = [
        _paragraph(title, style="Title"),
        _paragraph(subtitle, style="Subtitle") if subtitle else "",
        _paragraph(f"Generated: {generated_at}", style="Normal"),
    ]
    for section in sections:
        paragraphs.append(_paragraph(section.heading, style="Heading1"))
        for body in section.body:
            paragraphs.append(_paragraph(body, style="Normal"))
        for bullet in section.bullets:
            paragraphs.append(_paragraph(bullet, style="ListBullet"))

    document_xml = _document_xml("".join(paragraph for paragraph in paragraphs if paragraph))
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", CONTENT_TYPES_XML)
        archive.writestr("_rels/.rels", ROOT_RELS_XML)
        archive.writestr("word/_rels/document.xml.rels", DOCUMENT_RELS_XML)
        archive.writestr("word/styles.xml", STYLES_XML)
        archive.writestr("word/numbering.xml", NUMBERING_XML)
        archive.writestr("word/document.xml", document_xml)
    return output.getvalue()


def _sections_from_document(report_document: dict[str, Any]) -> list[ReportSection]:
    sections: list[ReportSection] = []
    for section in report_document.get("sections") or []:
        if not isinstance(section, dict):
            continue
        sections.append(
            ReportSection(
                heading=str(section.get("heading") or "Section"),
                body=tuple(str(item) for item in section.get("body") or [] if str(item).strip()),
                bullets=tuple(str(item) for item in section.get("bullets") or [] if str(item).strip()),
            )
        )
    return sections


def _paragraph(text: str, *, style: str) -> str:
    escaped = html.escape(text, quote=False)
    style_xml = f'<w:pStyle w:val="{style}"/>' if style != "Normal" else ""
    bullet_xml = ""
    if style == "ListBullet":
        style_xml = '<w:pStyle w:val="ListBullet"/>'
        bullet_xml = "<w:numPr><w:ilvl w:val=\"0\"/><w:numId w:val=\"1\"/></w:numPr>"
    return (
        "<w:p>"
        f"<w:pPr>{style_xml}{bullet_xml}</w:pPr>"
        f"<w:r><w:t xml:space=\"preserve\">{escaped}</w:t></w:r>"
        "</w:p>"
    )


def _document_xml(body_xml: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body>"
        f"{body_xml}"
        "<w:sectPr>"
        '<w:pgSz w:w="12240" w:h="15840"/>'
        '<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440" '
        'w:header="720" w:footer="720" w:gutter="0"/>'
        "</w:sectPr>"
        "</w:body>"
        "</w:document>"
    )


def _safe_key_part(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-")
    return cleaned[:80] or "report"


def _boto3_s3_client(region: str) -> Any:
    import boto3

    return boto3.client("s3", region_name=region)


CONTENT_TYPES_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
  <Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/>
</Types>
"""

ROOT_RELS_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>
"""

DOCUMENT_RELS_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rIdStyles" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
  <Relationship Id="rIdNumbering" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering" Target="numbering.xml"/>
</Relationships>
"""

STYLES_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:style w:type="paragraph" w:default="1" w:styleId="Normal">
    <w:name w:val="Normal"/>
    <w:qFormat/>
    <w:pPr><w:spacing w:after="120" w:line="276" w:lineRule="auto"/></w:pPr>
    <w:rPr><w:rFonts w:ascii="Aptos" w:hAnsi="Aptos"/><w:sz w:val="22"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Title">
    <w:name w:val="Title"/>
    <w:basedOn w:val="Normal"/>
    <w:next w:val="Subtitle"/>
    <w:qFormat/>
    <w:pPr><w:spacing w:after="160"/></w:pPr>
    <w:rPr><w:b/><w:sz w:val="36"/><w:color w:val="1F2937"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Subtitle">
    <w:name w:val="Subtitle"/>
    <w:basedOn w:val="Normal"/>
    <w:next w:val="Normal"/>
    <w:qFormat/>
    <w:rPr><w:i/><w:color w:val="4B5563"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Heading1">
    <w:name w:val="Heading 1"/>
    <w:basedOn w:val="Normal"/>
    <w:next w:val="Normal"/>
    <w:qFormat/>
    <w:pPr><w:spacing w:before="280" w:after="100"/></w:pPr>
    <w:rPr><w:b/><w:sz w:val="28"/><w:color w:val="111827"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="ListBullet">
    <w:name w:val="List Bullet"/>
    <w:basedOn w:val="Normal"/>
    <w:pPr><w:ind w:left="720" w:hanging="360"/></w:pPr>
  </w:style>
</w:styles>
"""

NUMBERING_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:numbering xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:abstractNum w:abstractNumId="0">
    <w:multiLevelType w:val="hybridMultilevel"/>
    <w:lvl w:ilvl="0">
      <w:start w:val="1"/>
      <w:numFmt w:val="bullet"/>
      <w:lvlText w:val="&#8226;"/>
      <w:lvlJc w:val="left"/>
      <w:pPr><w:ind w:left="720" w:hanging="360"/></w:pPr>
    </w:lvl>
  </w:abstractNum>
  <w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num>
</w:numbering>
"""
