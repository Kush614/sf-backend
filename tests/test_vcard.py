"""The vCard export: one contact, the whole book, and the escaping rules."""

BASE = "/api/v1/contacts"

TINY_PNG = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


def unfold(body: str) -> list[str]:
    """Undo RFC 6350 line folding, so assertions can look at logical lines."""
    return body.replace("\r\n ", "").strip().split("\r\n")


def test_vcard_has_the_required_envelope(client, payload):
    contact_id = client.post(BASE, json=payload).json()["id"]

    response = client.get(f"{BASE}/{contact_id}/vcard")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/vcard")
    assert response.headers["content-disposition"] == 'attachment; filename="Ada-Lovelace.vcf"'

    lines = unfold(response.text)
    assert lines[0] == "BEGIN:VCARD"
    assert lines[1] == "VERSION:4.0"
    assert lines[-1] == "END:VCARD"
    assert "FN:Ada Lovelace" in lines
    assert "N:Lovelace;Ada;;;" in lines


def test_vcard_exports_every_address_with_its_type(client, payload):
    """The one-to-many survives the export instead of being flattened."""
    contact_id = client.post(BASE, json=payload).json()["id"]

    lines = unfold(client.get(f"{BASE}/{contact_id}/vcard").text)
    addresses = [line for line in lines if line.startswith("ADR")]

    assert addresses == [
        "ADR;TYPE=work:;;1 Market St\\, Suite 400;San Francisco;CA;94105;USA",
        "ADR;TYPE=home:;;;London;;;UK",
    ]


def test_vcard_carries_the_photo_unescaped(client, payload):
    """PHOTO is a URI, so the `;` and `,` inside a data URL are structural."""
    contact_id = client.post(BASE, json={**payload, "photo": TINY_PNG}).json()["id"]

    lines = unfold(client.get(f"{BASE}/{contact_id}/vcard").text)
    assert f"PHOTO:{TINY_PNG}" in lines


def test_vcard_omits_the_fields_that_are_not_set(client):
    body = client.post(
        BASE, json={"first_name": "Grace", "last_name": "Hopper", "email": "grace@example.com"}
    ).json()

    lines = unfold(client.get(f"{BASE}/{body['id']}/vcard").text)
    assert not [line for line in lines if line.startswith(("TEL", "ORG", "TITLE", "ADR", "PHOTO", "NOTE"))]


def test_vcard_escapes_text_values(client, payload):
    contact_id = client.post(
        BASE, json={**payload, "company": "Babbage, Lovelace; Ltd", "notes": "line one\nline two"}
    ).json()["id"]

    lines = unfold(client.get(f"{BASE}/{contact_id}/vcard").text)
    assert "ORG:Babbage\\, Lovelace\\; Ltd" in lines
    assert "NOTE:line one\\nline two" in lines


def test_vcard_folds_long_lines_at_75_octets(client, payload):
    contact_id = client.post(BASE, json={**payload, "photo": TINY_PNG}).json()["id"]

    raw = client.get(f"{BASE}/{contact_id}/vcard").text
    for line in raw.split("\r\n"):
        assert len(line.encode("utf-8")) <= 75

    # Folding must be reversible, not lossy.
    assert f"PHOTO:{TINY_PNG}" in unfold(raw)


def test_vcard_filename_falls_back_when_the_name_has_nothing_usable(client, payload):
    contact_id = client.post(BASE, json={**payload, "first_name": "厳", "last_name": "格"}).json()["id"]

    disposition = client.get(f"{BASE}/{contact_id}/vcard").headers["content-disposition"]
    assert disposition == f'attachment; filename="contact-{contact_id}.vcf"'


def test_vcard_for_a_missing_contact_returns_404(client):
    assert client.get(f"{BASE}/9999/vcard").status_code == 404


def test_export_concatenates_every_contact(client, payload):
    client.post(BASE, json=payload)
    client.post(BASE, json={**payload, "first_name": "Grace", "email": "grace@example.com"})

    response = client.get(f"{BASE}/vcard")

    assert response.status_code == 200
    assert response.headers["content-disposition"] == 'attachment; filename="contacts.vcf"'
    assert response.text.count("BEGIN:VCARD") == 2
    assert response.text.count("END:VCARD") == 2


def test_export_honours_the_search_filter(client, payload):
    client.post(BASE, json=payload)
    client.post(BASE, json={**payload, "first_name": "Grace", "email": "grace@example.com"})

    response = client.get(f"{BASE}/vcard", params={"search": "grace"})

    assert response.text.count("BEGIN:VCARD") == 1
    assert "FN:Grace Lovelace" in unfold(response.text)


def test_export_path_is_not_swallowed_by_the_id_route(client):
    """`/vcard` must match the literal route, not `/{contact_id}`."""
    response = client.get(f"{BASE}/vcard")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/vcard")
