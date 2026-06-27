import zipfile
from io import BytesIO
from unittest import TestCase

from license_agent.report_artifacts import build_docx_report, publish_word_report
from license_agent.settings import LicenseAgentSettings


class FakeS3Client:
    def __init__(self) -> None:
        self.objects = {}

    def put_object(self, **kwargs):
        self.objects[(kwargs["Bucket"], kwargs["Key"])] = kwargs
        return {}

    def generate_presigned_url(self, operation, *, Params, ExpiresIn):
        return f"https://example.test/{Params['Bucket']}/{Params['Key']}?expires={ExpiresIn}"


class ReportArtifactsTests(TestCase):
    def test_builds_docx_report_package(self) -> None:
        body = build_docx_report(
            {
                "title": "License Violation Review: Example Corp",
                "subtitle": "Requested subject: Example Corp",
                "sections": [
                    {
                        "heading": "Executive Summary",
                        "body": ["Evaluation: review recommended."],
                        "bullets": ["Files processed: 66,827"],
                    }
                ],
            }
        )
        with zipfile.ZipFile(BytesIO(body)) as archive:
            self.assertIn("[Content_Types].xml", archive.namelist())
            document = archive.read("word/document.xml").decode("utf-8")
        self.assertIn("License Violation Review: Example Corp", document)
        self.assertIn("Files processed: 66,827", document)

    def test_publish_word_report_uploads_to_report_bucket(self) -> None:
        client = FakeS3Client()
        artifact = publish_word_report(
            LicenseAgentSettings(report_s3_bucket="reports-bucket"),
            job_id="job-123",
            report_document={"title": "License Violation Review", "subject": "Example Corp", "sections": []},
            s3_client=client,
        )
        self.assertEqual(artifact["bucket"], "reports-bucket")
        self.assertIn("job-123-Example-Corp.docx", artifact["key"])
        self.assertTrue(client.objects)
