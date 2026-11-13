# Frontend E2E tests using pytest-playwright.
#
# Setup (one-time):
#   pip install -r tests/requirements-frontend.txt
#   playwright install chromium
#
# Run:
#   pytest tests/frontend/
#
# All API endpoints (/upload, /progress, /files) are intercepted by Playwright
# route mocks so these tests never touch the real pipeline or InfluxDB.

import json
import re
import pytest
from playwright.sync_api import Page, expect


'''
Shared mock payloads
'''

_EMPTY = json.dumps([])

_TWO_FILES = json.dumps([
    {"name": "run1.csv", "timestamp": "2024-01-15 10:30:00", "size": 2048},
    {"name": "run2.csv", "timestamp": "2024-01-14 09:00:00", "size": 512},
])

_UPLOAD_OK = json.dumps({"name": "task-abc123"})

_PROGRESS_DONE = json.dumps({
    "progress": "done",
    "exception": {"present": False, "type": "None"},
})

_PROGRESS_IN_FLIGHT = json.dumps({
    "progress": "binary_to_can",
    "exception": {"present": False, "type": "None"},
})

_PROGRESS_EXCEPTION = json.dumps({
    "progress": "can_to_csv",
    "exception": {"present": True, "type": "ValueError: bad data"},
})


'''
Helpers
'''

def _mock_files_empty(route):
    route.fulfill(status=200, content_type="application/json", body=_EMPTY)


def _goto(page: Page, url: str) -> None:
    """Navigate and wait for JS-driven initial file-list fetches to settle."""
    page.goto(url)


@pytest.fixture
def setup_empty_files_page(page: Page, server_url) -> None:
    """Mock all /files endpoints to return empty lists, then navigate."""
    page.route("**/files**", _mock_files_empty)
    _goto(page, server_url)


'''
Page structure
'''

@pytest.mark.usefixtures("setup_empty_files_page")
class TestPageStructure:
    def test_title(self, page: Page, server_url):
        expect(page).to_have_title("The Pipeline")

    def test_navbar_brand_text(self, page: Page, server_url):
        expect(page.locator(".navbar-brand span")).to_contain_text("The Pipeline")

    def test_navbar_wiki_link(self, page: Page, server_url):
        expect(page.locator("a.nav-link", has_text="The Wiki")).to_be_visible()

    def test_navbar_grafana_link(self, page: Page, server_url):
        expect(page.locator("a.nav-link", has_text="Grafana")).to_be_visible()

    def test_navbar_rerun_viewer_link(self, page: Page, server_url):
        expect(page.locator("a.nav-link", has_text="Rerun Viewer")).to_be_visible()

    def test_navbar_feature_request_link(self, page: Page, server_url):
        expect(page.locator("a.nav-link", has_text="Feature Request")).to_be_visible()

    def test_dropzone_visible(self, page: Page, server_url):
        expect(page.locator("#dropZone")).to_be_visible()

    def test_dropzone_drag_prompt(self, page: Page, server_url):
        expect(page.locator("#dropZone")).to_contain_text("Drag Data Here")

    def test_dropzone_click_hint(self, page: Page, server_url):
        expect(page.locator("#dropZone")).to_contain_text("click to upload")

    def test_file_input_accepts_data_extension(self, page: Page, server_url):
        expect(page.locator("#fileInput")).to_have_attribute(
            "accept", ".data,.bin,.pb,.protobuf,.binpd"
        )

    def test_file_input_allows_multiple(self, page: Page, server_url):
        # `multiple` attribute present means the element accepts multiple files
        input_el = page.locator("#fileInput")
        assert input_el.get_attribute("multiple") is not None

    def test_debug_toggle_visible(self, page: Page, server_url):
        expect(page.locator("#debugToggle")).to_be_visible()

    def test_debug_toggle_label(self, page: Page, server_url):
        expect(page.locator("label[for='debugToggle']")).to_contain_text("Debug Mode")

    def test_debug_toggle_unchecked_by_default(self, page: Page, server_url):
        expect(page.locator("#debugToggle")).not_to_be_checked()

    def test_debug_toggle_stays_with_its_label(self, page: Page, server_url):
        toggle = page.locator("#debugToggle")
        label = page.locator("label[for='debugToggle']")
        assert toggle.evaluate("element => getComputedStyle(element).position") == "static"
        assert abs(toggle.bounding_box()["y"] - label.bounding_box()["y"]) < 10

    def test_protobuf_options_scroll_from_the_top(self, page: Page, server_url):
        page.locator("#inputFormat").select_option("protobuf_delimited")
        main = page.locator("main")
        assert main.evaluate("element => getComputedStyle(element).minHeight") == "0px"
        assert main.evaluate("element => getComputedStyle(element).justifyContent") in {
            "flex-start",
            "flow-start",
        }
        expect(page.locator("label[for='schemaInput']")).to_be_in_viewport()

    def test_schema_input_allows_multiple_proto_files(self, page: Page, server_url):
        schema_input = page.locator("#schemaInput")
        expect(schema_input).to_have_attribute("accept", ".proto")
        assert schema_input.get_attribute("multiple") is not None
        assert schema_input.get_attribute("webkitdirectory") is not None
        expect(page.locator("#protobufOptions")).to_contain_text("override")
        expect(page.locator("#protobufOptions")).to_contain_text("MF26/v3")
        expect(page.locator("#messageType")).to_have_value("MF26.v3.CarFrame")

    def test_protobuf_output_mode_control_is_removed(self, page: Page, server_url):
        expect(page.locator("#protobufOutputMode")).to_have_count(0)
        expect(page.locator("text=Output mode")).to_have_count(0)
        expect(page.locator("text=Raw CAN through DBC")).to_have_count(0)

    def test_csv_file_list_present(self, page: Page, server_url):
        expect(page.locator("#csvfileList")).to_be_visible()

    def test_rerun_file_list_present(self, page: Page, server_url):
        expect(page.locator("#rerunfileList")).to_be_visible()

    def test_raw_file_list_present(self, page: Page, server_url):
        expect(page.locator("#rawfileList")).to_be_visible()

    def test_csv_card_header(self, page: Page, server_url):
        expect(page.locator(".card-header", has_text="CSV Files")).to_be_visible()

    def test_rerun_card_header(self, page: Page, server_url):
        expect(page.locator(".card-header", has_text="Rerun Files")).to_be_visible()

    def test_raw_card_header(self, page: Page, server_url):
        expect(page.locator(".card-header", has_text="Raw Files")).to_be_visible()

    def test_alert_container_starts_empty(self, page: Page, server_url):
        expect(page.locator("#alertContainer")).to_be_empty()


'''
File lists
'''

class TestFileLists:
    @pytest.mark.usefixtures("setup_empty_files_page")
    def test_empty_csv_list_message(self, page: Page, server_url):
        expect(page.locator("#csvfileList")).to_contain_text("No files found")

    @pytest.mark.usefixtures("setup_empty_files_page")
    def test_empty_rerun_list_message(self, page: Page, server_url):
        expect(page.locator("#rerunfileList")).to_contain_text("No files found")

    @pytest.mark.usefixtures("setup_empty_files_page")
    def test_empty_raw_list_message(self, page: Page, server_url):
        expect(page.locator("#rawfileList")).to_contain_text("No files found")

    def test_files_show_names(self, page: Page, server_url):
        page.route("**/files**", lambda r: r.fulfill(
            status=200, content_type="application/json", body=_TWO_FILES
        ))
        _goto(page, server_url)
        expect(page.locator("#csvfileList")).to_contain_text("run1.csv")
        expect(page.locator("#csvfileList")).to_contain_text("run2.csv")

    def test_files_show_timestamps(self, page: Page, server_url):
        page.route("**/files**", lambda r: r.fulfill(
            status=200, content_type="application/json", body=_TWO_FILES
        ))
        _goto(page, server_url)
        expect(page.locator("#csvfileList")).to_contain_text("2024-01-15 10:30:00")

    def test_files_show_size_in_kb(self, page: Page, server_url):
        page.route("**/files**", lambda r: r.fulfill(
            status=200, content_type="application/json", body=_TWO_FILES
        ))
        _goto(page, server_url)
        # run1.csv is 2048 bytes = 2 KB
        expect(page.locator("#csvfileList")).to_contain_text("2 KB")

    def test_files_show_size_in_bytes(self, page: Page, server_url):
        payload = json.dumps([
            {"name": "tiny.csv", "timestamp": "2024-01-01 00:00:00", "size": 500}
        ])
        page.route("**/files**", lambda r: r.fulfill(
            status=200, content_type="application/json", body=payload
        ))
        _goto(page, server_url)
        expect(page.locator("#csvfileList")).to_contain_text("500 B")

    def test_files_show_size_zero(self, page: Page, server_url):
        payload = json.dumps([
            {"name": "empty.csv", "timestamp": "2024-01-01 00:00:00", "size": 0}
        ])
        page.route("**/files**", lambda r: r.fulfill(
            status=200, content_type="application/json", body=payload
        ))
        _goto(page, server_url)
        expect(page.locator("#csvfileList")).to_contain_text("0 B")

    def test_files_show_download_button(self, page: Page, server_url):
        page.route("**/files**", lambda r: r.fulfill(
            status=200, content_type="application/json", body=_TWO_FILES
        ))
        _goto(page, server_url)
        expect(
            page.locator("#csvfileList a.btn", has_text="Download").first
        ).to_be_visible()

    def test_download_button_href_contains_filename(self, page: Page, server_url):
        page.route("**/files**", lambda r: r.fulfill(
            status=200, content_type="application/json", body=_TWO_FILES
        ))
        _goto(page, server_url)
        btn = page.locator("#csvfileList a.btn", has_text="Download").first
        expect(btn).to_have_attribute(
            "href", re.compile(r".*files/download/run1\.csv")
        )

    def test_correct_number_of_list_items(self, page: Page, server_url):
        page.route("**/files**", lambda r: r.fulfill(
            status=200, content_type="application/json", body=_TWO_FILES
        ))
        _goto(page, server_url)
        expect(page.locator("#csvfileList li.list-group-item")).to_have_count(2)

    def test_filename_tooltip(self, page: Page, server_url):
        page.route("**/files**", lambda r: r.fulfill(
            status=200, content_type="application/json", body=_TWO_FILES
        ))
        _goto(page, server_url)
        name_span = page.locator("#csvfileList span.text-truncate").first
        expect(name_span).to_have_attribute("title", "run1.csv")

    def test_fetch_error_csv(self, page: Page, server_url):
        page.route("**/files**", lambda r: r.fulfill(status=500))
        _goto(page, server_url)
        expect(page.locator("#csvfileList")).to_contain_text("Failed to load files")

    def test_fetch_error_rerun(self, page: Page, server_url):
        page.route("**/files**", lambda r: r.fulfill(status=500))
        _goto(page, server_url)
        expect(page.locator("#rerunfileList")).to_contain_text("Failed to load files")

    def test_fetch_error_raw(self, page: Page, server_url):
        page.route("**/files**", lambda r: r.fulfill(status=500))
        _goto(page, server_url)
        expect(page.locator("#rawfileList")).to_contain_text("Failed to load files")


'''
Alert rendering
'''

@pytest.mark.usefixtures("setup_empty_files_page")
class TestAlerts:
    def test_info_alert(self, page: Page, server_url):
        page.evaluate("showAlert('Info message', 'info')")
        expect(page.locator("#alertContainer .alert-info")).to_be_visible()
        expect(page.locator("#alertContainer")).to_contain_text("Info message")

    def test_success_alert(self, page: Page, server_url):
        page.evaluate("showAlert('All done!', 'success')")
        expect(page.locator("#alertContainer .alert-success")).to_be_visible()

    def test_danger_alert(self, page: Page, server_url):
        page.evaluate("showAlert('Something broke', 'danger')")
        expect(page.locator("#alertContainer .alert-danger")).to_be_visible()

    def test_warning_alert(self, page: Page, server_url):
        page.evaluate("showAlert('Watch out', 'warning')")
        expect(page.locator("#alertContainer .alert-warning")).to_be_visible()

    def test_new_alert_replaces_previous(self, page: Page, server_url):
        page.evaluate("showAlert('First', 'info')")
        page.evaluate("showAlert('Second', 'success')")
        expect(page.locator("#alertContainer .alert")).to_have_count(1)
        expect(page.locator("#alertContainer")).to_contain_text("Second")
        expect(page.locator("#alertContainer")).not_to_contain_text("First")

    def test_alert_has_role_attribute(self, page: Page, server_url):
        page.evaluate("showAlert('Test', 'info')")
        expect(page.locator("#alertContainer [role='alert']")).to_be_visible()


'''
Drag and drop
'''

class TestDragDrop:
    @pytest.mark.usefixtures("setup_empty_files_page")
    def test_dragover_adds_class(self, page: Page, server_url):
        page.evaluate("""
            const zone = document.getElementById('dropZone');
            zone.dispatchEvent(new DragEvent('dragover', {bubbles: true, cancelable: true}));
        """)
        classes = page.locator("#dropZone").get_attribute("class")
        assert "dragover" in classes

    @pytest.mark.usefixtures("setup_empty_files_page")
    def test_dragleave_removes_class(self, page: Page, server_url):
        page.evaluate("""
            const zone = document.getElementById('dropZone');
            zone.classList.add('dragover');
            zone.dispatchEvent(new DragEvent('dragleave', {bubbles: true}));
        """)
        classes = page.locator("#dropZone").get_attribute("class")
        assert "dragover" not in classes

    @pytest.mark.usefixtures("setup_empty_files_page")
    def test_dragover_removes_invalid_class(self, page: Page, server_url):
        page.evaluate("""
            const zone = document.getElementById('dropZone');
            zone.classList.add('invalid');
            zone.dispatchEvent(new DragEvent('dragover', {bubbles: true, cancelable: true}));
        """)
        classes = page.locator("#dropZone").get_attribute("class")
        assert "invalid" not in classes

    @pytest.mark.usefixtures("setup_empty_files_page")
    def test_dragleave_removes_invalid_class(self, page: Page, server_url):
        page.evaluate("""
            const zone = document.getElementById('dropZone');
            zone.classList.add('invalid');
            zone.dispatchEvent(new DragEvent('dragleave', {bubbles: true}));
        """)
        classes = page.locator("#dropZone").get_attribute("class")
        assert "invalid" not in classes

    @pytest.mark.usefixtures("setup_empty_files_page")
    def test_drop_empty_transfer_shows_warning(self, page: Page, server_url):
        page.evaluate("""
            const zone = document.getElementById('dropZone');
            zone.dispatchEvent(new DragEvent('drop', {
                bubbles: true,
                cancelable: true,
                dataTransfer: new DataTransfer(),
            }));
        """)
        expect(page.locator("#alertContainer .alert-warning")).to_be_visible()
        expect(page.locator("#alertContainer")).to_contain_text("No files dropped.")

    def test_drop_removes_dragover_class(self, page: Page, server_url):
        page.route("**/files**", _mock_files_empty)
        page.route("**/upload", lambda r: r.fulfill(
            status=200, content_type="application/json", body=_UPLOAD_OK
        ))
        page.route("**/progress**", lambda r: r.fulfill(
            status=200, content_type="application/json", body=_PROGRESS_DONE
        ))
        _goto(page, server_url)
        page.evaluate("""
            const zone = document.getElementById('dropZone');
            zone.classList.add('dragover');
            zone.dispatchEvent(new DragEvent('drop', {
                bubbles: true,
                cancelable: true,
                dataTransfer: new DataTransfer(),
            }));
        """)
        classes = page.locator("#dropZone").get_attribute("class")
        assert "dragover" not in classes

    @pytest.mark.usefixtures("setup_empty_files_page")
    def test_dropzone_click_opens_file_chooser(self, page: Page, server_url):
        with page.expect_file_chooser() as fc_info:
            page.locator("#dropZone").click()
        assert fc_info.value is not None


'''
Upload and progress polling
'''

def _write_upload_file(page: Page, tmp_path) -> None:
    f = tmp_path / "run.data"
    f.write_bytes(b"\x00" * 64)
    page.locator("#fileInput").set_input_files(str(f))


class TestUpload:
    def test_shows_uploading_alert(self, page: Page, server_url, tmp_path):
        page.route("**/files**", _mock_files_empty)
        page.route("**/upload", lambda r: r.fulfill(
            status=200, content_type="application/json", body=_UPLOAD_OK
        ))
        page.route("**/progress**", lambda r: r.fulfill(
            status=200, content_type="application/json", body=_PROGRESS_DONE
        ))
        _goto(page, server_url)

        _write_upload_file(page, tmp_path)

        # "Uploading…" is shown synchronously before fetch resolves
        expect(page.locator("#alertContainer")).to_contain_text("Uploading")

    def test_upload_server_error_shows_danger_alert(self, page: Page, server_url, tmp_path):
        page.route("**/files**", _mock_files_empty)
        page.route("**/upload", lambda r: r.fulfill(
            status=500, content_type="application/json",
            body=json.dumps({"error": "Internal server error"}),
        ))
        _goto(page, server_url)

        _write_upload_file(page, tmp_path)

        expect(page.locator("#alertContainer .alert-danger")).to_be_visible(timeout=5000)

    def test_missing_task_name_shows_danger_alert(self, page: Page, server_url, tmp_path):
        page.route("**/files**", _mock_files_empty)
        page.route("**/upload", lambda r: r.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({}),  # no "name" key
        ))
        _goto(page, server_url)

        _write_upload_file(page, tmp_path)

        expect(page.locator("#alertContainer .alert-danger")).to_be_visible(timeout=5000)
        expect(page.locator("#alertContainer")).to_contain_text("Server did not return a task name.")

    def test_progress_polling_initial_display(self, page: Page, server_url, tmp_path):
        page.route("**/files**", _mock_files_empty)
        page.route("**/upload", lambda r: r.fulfill(
            status=200, content_type="application/json", body=_UPLOAD_OK
        ))
        page.route("**/progress**", lambda r: r.fulfill(
            status=202, content_type="application/json", body=_PROGRESS_IN_FLIGHT
        ))
        _goto(page, server_url)

        _write_upload_file(page, tmp_path)

        # startPolling calls updateProgressDisplay(0) synchronously before the first poll
        expect(page.locator("#alertContainer")).to_contain_text("Processing: 0", timeout=3000)

    def test_progress_complete_shows_success(self, page: Page, server_url, tmp_path):
        page.route("**/files**", _mock_files_empty)
        page.route("**/upload", lambda r: r.fulfill(
            status=200, content_type="application/json", body=_UPLOAD_OK
        ))
        page.route("**/progress**", lambda r: r.fulfill(
            status=200, content_type="application/json", body=_PROGRESS_DONE
        ))
        _goto(page, server_url)

        _write_upload_file(page, tmp_path)

        # Poll fires after POLL_INTERVAL (2 s); allow up to 5 s
        expect(page.locator("#alertContainer .alert-success")).to_be_visible(timeout=5000)
        expect(page.locator("#alertContainer")).to_contain_text("Upload and processing complete!")

    def test_progress_exception_shows_danger_alert(self, page: Page, server_url, tmp_path):
        page.route("**/files**", _mock_files_empty)
        page.route("**/upload", lambda r: r.fulfill(
            status=200, content_type="application/json", body=_UPLOAD_OK
        ))
        page.route("**/progress**", lambda r: r.fulfill(
            status=202, content_type="application/json", body=_PROGRESS_EXCEPTION
        ))
        _goto(page, server_url)

        _write_upload_file(page, tmp_path)

        expect(page.locator("#alertContainer .alert-danger")).to_be_visible(timeout=5000)
        expect(page.locator("#alertContainer")).to_contain_text("ValueError: bad data")

    def test_progress_404_shows_danger_alert(self, page: Page, server_url, tmp_path):
        page.route("**/files**", _mock_files_empty)
        page.route("**/upload", lambda r: r.fulfill(
            status=200, content_type="application/json", body=_UPLOAD_OK
        ))
        page.route("**/progress**", lambda r: r.fulfill(
            status=404, content_type="application/json",
            body=json.dumps({"message": "Unknown task name."}),
        ))
        _goto(page, server_url)

        _write_upload_file(page, tmp_path)

        expect(page.locator("#alertContainer .alert-danger")).to_be_visible(timeout=5000)
        # The catch block in startPolling always shows this generic message regardless of the 404
        expect(page.locator("#alertContainer")).to_contain_text("Error while checking progress.")

    def test_success_reloads_file_lists(self, page: Page, server_url, tmp_path):
        """After a successful upload, loadAllFiles() is called again."""
        reload_count = {"n": 0}

        def count_files_requests(route):
            reload_count["n"] += 1
            route.fulfill(status=200, content_type="application/json", body=_EMPTY)

        page.route("**/files**", count_files_requests)
        page.route("**/upload", lambda r: r.fulfill(
            status=200, content_type="application/json", body=_UPLOAD_OK
        ))
        page.route("**/progress**", lambda r: r.fulfill(
            status=200, content_type="application/json", body=_PROGRESS_DONE
        ))
        _goto(page, server_url)

        initial_count = reload_count["n"]  # 3 from page load (csv, rerun, raw)

        _write_upload_file(page, tmp_path)

        # Wait for the success alert, which means loadAllFiles() was called
        expect(page.locator("#alertContainer .alert-success")).to_be_visible(timeout=5000)
        assert reload_count["n"] > initial_count

    @pytest.mark.usefixtures("setup_empty_files_page")
    def test_debug_toggle_can_be_checked_and_unchecked(self, page: Page, server_url):
        toggle = page.locator("#debugToggle")
        toggle.check()
        expect(toggle).to_be_checked()
        toggle.uncheck()
        expect(toggle).not_to_be_checked()

    def test_upload_clears_previous_alert(self, page: Page, server_url, tmp_path):
        page.route("**/files**", _mock_files_empty)
        page.route("**/upload", lambda r: r.fulfill(
            status=200, content_type="application/json", body=_UPLOAD_OK
        ))
        page.route("**/progress**", lambda r: r.fulfill(
            status=200, content_type="application/json", body=_PROGRESS_DONE
        ))
        _goto(page, server_url)

        page.evaluate("showAlert('Old alert', 'warning')")

        _write_upload_file(page, tmp_path)

        expect(page.locator("#alertContainer")).not_to_contain_text("Old alert")
