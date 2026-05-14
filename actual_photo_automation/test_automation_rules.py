from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from actual_photo_automation.automation import ActualPhotoAutomation
from actual_photo_automation.helpers import (
    MatchResult,
    MediaCandidate,
    RequestItem,
    extract_request_items,
)
from actual_photo_automation.upload_to_akeneo import process_records


BASE_CONFIG = {
    "archive_item_name_field": "Item_Name",
    "archive_media_fields": ["Image_Upload", "Video_Upload"],
    "archive_report": "Archive_Report",
    "archive_sku_field": "SKU",
    "archive_status_field": "Pasado_ba_sa_Quality_Check",
    "archive_approved_value": "Approve",
    "crm_app": "crm",
    "encoding_report": "All_Encoding_Requests",
    "field_actual_media": "",
    "field_image_media": "Actual_Photo1",
    "field_product_name": "Product_Name",
    "field_remarks": "Remarks_Notes",
    "field_request_items_subform": "Product_Name1",
    "field_request_status": "Request_Status",
    "field_request_type": "Type_of_Request",
    "field_video_media": "Video",
    "max_pending_fetch": 50,
    "max_upload_files_per_record": 15,
    "request_done_value": "Done",
    "request_not_available_value": "Not available",
    "request_open_values": ["Pending", "In progress"],
    "request_type_value": "Actual Photo",
    "subform_item_field": "Items",
    "subform_item_name_field": "Item_Name",
    "subform_sku_field": "SKU",
    "success_remarks": "This is uploaded by Automated",
    "workdrive_parent_folder_id": "folder-1",
    "workdrive_parent_folder_ids": [],
    "workdrive_search_depth": 2,
    "workflow_app": "workflow-module-creation",
}


class CreatorSpy:
    def __init__(self, *, records: list[dict] | None = None, upload_error: Exception | None = None):
        self.records = list(records or [])
        self.upload_error = upload_error
        self.get_records_calls: list[dict] = []
        self.update_calls: list[dict] = []
        self.upload_calls: list[dict] = []

    def get_records(self, app_link: str, report_link: str, **kwargs):
        self.get_records_calls.append(
            {"app_link": app_link, "report_link": report_link, **kwargs}
        )
        return list(self.records)

    def update_record(self, app_link: str, report_link: str, record_id: str, data: dict):
        self.update_calls.append(
            {
                "app_link": app_link,
                "report_link": report_link,
                "record_id": record_id,
                "data": dict(data),
            }
        )
        return {"code": 3000, "message": "OK"}

    def upload_file_to_record(
        self,
        app_link: str,
        report_link: str,
        record_id: str,
        field_name: str,
        file_path: Path,
        *,
        upload_name: str | None = None,
    ):
        self.upload_calls.append(
            {
                "app_link": app_link,
                "report_link": report_link,
                "record_id": record_id,
                "field_name": field_name,
                "file_path": Path(file_path),
                "upload_name": upload_name,
            }
        )
        if self.upload_error is not None:
            raise self.upload_error
        return {"code": 3000, "message": "OK"}


class WorkDriveStub:
    def __init__(self, match: MatchResult | None = None):
        self.match = match
        self.calls: list[dict] = []

    def find_best_media_match(self, *args, **kwargs):
        self.calls.append({"args": args, "kwargs": kwargs})
        return self.match


class AutomationRulesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)

    def _make_automation(
        self,
        *,
        creator: CreatorSpy | None = None,
        workdrive: WorkDriveStub | None = None,
    ) -> ActualPhotoAutomation:
        automation = object.__new__(ActualPhotoAutomation)
        automation.config = dict(BASE_CONFIG)
        automation.creator = creator or CreatorSpy()
        automation.workdrive = workdrive or WorkDriveStub()
        automation.state = {"processed": {}}
        automation.state_path = Path(self.tempdir.name) / "processed.json"
        automation.find_archive_match = lambda product_name: None
        automation._save_state = lambda: None
        automation.state["processed"] = {}

        def download_candidate(candidate: MediaCandidate, target_dir: Path) -> Path:
            destination = target_dir / candidate.name
            destination.write_bytes(b"ok")
            return destination

        automation._download_candidate = download_candidate
        return automation

    def test_fetch_pending_requests_uses_actual_photo_open_status_criteria(self) -> None:
        creator = CreatorSpy(records=[])
        automation = self._make_automation(creator=creator)

        automation.fetch_pending_requests(limit=5)

        self.assertEqual(len(creator.get_records_calls), 1)
        call = creator.get_records_calls[0]
        self.assertEqual(
            call["criteria"],
            '((Type_of_Request == "Actual Photo") && (Request_Status == "Pending" || Request_Status == "In progress"))',
        )
        self.assertEqual(call["max_records"], 5)

    def test_process_record_skips_non_actual_photo_requests(self) -> None:
        creator = CreatorSpy()
        workdrive = WorkDriveStub()
        automation = self._make_automation(creator=creator, workdrive=workdrive)
        record = {
            "ID": "1",
            "Product_Name": "Sample Lamp",
            "Type_of_Request": "Pricing",
            "Request_Status": "Pending",
            "Actual_Photo1": [],
            "Video": "",
        }

        outcome = automation.process_record(record)

        self.assertEqual(outcome.source, "skip")
        self.assertIn("not eligible", outcome.note)
        self.assertEqual(workdrive.calls, [])
        self.assertEqual(creator.upload_calls, [])
        self.assertEqual(creator.update_calls, [])

    def test_process_record_marks_successful_actual_photo_as_done(self) -> None:
        creator = CreatorSpy()
        match = MatchResult(
            source="workdrive",
            matched_name="Sample Lamp",
            media=(MediaCandidate(source="workdrive", identifier="file-1", name="sample.jpg"),),
        )
        automation = self._make_automation(
            creator=creator,
            workdrive=WorkDriveStub(match=match),
        )
        record = {
            "ID": "2",
            "Product_Name": "Sample Lamp",
            "Type_of_Request": "Actual Photo",
            "Request_Status": "Pending",
            "Actual_Photo1": [],
            "Video": "",
        }

        outcome = automation.process_record(record)

        self.assertEqual(outcome.uploaded_count, 1)
        self.assertEqual(len(creator.upload_calls), 1)
        self.assertEqual(len(creator.update_calls), 1)
        self.assertEqual(
            creator.update_calls[0]["data"]["Request_Status"],
            "Done",
        )
        self.assertIn("Automated retrieval of actual photos/videos", creator.update_calls[0]["data"]["Remarks_Notes"])

    def test_process_record_sets_not_available_when_no_media_found(self) -> None:
        """When neither WorkDrive nor Kanban Archive yield media,
        the row's status should flip to ``Not available``."""
        creator = CreatorSpy()
        automation = self._make_automation(
            creator=creator,
            workdrive=WorkDriveStub(match=None),
        )
        record = {
            "ID": "99",
            "Product_Name": "",
            "Product_Name1": [
                {
                    "ID": "row-99",
                    "Items": {"Item_Name": "Lonely Lamp"},
                    "SKU": "LONE-001",
                }
            ],
            "Type_of_Request": "Actual Photo",
            "Request_Status": "Pending",
            "Actual_Photo1": [],
            "Video": "",
        }

        outcome = automation.process_record(record)

        self.assertEqual(outcome.source, "none")
        self.assertEqual(outcome.uploaded_count, 0)
        # Exactly one update_record call setting status to "Not available".
        status_updates = [
            call for call in creator.update_calls
            if call["data"].get("Request_Status") == "Not available"
        ]
        self.assertEqual(len(status_updates), 1)

    def test_process_record_reads_subform_and_uses_sku_for_archive(self) -> None:
        """The subform's Item_Name drives WorkDrive search; SKU drives Archive."""
        creator = CreatorSpy()
        archive_calls: list[str] = []

        def fake_archive(self, sku):
            archive_calls.append(sku)
            return None

        automation = self._make_automation(creator=creator)
        automation.find_archive_match = fake_archive.__get__(automation, ActualPhotoAutomation)

        match = MatchResult(
            source="workdrive",
            matched_name="Ashura Modern LED Wall Light",
            media=(MediaCandidate(source="workdrive", identifier="f-1", name="ashura.jpg"),),
        )
        automation.workdrive = WorkDriveStub(match=match)
        record = {
            "ID": "5",
            "Product_Name": "",  # legacy field empty
            "Product_Name1": [
                {
                    "ID": "row-5",
                    "Items": {
                        "Item_Name": "Ashura | Modern LED Wall Light/Chrome + White / Medium: D40cm",
                        "zc_display_value": "Ashura | Modern LED Wall Light/Chrome + White / Medium: D40cm",
                    },
                    "SKU": "F028-M-chrome",
                }
            ],
            "Type_of_Request": "Actual Photo",
            "Request_Status": "Pending",
            "Actual_Photo1": [],
            "Video": "",
        }

        outcome = automation.process_record(record)

        self.assertEqual(outcome.uploaded_count, 1)
        self.assertEqual(archive_calls, ["F028-M-chrome"])
        # WorkDrive should have been called with the Item_Name search terms.
        self.assertEqual(len(automation.workdrive.calls), 1)
        first_workdrive_call = automation.workdrive.calls[0]
        search_terms = first_workdrive_call["args"][1]
        self.assertIn("ashura", search_terms.normalized.lower())

    def test_process_record_failed_upload_does_not_mark_done(self) -> None:
        creator = CreatorSpy(upload_error=RuntimeError("upload failed"))
        match = MatchResult(
            source="workdrive",
            matched_name="Sample Lamp",
            media=(MediaCandidate(source="workdrive", identifier="file-1", name="sample.jpg"),),
        )
        automation = self._make_automation(
            creator=creator,
            workdrive=WorkDriveStub(match=match),
        )
        record = {
            "ID": "3",
            "Product_Name": "Sample Lamp",
            "Type_of_Request": "Actual Photo",
            "Request_Status": "Pending",
            "Actual_Photo1": [],
            "Video": "",
        }

        outcome = automation.process_record(record)

        self.assertEqual(outcome.uploaded_count, 0)
        self.assertEqual(len(creator.upload_calls), 1)
        self.assertEqual(len(creator.update_calls), 1)
        self.assertNotIn("Request_Status", creator.update_calls[0]["data"])
        self.assertIn("No files were successfully uploaded", creator.update_calls[0]["data"]["Remarks_Notes"])


class UploadToAkeneoRulesTests(unittest.TestCase):
    def test_process_records_uses_done_and_actual_photo_criteria(self) -> None:
        creator = CreatorSpy(records=[])
        counts = process_records(
            creator=creator,
            akeneo=object(),
            config=dict(BASE_CONFIG),
            state={"uploaded": {}},
            state_path=Path(tempfile.gettempdir()) / "akeneo-state.json",
            report_path=Path(tempfile.gettempdir()) / "akeneo-report.csv",
            dry_run=True,
            photo_attributes=["Actual_Photo"],
        )

        self.assertEqual(counts["processed"], 0)
        self.assertEqual(len(creator.get_records_calls), 1)
        self.assertEqual(
            creator.get_records_calls[0]["criteria"],
            '(Request_Status == "Done" && Type_of_Request == "Actual Photo")',
        )

    def test_process_records_skips_non_actual_photo_records(self) -> None:
        creator = CreatorSpy(
            records=[
                {
                    "ID": "10",
                    "Product_Name": "Sample Lamp",
                    "Type_of_Request": "Pricing",
                    "Request_Status": "Done",
                    "Actual_Photo1": ["sample.jpg"],
                }
            ]
        )

        counts = process_records(
            creator=creator,
            akeneo=object(),
            config=dict(BASE_CONFIG),
            state={"uploaded": {}},
            state_path=Path(tempfile.gettempdir()) / "akeneo-state.json",
            report_path=Path(tempfile.gettempdir()) / "akeneo-report.csv",
            dry_run=True,
            photo_attributes=["Actual_Photo"],
        )

        self.assertEqual(counts["processed"], 0)
        self.assertEqual(counts["uploaded"], 0)
        self.assertEqual(counts["error"], 0)


class HelperTests(unittest.TestCase):
    def test_extract_request_items_parses_subform(self) -> None:
        record = {
            "Product_Name": "",
            "Product_Name1": [
                {
                    "ID": "row-1",
                    "Items": {
                        "Item_Name": "Ashura | Modern LED Wall Light",
                        "zc_display_value": "Ashura | Modern LED Wall Light",
                    },
                    "SKU": "F028-M-chrome",
                },
                {
                    "ID": "row-2",
                    "Items": {"Item_Name": "Other Item"},
                    "SKU": "OTHER-1",
                },
            ],
        }

        items = extract_request_items(record)

        self.assertEqual(
            items,
            [
                RequestItem(
                    item_name="Ashura | Modern LED Wall Light",
                    sku="F028-M-chrome",
                ),
                RequestItem(item_name="Other Item", sku="OTHER-1"),
            ],
        )

    def test_extract_request_items_falls_back_to_flat_field(self) -> None:
        record = {
            "Product_Name": "Legacy Lamp",
            "Product_Name1": [],
        }

        items = extract_request_items(record)

        self.assertEqual(items, [RequestItem(item_name="Legacy Lamp", sku="")])

    def test_extract_request_items_returns_empty_when_no_data(self) -> None:
        self.assertEqual(extract_request_items({}), [])


class AkeneoEmptySlotsTests(unittest.TestCase):
    """Validates the per-slot fill-empty behaviour of the Akeneo uploader."""

    def _make_client(self) -> object:
        from actual_photo_automation.akeneo import AkeneoClient

        return AkeneoClient(
            host="http://example.test",
            client_id="cid",
            secret="secret",
            username="u",
            password="p",
        )

    def test_find_empty_photo_slots_returns_all_when_none_set(self) -> None:
        client = self._make_client()
        product_data = {"values": {}}
        slots = client.find_empty_photo_slots(
            product_data,
            ["Actual_Photo", "another_picture_5", "another_picture_6"],
        )
        self.assertEqual(
            slots, ["Actual_Photo", "another_picture_5", "another_picture_6"]
        )

    def test_find_empty_photo_slots_skips_filled_slot_in_order(self) -> None:
        client = self._make_client()
        product_data = {
            "values": {
                "Actual_Photo": [
                    {"scope": None, "locale": None, "data": "some/path.jpg"}
                ],
                # another_picture_5 has an entry but no data -> still empty
                "another_picture_5": [{"scope": None, "locale": None, "data": ""}],
                # another_picture_6 missing -> empty
            }
        }
        slots = client.find_empty_photo_slots(
            product_data,
            ["Actual_Photo", "another_picture_5", "another_picture_6"],
        )
        # Actual_Photo is filled, others empty; order preserved.
        self.assertEqual(slots, ["another_picture_5", "another_picture_6"])

    def test_find_empty_photo_slots_returns_empty_list_when_all_full(self) -> None:
        client = self._make_client()
        product_data = {
            "values": {
                attr: [{"scope": None, "locale": None, "data": f"{attr}.jpg"}]
                for attr in ("Actual_Photo", "another_picture_5", "another_picture_6")
            }
        }
        slots = client.find_empty_photo_slots(
            product_data,
            ["Actual_Photo", "another_picture_5", "another_picture_6"],
        )
        self.assertEqual(slots, [])

    def test_resolve_photo_target_returns_parent_model_for_variant(self) -> None:
        """A product with parent= must upload to the parent product model."""
        from actual_photo_automation.akeneo import AkeneoClient

        ak = AkeneoClient(
            host="http://example.test",
            client_id="cid", secret="secret", username="u", password="p",
        )

        captured = {}

        def fake_get_model(code: str):
            captured["code"] = code
            return {"code": code, "values": {}, "family_variant": "size_new"}

        ak.get_product_model = fake_get_model  # type: ignore[assignment]

        variant = {
            "identifier": "SK-PU-800",
            "parent": "devi_silk_pendant_light",
            "family_variant": "size_new",
            "values": {},
        }
        data, ident, kind = ak.resolve_photo_target(variant, "product")
        self.assertEqual(captured["code"], "devi_silk_pendant_light")
        self.assertEqual(kind, "product_model")
        self.assertEqual(ident, "devi_silk_pendant_light")
        self.assertEqual(data["code"], "devi_silk_pendant_light")

    def test_resolve_photo_target_standalone_product_unchanged(self) -> None:
        from actual_photo_automation.akeneo import AkeneoClient

        ak = AkeneoClient(
            host="http://example.test",
            client_id="cid", secret="secret", username="u", password="p",
        )
        # Track whether get_product_model is called (it must NOT be).
        ak.get_product_model = lambda code: self.fail(  # type: ignore[assignment]
            "Standalone product must not trigger a parent-model lookup"
        )

        product = {"identifier": "GH-LCF-KD353", "parent": None, "values": {}}
        data, ident, kind = ak.resolve_photo_target(product, "product")
        self.assertEqual(kind, "product")
        self.assertEqual(ident, "GH-LCF-KD353")
        self.assertIs(data, product)

    def test_product_has_actual_photos_still_works(self) -> None:
        """Backward-compat: the older boolean helper is still accurate."""
        client = self._make_client()
        all_filled = {
            "values": {
                attr: [{"scope": None, "data": f"{attr}.jpg"}]
                for attr in ("Actual_Photo", "another_picture_5", "another_picture_6")
            }
        }
        one_filled = {
            "values": {
                "Actual_Photo": [{"scope": None, "data": "x.jpg"}],
            }
        }
        none_filled = {"values": {}}
        attrs = ["Actual_Photo", "another_picture_5", "another_picture_6"]
        self.assertTrue(client.product_has_actual_photos(all_filled, attrs))
        self.assertTrue(client.product_has_actual_photos(one_filled, attrs))
        self.assertFalse(client.product_has_actual_photos(none_filled, attrs))


if __name__ == "__main__":
    unittest.main()
