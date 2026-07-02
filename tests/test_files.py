"""Original-Datei-Archiv (§5): Upload speichert Binär 1:1 zur Quelle,
Download liefert Bytes zurück, Größenlimit greift, Metadaten sichtbar."""

from weltmodell.files import MAX_UPLOAD_BYTES


def test_upload_stores_and_downloads_original(client):
    content = b"%PDF-1.4\nfake pdf content\n"
    r = client.post(
        "/api/sources/upload",
        files={"file": ("dossier.pdf", content, "application/pdf")},
        data={"activity": "upload:test", "url": "https://example.org/dossier.pdf"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    sid = body["source"]["id"]
    assert body["file"]["filename"] == "dossier.pdf"
    assert body["file"]["size_bytes"] == len(content)
    assert body["file"]["mime"] == "application/pdf"
    assert len(body["file"]["sha256"]) == 64

    # Download-Roundtrip: exakt dieselben Bytes zurück
    d = client.get(f"/api/sources/{sid}/file")
    assert d.status_code == 200
    assert d.content == content
    assert d.headers["content-type"].startswith("application/pdf")
    assert "dossier.pdf" in d.headers["content-disposition"]


def test_detail_and_list_expose_file_metadata(client):
    content = b"col_a,col_b\n1,2\n"
    r = client.post(
        "/api/sources/upload",
        files={"file": ("daten.csv", content, "text/csv")},
        data={"activity": "upload:meta"},
    )
    sid = r.json()["source"]["id"]

    detail = client.get(f"/api/sources/{sid}").json()
    assert detail["file"]["filename"] == "daten.csv"
    assert detail["file"]["size_bytes"] == len(content)

    items = client.get("/api/sources").json()["items"]
    hit = next(i for i in items if i["id"] == sid)
    assert hit["file_name"] == "daten.csv"
    assert hit["file_size"] == len(content)


def test_upload_rejects_oversize(client):
    big = b"x" * (MAX_UPLOAD_BYTES + 1)
    r = client.post(
        "/api/sources/upload",
        files={"file": ("gross.bin", big, "application/octet-stream")},
        data={"activity": "upload:test"},
    )
    assert r.status_code == 422


def test_download_missing_file_is_404(client):
    # Quelle aus JSON-Ingest hat keine Datei → Download 404
    src = client.post(
        "/api/sources", json={"activity": "json:only", "agent": "pytest"}
    ).json()
    r = client.get(f"/api/sources/{src['id']}/file")
    assert r.status_code == 404


def test_source_without_file_has_null_meta(client):
    src = client.post(
        "/api/sources", json={"activity": "json:only2", "agent": "pytest"}
    ).json()
    detail = client.get(f"/api/sources/{src['id']}").json()
    assert detail["file"] is None
