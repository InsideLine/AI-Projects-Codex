from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from xml.etree.ElementTree import Element, SubElement, fromstring, tostring

from .settings import LicenseAgentSettings


class SoloError(RuntimeError):
    pass


@dataclass(frozen=True)
class SoloReportRequest:
    report_path: str
    report_type: str
    start_date: date
    end_date: date
    extra_params: dict[str, str]


class SoloClient:
    XML_LICENSE_SERVICE_PATH = "/webservices/XmlLicenseService.asmx"
    REPORT_TYPES = frozenset({"Pdf", "Xls", "Csv", "XmlElements", "XmlParameters"})

    def __init__(self, settings: LicenseAgentSettings) -> None:
        self.settings = settings
        self.base_url = settings.solo_base_url.rstrip("/")

    def status(self) -> dict[str, object]:
        return {
            **self.settings.solo_status(),
            "xml_license_service_url": f"{self.base_url}{self.XML_LICENSE_SERVICE_PATH}",
            "can_call_xml_service": bool(
                self.settings.solo_author_id
                and self.settings.solo_api_user_id
                and self.settings.solo_api_user_password
            ),
        }

    def build_add_license_xml(self, fields: dict[str, Any]) -> str:
        return self._build_xml("LicenseAdd", fields, include_credentials=True)

    def build_get_license_custom_data_xml(self, license_id: str | int) -> str:
        return self._build_xml(
            "GetLicenseCustomData",
            {"LicenseID": str(license_id)},
            include_credentials=True,
        )

    def build_update_license_custom_data_xml(self, license_id: str | int, custom_data: str, xml_format: str = "") -> str:
        return self._build_xml(
            "UpdateLicenseCustomData",
            {
                "LicenseID": str(license_id),
                "LicenseCustomData": custom_data,
                "Format": xml_format,
            },
            include_credentials=True,
        )

    def build_programmatic_report_params(self, request: SoloReportRequest) -> dict[str, str]:
        self._validate_report_type(request.report_type)
        return {
            "WebServiceLogin": "True",
            "AuthorID": self._require(self.settings.solo_author_id, "SOLO_AUTHOR_ID"),
            "UserID": self._require(self.settings.solo_api_user_id, "SOLO_API_USER_ID"),
            "UserPassword": self._require(self.settings.solo_api_user_password, "SOLO_API_USER_PASSWORD"),
            "ReportType": request.report_type,
            "StartDate": self._format_date(request.start_date),
            "EndDate": self._format_date(request.end_date),
            **request.extra_params,
        }

    def build_programmatic_report_url(self, report_path: str) -> str:
        normalized = report_path if report_path.startswith("/") else f"/{report_path}"
        return f"{self.base_url}{normalized}"

    def execute_programmatic_report(self, request: SoloReportRequest, *, method: str = "POST") -> bytes:
        params = self.build_programmatic_report_params(request)
        url = self.build_programmatic_report_url(request.report_path)

        if method.upper() == "GET":
            url = f"{url}?{urlencode(params)}"
            body = None
            headers: dict[str, str] = {}
        else:
            body = urlencode(params).encode("utf-8")
            headers = {"Content-Type": "application/x-www-form-urlencoded"}

        response = self._request(url, method=method.upper(), data=body, headers=headers)

        if response.startswith(b"<RequestError>"):
            try:
                error_root = fromstring(response.decode("utf-8"))
                error_type = error_root.findtext("ErrorType") or "Unknown"
            except Exception as exc:  # pragma: no cover
                raise SoloError("SOLO returned a report error payload.") from exc
            raise SoloError(f"SOLO report request failed with error type: {error_type}")
        return response

    def execute_xml_license_operation(self, operation: str, xml_payload: str) -> bytes:
        if not operation:
            raise SoloError("SOLO XML operation name is required.")
        url = f"{self.base_url}{self.XML_LICENSE_SERVICE_PATH}/{operation}"
        body = urlencode({"xml": xml_payload}).encode("utf-8")
        return self._request(
            url,
            method="POST",
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

    def _build_xml(self, root_name: str, fields: dict[str, Any], *, include_credentials: bool) -> str:
        root = Element(root_name)

        if include_credentials:
            SubElement(root, "AuthorID").text = self._require(self.settings.solo_author_id, "SOLO_AUTHOR_ID")
            SubElement(root, "UserID").text = self._require(self.settings.solo_api_user_id, "SOLO_API_USER_ID")
            SubElement(root, "UserPassword").text = self._require(
                self.settings.solo_api_user_password,
                "SOLO_API_USER_PASSWORD",
            )

        for key, value in fields.items():
            element = SubElement(root, key)
            element.text = "" if value is None else str(value)
        return tostring(root, encoding="unicode", short_empty_elements=False)

    def _request(
        self,
        url: str,
        *,
        method: str,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> bytes:
        request = Request(url, method=method, data=data, headers=headers or {})
        try:
            with urlopen(request, timeout=30) as response:
                return response.read()
        except Exception as exc:  # pragma: no cover
            raise SoloError(f"SOLO request failed: {exc}") from exc

    def _format_date(self, value: date) -> str:
        return f"{value.month}/{value.day}/{value.year}"

    def _validate_report_type(self, report_type: str) -> None:
        if report_type not in self.REPORT_TYPES:
            raise SoloError(f"Unsupported SOLO report type: {report_type}")

    def _require(self, value: str | None, name: str) -> str:
        if not value:
            raise SoloError(f"{name} is required.")
        return value

