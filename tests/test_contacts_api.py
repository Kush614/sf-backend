import pytest
from sqlalchemy import func, select

from app.database import SessionLocal
from app.models import Address
from app.schemas import MAX_ADDRESSES, MAX_PHOTO_BYTES

BASE = "/api/v1/contacts"


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "sqlite"


def test_create_contact(client, payload):
    response = client.post(BASE, json=payload)
    assert response.status_code == 201
    body = response.json()
    assert body["id"] > 0
    assert body["email"] == "ada@example.com"
    assert body["full_name"] == "Ada Lovelace"
    assert body["created_at"] and body["updated_at"]


def test_create_requires_valid_email(client, payload):
    response = client.post(BASE, json={**payload, "email": "not-an-email"})
    assert response.status_code == 422


def test_create_requires_names(client, payload):
    response = client.post(BASE, json={**payload, "first_name": ""})
    assert response.status_code == 422


def test_duplicate_email_conflicts(client, payload):
    assert client.post(BASE, json=payload).status_code == 201
    response = client.post(BASE, json={**payload, "email": "ADA@example.com"})
    assert response.status_code == 409


def test_get_contact(client, payload):
    contact_id = client.post(BASE, json=payload).json()["id"]
    response = client.get(f"{BASE}/{contact_id}")
    assert response.status_code == 200
    assert response.json()["id"] == contact_id


def test_get_missing_contact_returns_404(client):
    assert client.get(f"{BASE}/9999").status_code == 404


def test_list_pagination_and_total(client, payload):
    for index in range(5):
        client.post(BASE, json={**payload, "email": f"user{index}@example.com"})

    response = client.get(BASE, params={"limit": 2, "offset": 2})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 5
    assert len(body["items"]) == 2
    assert body["limit"] == 2 and body["offset"] == 2


def test_list_search(client, payload):
    client.post(BASE, json=payload)
    client.post(
        BASE,
        json={**payload, "first_name": "Grace", "last_name": "Hopper", "email": "grace@example.com", "company": "US Navy"},
    )

    hits = client.get(BASE, params={"search": "hopper"}).json()
    assert hits["total"] == 1
    assert hits["items"][0]["last_name"] == "Hopper"

    by_company = client.get(BASE, params={"search": "navy"}).json()
    assert by_company["total"] == 1

    misses = client.get(BASE, params={"search": "nobody"}).json()
    assert misses["total"] == 0


def test_list_sorting(client, payload):
    client.post(BASE, json={**payload, "last_name": "Zhang", "email": "z@example.com"})
    client.post(BASE, json={**payload, "last_name": "Adams", "email": "a@example.com"})

    names = [
        item["last_name"]
        for item in client.get(BASE, params={"sort_by": "last_name", "order": "asc"}).json()["items"]
    ]
    assert names == ["Adams", "Zhang"]


def test_list_rejects_bad_sort_field(client):
    assert client.get(BASE, params={"sort_by": "; DROP TABLE contacts"}).status_code == 422


def test_patch_updates_only_sent_fields(client, payload):
    contact_id = client.post(BASE, json=payload).json()["id"]
    response = client.patch(f"{BASE}/{contact_id}", json={"phone": "+1-000-000-0000"})
    assert response.status_code == 200
    body = response.json()
    assert body["phone"] == "+1-000-000-0000"
    assert body["first_name"] == "Ada"
    assert body["company"] == "Analytical Engines"


def test_patch_duplicate_email_conflicts(client, payload):
    first = client.post(BASE, json=payload).json()["id"]
    client.post(BASE, json={**payload, "email": "grace@example.com"})
    response = client.patch(f"{BASE}/{first}", json={"email": "grace@example.com"})
    assert response.status_code == 409


def test_patch_same_email_is_allowed(client, payload):
    contact_id = client.post(BASE, json=payload).json()["id"]
    response = client.patch(f"{BASE}/{contact_id}", json={"email": payload["email"]})
    assert response.status_code == 200


def test_put_replaces_contact(client, payload):
    contact_id = client.post(BASE, json=payload).json()["id"]
    response = client.put(
        f"{BASE}/{contact_id}",
        json={"first_name": "Grace", "last_name": "Hopper", "email": "grace@example.com"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["full_name"] == "Grace Hopper"
    assert body["company"] is None  # omitted fields are cleared by PUT


def test_put_missing_contact_returns_404(client):
    response = client.put(
        f"{BASE}/9999",
        json={"first_name": "A", "last_name": "B", "email": "ab@example.com"},
    )
    assert response.status_code == 404


def test_delete_contact(client, payload):
    contact_id = client.post(BASE, json=payload).json()["id"]
    assert client.delete(f"{BASE}/{contact_id}").status_code == 204
    assert client.get(f"{BASE}/{contact_id}").status_code == 404
    assert client.delete(f"{BASE}/{contact_id}").status_code == 404


def test_root_lists_entrypoints(client):
    body = client.get("/").json()
    assert body["contacts"] == BASE


TINY_PNG = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


def test_create_contact_with_photo(client, payload):
    response = client.post(BASE, json={**payload, "photo": TINY_PNG})
    assert response.status_code == 201
    assert response.json()["photo"] == TINY_PNG


def test_photo_defaults_to_null(client, payload):
    assert client.post(BASE, json=payload).json()["photo"] is None


def test_blank_photo_is_stored_as_null(client, payload):
    response = client.post(BASE, json={**payload, "photo": "   "})
    assert response.status_code == 201
    assert response.json()["photo"] is None


@pytest.mark.parametrize(
    "photo",
    [
        "https://example.com/ada.png",  # not a data URL
        "data:text/html;base64,PHNjcmlwdD4=",  # not an image
        "data:image/png;base64,not valid base64!",
        "data:image/png;base64,",  # decodes to nothing
    ],
)
def test_photo_rejects_bad_values(client, payload, photo):
    assert client.post(BASE, json={**payload, "photo": photo}).status_code == 422


def test_photo_rejects_oversized_image(client, payload):
    oversized = "data:image/png;base64," + "A" * (4 * (MAX_PHOTO_BYTES // 3) + 8)
    assert client.post(BASE, json={**payload, "photo": oversized}).status_code == 422


def test_patch_can_set_and_clear_photo(client, payload):
    contact_id = client.post(BASE, json=payload).json()["id"]

    assert client.patch(f"{BASE}/{contact_id}", json={"photo": TINY_PNG}).json()["photo"] == TINY_PNG
    assert client.patch(f"{BASE}/{contact_id}", json={"photo": None}).json()["photo"] is None


def test_put_without_photo_clears_it(client, payload):
    """PUT is a full replacement, so an omitted photo really is removed."""
    contact_id = client.post(BASE, json={**payload, "photo": TINY_PNG}).json()["id"]

    response = client.put(f"{BASE}/{contact_id}", json=payload)
    assert response.status_code == 200
    assert response.json()["photo"] is None


# --------------------------------------------------------------------------
# Addresses — a contact has many, each with a type
# --------------------------------------------------------------------------


def test_create_contact_with_addresses(client, payload):
    body = client.post(BASE, json=payload).json()

    assert [address["type"] for address in body["addresses"]] == ["work", "home"]
    work = body["addresses"][0]
    assert work["street"] == "1 Market St, Suite 400"
    assert work["postal_code"] == "94105"
    assert work["id"] > 0


def test_addresses_default_to_empty(client, payload):
    body = client.post(BASE, json={**payload, "addresses": []}).json()
    assert body["addresses"] == []

    omitted = client.post(
        BASE, json={"first_name": "Grace", "last_name": "Hopper", "email": "grace@example.com"}
    ).json()
    assert omitted["addresses"] == []


def test_contact_can_hold_several_addresses_of_the_same_type(client, payload):
    """The relationship is one-to-many, not one-per-type."""
    addresses = [
        {"type": "other", "city": "Reykjavik"},
        {"type": "other", "city": "Oslo"},
    ]
    body = client.post(BASE, json={**payload, "addresses": addresses}).json()

    assert [address["city"] for address in body["addresses"]] == ["Reykjavik", "Oslo"]


def test_address_rejects_an_unknown_type(client, payload):
    response = client.post(BASE, json={**payload, "addresses": [{"type": "holiday"}]})
    assert response.status_code == 422


def test_address_type_defaults_to_home(client, payload):
    body = client.post(BASE, json={**payload, "addresses": [{"city": "Oslo"}]}).json()
    assert body["addresses"][0]["type"] == "home"


def test_too_many_addresses_are_rejected(client, payload):
    addresses = [{"type": "other", "city": f"City {index}"} for index in range(MAX_ADDRESSES + 1)]
    response = client.post(BASE, json={**payload, "addresses": addresses})
    assert response.status_code == 422


def test_addresses_survive_a_round_trip(client, payload):
    contact_id = client.post(BASE, json=payload).json()["id"]

    fetched = client.get(f"{BASE}/{contact_id}").json()
    assert len(fetched["addresses"]) == 2

    listed = client.get(BASE).json()["items"][0]
    assert listed["addresses"] == fetched["addresses"]


def test_put_replaces_the_whole_address_list(client, payload):
    contact_id = client.post(BASE, json=payload).json()["id"]

    body = client.put(
        f"{BASE}/{contact_id}",
        json={**payload, "addresses": [{"type": "work", "city": "Cambridge"}]},
    ).json()

    assert len(body["addresses"]) == 1
    assert body["addresses"][0]["city"] == "Cambridge"


def test_put_without_addresses_clears_them(client, payload):
    contact_id = client.post(BASE, json=payload).json()["id"]

    body = client.put(
        f"{BASE}/{contact_id}",
        json={"first_name": "Ada", "last_name": "Lovelace", "email": "ada@example.com"},
    ).json()

    assert body["addresses"] == []


def test_patch_leaves_addresses_alone_unless_sent(client, payload):
    contact_id = client.post(BASE, json=payload).json()["id"]

    untouched = client.patch(f"{BASE}/{contact_id}", json={"company": "Analytical Engines Ltd"}).json()
    assert len(untouched["addresses"]) == 2

    replaced = client.patch(f"{BASE}/{contact_id}", json={"addresses": [{"type": "home"}]}).json()
    assert len(replaced["addresses"]) == 1

    cleared = client.patch(f"{BASE}/{contact_id}", json={"addresses": []}).json()
    assert cleared["addresses"] == []


def test_deleting_a_contact_deletes_its_addresses(client, payload):
    contact_id = client.post(BASE, json=payload).json()["id"]
    assert client.delete(f"{BASE}/{contact_id}").status_code == 204

    with SessionLocal() as db:
        remaining = db.execute(select(func.count()).select_from(Address)).scalar_one()
    assert remaining == 0


def test_replacing_addresses_deletes_the_old_rows(client, payload):
    """delete-orphan, not a growing pile of detached rows."""
    contact_id = client.post(BASE, json=payload).json()["id"]

    client.put(f"{BASE}/{contact_id}", json={**payload, "addresses": [{"type": "home"}]})

    with SessionLocal() as db:
        remaining = db.execute(select(func.count()).select_from(Address)).scalar_one()
    assert remaining == 1


def test_patch_with_explicit_null_clears_addresses(client, payload):
    """`null` clears, like every other field — not "silently do nothing"."""
    contact_id = client.post(BASE, json=payload).json()["id"]

    body = client.patch(f"{BASE}/{contact_id}", json={"addresses": None}).json()

    assert body["addresses"] == []
    with SessionLocal() as db:
        assert db.execute(select(func.count()).select_from(Address)).scalar_one() == 0


def test_reads_do_not_re_decode_stored_photos(client, payload, monkeypatch):
    """
    A stored photo was validated when it was written. Re-validating on the way
    out would base64-decode every photo on every response — up to 200 of them
    for one page of contacts.
    """
    import base64 as base64_module

    client.post(BASE, json={**payload, "photo": TINY_PNG})

    decodes: list[int] = []
    real_b64decode = base64_module.b64decode

    def counting_b64decode(*args, **kwargs):
        decodes.append(1)
        return real_b64decode(*args, **kwargs)

    monkeypatch.setattr(base64_module, "b64decode", counting_b64decode)

    assert client.get(BASE).json()["items"][0]["photo"] == TINY_PNG
    assert client.get(f"{BASE}/1").json()["photo"] == TINY_PNG
    assert decodes == []


def test_writes_still_validate_the_photo(client, payload, monkeypatch):
    """The other half of the same change: the write path must still decode."""
    import base64 as base64_module

    decodes: list[int] = []
    real_b64decode = base64_module.b64decode

    def counting_b64decode(*args, **kwargs):
        decodes.append(1)
        return real_b64decode(*args, **kwargs)

    monkeypatch.setattr(base64_module, "b64decode", counting_b64decode)

    assert client.post(BASE, json={**payload, "photo": TINY_PNG}).status_code == 201
    assert decodes
    assert client.post(
        BASE, json={**payload, "email": "x@example.com", "photo": "data:image/png;base64,!!"}
    ).status_code == 422


def test_address_only_patch_bumps_updated_at(client, payload):
    """
    `updated_at` is a column-level `onupdate`, which does not fire when only
    child rows change — so an address-only write would report a stale time.
    """
    created = client.post(BASE, json=payload).json()

    patched = client.patch(f"{BASE}/{created['id']}", json={"addresses": [{"type": "home"}]}).json()

    assert patched["updated_at"] > created["updated_at"]


def test_clearing_addresses_bumps_updated_at(client, payload):
    created = client.post(BASE, json=payload).json()

    cleared = client.patch(f"{BASE}/{created['id']}", json={"addresses": []}).json()

    assert cleared["updated_at"] > created["updated_at"]


def test_put_changing_only_addresses_bumps_updated_at(client, payload):
    """Same gap on PUT: every scalar is resent unchanged, so nothing dirties the row."""
    created = client.post(BASE, json=payload).json()

    replaced = client.put(
        f"{BASE}/{created['id']}",
        json={**payload, "addresses": [{"type": "other", "city": "Oslo"}]},
    ).json()

    assert replaced["updated_at"] > created["updated_at"]
