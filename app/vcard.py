"""
Render a contact as a vCard 4.0 document (RFC 6350).

vCard is what every phone and mail client imports, and it already has native
slots for the two things this app models: `PHOTO` takes the base64 data URL
as-is, and `ADR` repeats with a `TYPE` parameter — so a contact's several
addresses survive the export instead of being flattened into one.
"""

import re
import uuid
from collections.abc import Iterable, Iterator

from app.models import Address, Contact

# RFC 6350 §3.2: lines are folded at 75 octets, and a continuation begins with
# one space — which costs it an octet of payload.
_MAX_LINE_OCTETS = 75
_CONTINUATION_OCTETS = _MAX_LINE_OCTETS - 1

# Content-Disposition carries this, so keep it to characters that cannot end a
# quoted string early or start a new header.
_UNSAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")


def _escape(value: str) -> str:
    """Escape a TEXT value. Order matters: backslash first, or it doubles the rest."""
    return (
        value.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
        .replace("\r", "\\n")
    )


def _fold(line: str) -> str:
    """Fold one logical line, never splitting a multi-byte character."""
    if len(line.encode("utf-8")) <= _MAX_LINE_OCTETS:
        return line

    chunks: list[str] = []
    current: list[str] = []
    octets = 0
    limit = _MAX_LINE_OCTETS

    for char in line:
        size = len(char.encode("utf-8"))
        if octets + size > limit:
            chunks.append("".join(current))
            current, octets, limit = [], 0, _CONTINUATION_OCTETS
        current.append(char)
        octets += size

    chunks.append("".join(current))
    return "\r\n ".join(chunks)


def _structured(*components: str | None) -> str:
    """Join the components of a structured value like `N` or `ADR`."""
    return ";".join(_escape(component or "") for component in components)


def _address_line(address: Address) -> str:
    # ADR's seven components: PO box, extended address, street, locality,
    # region, postal code, country. The first two are deliberately left empty —
    # RFC 6350 says they should be.
    value = _structured(
        None,
        None,
        address.street,
        address.city,
        address.state,
        address.postal_code,
        address.country,
    )
    return f"ADR;TYPE={address.type.value}:{value}"


# A fixed namespace plus the contact's id and creation instant. Deterministic,
# so re-exporting the same contact yields the same UID and importers update
# rather than duplicate — while two installations would have to create the same
# id in the same microsecond to collide.
_UID_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_DNS, "sf-backend.contacts")


def _uid(contact: Contact) -> str:
    seed = f"{contact.id}:{contact.created_at.isoformat()}"
    return f"urn:uuid:{uuid.uuid5(_UID_NAMESPACE, seed)}"


def _lines(contact: Contact) -> Iterable[str]:
    yield "BEGIN:VCARD"
    yield "VERSION:4.0"
    yield "PRODID:-//sf-backend//Contacts API//EN"
    yield f"FN:{_escape(contact.full_name)}"
    yield f"N:{_structured(contact.last_name, contact.first_name, None, None, None)}"
    yield f"EMAIL:{_escape(contact.email)}"

    if contact.phone:
        # TEL defaults to the URI value type, and this app stores phone numbers
        # verbatim in whatever shape they were typed — so declare text rather
        # than emit "+1 (415) 555-0101" where a `tel:` URI is expected.
        yield f"TEL;TYPE=voice;VALUE=text:{_escape(contact.phone)}"
    if contact.company:
        yield f"ORG:{_escape(contact.company)}"
    if contact.job_title:
        yield f"TITLE:{_escape(contact.job_title)}"

    for address in contact.addresses:
        yield _address_line(address)

    if contact.photo:
        # PHOTO's value is a URI, not TEXT, so the `;` and `,` inside a data URL
        # are structural and must not be escaped.
        yield f"PHOTO:{contact.photo}"

    if contact.notes:
        yield f"NOTE:{_escape(contact.notes)}"

    yield f"UID:{_uid(contact)}"
    yield f"REV:{contact.updated_at.strftime('%Y%m%dT%H%M%SZ')}"
    yield "END:VCARD"


def to_vcard(contact: Contact) -> str:
    """One contact as a vCard document, CRLF-terminated per the spec."""
    return "".join(f"{_fold(line)}\r\n" for line in _lines(contact))


def iter_vcards(contacts: Iterable[Contact]) -> Iterator[str]:
    """
    Several contacts, concatenated — the standard way to carry an address book.

    Yields one card at a time so the caller can stream them out. Photos are
    inlined and notes have no length limit, so building the whole body as a
    single string would hold the entire export in memory twice.
    """
    for contact in contacts:
        yield to_vcard(contact)


def vcard_filename(contact: Contact) -> str:
    """A safe `name.vcf`, falling back to the id when the name has nothing usable."""
    slug = _UNSAFE_FILENAME.sub("-", contact.full_name).strip("-.")
    return f"{slug or f'contact-{contact.id}'}.vcf"
