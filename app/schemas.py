import base64
import binascii
import re
from datetime import datetime, timezone
from typing import Annotated

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    EmailStr,
    Field,
    computed_field,
    field_validator,
)

from app.models import AddressType

# ---------------------------------------------------------------------------
# Contact photo
# ---------------------------------------------------------------------------
# Photos are stored inline as base64 data URLs rather than in an object store,
# because the default database is in-process and the service has no filesystem
# to hand out URLs for. That makes the size cap load-bearing: it is the only
# thing keeping a row — and every list response containing it — bounded.

MAX_PHOTO_BYTES = 2 * 1024 * 1024
ALLOWED_PHOTO_MEDIA_TYPES = ("image/gif", "image/jpeg", "image/png", "image/webp")

# base64 encodes 3 bytes as 4 characters, plus the longest allowed prefix.
_MAX_PHOTO_URL_CHARS = -(-MAX_PHOTO_BYTES // 3) * 4 + len("data:image/jpeg;base64,")

# Two flat character classes and one optional suffix — linear, so a multi-megabyte
# candidate cannot make this backtrack.
_PHOTO_DATA_URL = re.compile(r"^data:([\w.+-]+/[\w.+-]+);base64,([A-Za-z0-9+/]*={0,2})$")


def _validate_photo(value: object) -> object:
    """Accept a well-formed, size-capped image data URL; treat blank as absent."""
    if not isinstance(value, str):
        return value

    photo = value.strip()
    if not photo:
        return None

    # Length is checked before the regex so an oversized payload is rejected
    # without scanning it.
    if len(photo) > _MAX_PHOTO_URL_CHARS:
        raise ValueError(f"photo must decode to {MAX_PHOTO_BYTES // (1024 * 1024)} MB or less")

    match = _PHOTO_DATA_URL.match(photo)
    if match is None:
        raise ValueError("photo must be a base64 data URL, e.g. 'data:image/png;base64,iVBORw0...'")

    media_type, payload = match.group(1).lower(), match.group(2)
    if media_type not in ALLOWED_PHOTO_MEDIA_TYPES:
        raise ValueError(f"photo must be one of: {', '.join(ALLOWED_PHOTO_MEDIA_TYPES)}")

    try:
        decoded = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("photo is not valid base64") from exc

    if not decoded:
        raise ValueError("photo must not be empty")
    if len(decoded) > MAX_PHOTO_BYTES:
        raise ValueError(f"photo must decode to {MAX_PHOTO_BYTES // (1024 * 1024)} MB or less")

    return photo


_TINY_PNG = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)

PhotoDataUrl = Annotated[str | None, BeforeValidator(_validate_photo)]


MAX_ADDRESSES = 10


class AddressBase(BaseModel):
    """The parts of a postal address, plus what the address is for."""

    type: AddressType = Field(
        default=AddressType.home,
        description="What this address is for. One of: home, work, other.",
        examples=[AddressType.work],
    )
    street: str | None = Field(
        default=None,
        max_length=300,
        description="Street address, including unit or suite.",
        examples=["1 Market St, Suite 400"],
    )
    city: str | None = Field(default=None, max_length=120, description="City or locality.", examples=["San Francisco"])
    state: str | None = Field(
        default=None,
        max_length=120,
        description="State, province, or region.",
        examples=["CA"],
    )
    postal_code: str | None = Field(
        default=None,
        max_length=20,
        description="Postal or ZIP code.",
        examples=["94105"],
    )
    country: str | None = Field(default=None, max_length=120, description="Country name.", examples=["USA"])


class AddressCreate(AddressBase):
    """One address in a contact's `addresses` list."""


class AddressRead(AddressBase):
    """A stored address. `id` is stable for as long as the address survives a write."""

    model_config = ConfigDict(from_attributes=True)

    id: int = Field(description="Server-assigned identifier.", examples=[1])


class ContactBase(BaseModel):
    """Fields shared by every contact request and response."""

    first_name: str = Field(
        min_length=1,
        max_length=100,
        description="Given name. Required, must not be blank.",
        examples=["Ada"],
    )
    last_name: str = Field(
        min_length=1,
        max_length=100,
        description="Family name. Required, must not be blank.",
        examples=["Lovelace"],
    )
    email: EmailStr = Field(
        max_length=320,
        description=(
            "Primary email address. Required and unique across all contacts; "
            "compared case-insensitively and stored lowercased."
        ),
        examples=["ada@example.com"],
    )
    phone: str | None = Field(
        default=None,
        max_length=40,
        description="Phone number. Stored verbatim — any format is accepted.",
        examples=["+1-415-555-0101"],
    )
    company: str | None = Field(
        default=None,
        max_length=200,
        description="Employer or organisation name.",
        examples=["Analytical Engines"],
    )
    job_title: str | None = Field(
        default=None,
        max_length=200,
        description="Role held at the company.",
        examples=["Mathematician"],
    )
    notes: str | None = Field(
        default=None,
        description="Free-form notes about the contact. No length limit.",
        examples=["Met at the SF hackathon."],
    )


_FULL_EXAMPLE = {
    "first_name": "Ada",
    "last_name": "Lovelace",
    "email": "ada@example.com",
    "phone": "+1-415-555-0101",
    "company": "Analytical Engines",
    "job_title": "Mathematician",
    "addresses": [
        {
            "type": "work",
            "street": "1 Market St, Suite 400",
            "city": "San Francisco",
            "state": "CA",
            "postal_code": "94105",
            "country": "USA",
        },
        {"type": "home", "city": "London", "country": "UK"},
    ],
    "notes": "Met at the SF hackathon.",
    "photo": _TINY_PNG,
}
_MINIMAL_EXAMPLE = {"first_name": "Grace", "last_name": "Hopper", "email": "grace@example.com"}


class ContactWrite(ContactBase):
    """Shared by the two bodies that accept a whole contact (`POST` and `PUT`)."""

    photo: PhotoDataUrl = Field(
        default=None,
        description=(
            "Profile photo as a base64 data URL. Must be one of "
            f"{', '.join(ALLOWED_PHOTO_MEDIA_TYPES)} and decode to at most "
            f"{MAX_PHOTO_BYTES // (1024 * 1024)} MB. Blank is stored as `null`, "
            "and a contact without one falls back to their initials."
        ),
        examples=[_TINY_PNG],
    )

    addresses: list[AddressCreate] = Field(
        default_factory=list,
        max_length=MAX_ADDRESSES,
        description=(
            "The contact's addresses, in display order. Sent as a whole list: "
            "whatever you send replaces what is stored. Omit or send `[]` for none."
        ),
    )


class ContactCreate(ContactWrite):
    """Body of `POST /api/v1/contacts`. Only the two names and email are required."""

    model_config = ConfigDict(json_schema_extra={"examples": [_FULL_EXAMPLE, _MINIMAL_EXAMPLE]})


class ContactReplace(ContactWrite):
    """
    Body of `PUT /api/v1/contacts/{contact_id}`.

    This is a full replacement: any optional field you omit is set back to `null`,
    and an omitted `addresses` list clears every address. Use `PATCH` if you only
    want to change some fields.
    """

    model_config = ConfigDict(json_schema_extra={"examples": [_FULL_EXAMPLE]})


class ContactUpdate(BaseModel):
    """
    Body of `PATCH /api/v1/contacts/{contact_id}`.

    Every field is optional. Only the fields actually present in the request are
    written; omitted fields keep their current value. Sending an explicit `null`
    clears that field.
    """

    model_config = ConfigDict(
        json_schema_extra={"examples": [{"phone": "+1-415-555-0199", "job_title": "Chief Engineer"}]}
    )

    first_name: str | None = Field(default=None, min_length=1, max_length=100, description="New given name.")
    last_name: str | None = Field(default=None, min_length=1, max_length=100, description="New family name.")
    email: EmailStr | None = Field(
        default=None,
        max_length=320,
        description="New email address. Must not belong to another contact.",
    )
    phone: str | None = Field(default=None, max_length=40, description="New phone number.")
    company: str | None = Field(default=None, max_length=200, description="New company.")
    job_title: str | None = Field(default=None, max_length=200, description="New job title.")
    addresses: list[AddressCreate] | None = Field(
        default=None,
        max_length=MAX_ADDRESSES,
        description=(
            "Replacement list of addresses. Addresses have no partial update: "
            "sending this key replaces the whole set, and `[]` or `null` clears "
            "it. Omit the key to leave the existing addresses alone."
        ),
    )
    notes: str | None = Field(default=None, description="New notes; replaces the existing text.")
    photo: PhotoDataUrl = Field(default=None, description="New profile photo as a base64 data URL.")


class ContactRead(ContactBase):
    """A stored contact, as returned by every contact endpoint."""

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "examples": [
                {
                    **_FULL_EXAMPLE,
                    "id": 1,
                    "addresses": [
                        {"id": 1, **address} for address in _FULL_EXAMPLE["addresses"]
                    ],
                    "full_name": "Ada Lovelace",
                    "created_at": "2026-08-19T16:22:58.189507Z",
                    "updated_at": "2026-08-19T16:22:58.189511Z",
                }
            ]
        },
    )

    id: int = Field(description="Server-assigned identifier.", examples=[1])
    # Deliberately not `PhotoDataUrl`: the stored value was validated when it was
    # written, and re-running the validator here would base64-decode every photo
    # again on every response — up to 200 of them for one page of contacts.
    photo: str | None = Field(
        default=None,
        description="Profile photo as a base64 data URL, or `null` to fall back to initials.",
        examples=[_TINY_PNG],
    )
    addresses: list[AddressRead] = Field(
        default_factory=list,
        description="Every address on file for this contact, in display order.",
    )
    created_at: datetime = Field(
        description="UTC timestamp of when the contact was created.",
        examples=["2026-08-19T16:22:58.189507Z"],
    )
    updated_at: datetime = Field(
        description="UTC timestamp of the last modification.",
        examples=["2026-08-19T16:22:58.189511Z"],
    )

    @field_validator("created_at", "updated_at")
    @classmethod
    def _as_utc(cls, value: datetime) -> datetime:
        # SQLite discards tzinfo on write; the stored values are UTC, so label
        # them as such rather than emitting an ambiguous naive timestamp.
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value

    @computed_field(description="Convenience concatenation of first and last name.", examples=["Ada Lovelace"])
    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()


class ContactPage(BaseModel):
    """One page of contacts plus the totals a client needs to paginate."""

    items: list[ContactRead] = Field(description="Contacts on this page, ordered by the requested sort.")
    total: int = Field(
        description="Total contacts matching the query, ignoring `limit` and `offset`.",
        examples=[42],
    )
    limit: int = Field(description="Page size that was applied.", examples=[50])
    offset: int = Field(description="Number of records skipped.", examples=[0])


class HealthResponse(BaseModel):
    """Result of the liveness probe."""

    status: str = Field(description="Always `ok` when the service can serve traffic.", examples=["ok"])
    database: str = Field(description="Active SQLAlchemy dialect.", examples=["sqlite"])
    contacts: int = Field(description="Number of contacts currently stored.", examples=[3])


class RootResponse(BaseModel):
    """Discovery document listing the API's entry points."""

    name: str = Field(description="Human-readable service name.", examples=["Contacts API"])
    version: str = Field(description="Service version.", examples=["0.1.0"])
    docs: str = Field(description="Path to the Swagger UI.", examples=["/docs"])
    redoc: str = Field(description="Path to the ReDoc UI.", examples=["/redoc"])
    openapi: str = Field(description="Path to the OpenAPI 3.1 document.", examples=["/openapi.json"])
    contacts: str = Field(description="Base path of the contacts collection.", examples=["/api/v1/contacts"])
    health: str = Field(description="Path to the liveness probe.", examples=["/health"])


class ErrorResponse(BaseModel):
    """Shape of every non-validation error returned by the API."""

    detail: str = Field(
        description="Human-readable explanation of the failure.",
        examples=["Contact 42 not found"],
    )
